/**
 * AIoT Predictive Safety System - Browser Audio Alert Engine (Web Audio API)
 *
 * Software-only audible anomaly channel for the cockpit. Synthesizes tones with
 * an AudioContext (no audio files, no npm packages, no extra Python deps) and
 * decides WHEN to play them through an isolated incident tracker.
 *
 * Data flow (discovered from dashboard/app.py):
 *   Flask SSE /api/stream --(event: prediction|alert)--> handlePrediction/handleAlert
 *   tracker (dedup + escalation) --(alert decision)--> playPattern("warn"|"crit")
 *   snapshot --(seed)--> silent state sync so a page reload never replays old alerts
 *
 * Browser: window.AudioAlerts (singleton). Node tests: require() the factory API.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root && root.document && !root.AudioAlerts) {
    root.AudioAlerts = api.createEngine();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // -------------------------------------------------------------------------
  // Configuration (tweak lab comfort here; defaults stay conservative)
  // -------------------------------------------------------------------------
  const AUDIO_CONFIG = {
    masterVolume: 0.35,
    storageKey: "aiot.soundAlerts",
    // Hard bound so a repeated pattern can never become an endless alarm.
    maxAlertMs: 4000,
    maxRepeats: 8,
    // Flap guard: minimum spacing between two *different* warning incidents
    // that occur while another warning is still active (class oscillation).
    warnFlapGuardMs: 3000,
    patterns: {
      warn: { type: "triangle", notes: [880, 660], noteMs: 170, gapMs: 130, repeat: 2 },
      crit: { type: "square", notes: [1046, 784], noteMs: 130, gapMs: 90, repeat: 4 },
      test: { type: "sine", notes: [740], noteMs: 200, gapMs: 0, repeat: 1 },
    },
  };

  const SEVERITY = { NORMAL: 0, WARN: 1, CRITICAL: 2 };

  // -------------------------------------------------------------------------
  // Payload normalization (tolerates missing/malformed fields; never throws)
  // -------------------------------------------------------------------------
  function clampSeverity(value) {
    if (value >= SEVERITY.CRITICAL) return SEVERITY.CRITICAL;
    if (value >= SEVERITY.WARN) return SEVERITY.WARN;
    return SEVERITY.NORMAL;
  }

  function normalizePrediction(pred) {
    if (!pred || typeof pred !== "object") return null;
    const state = String(pred.state || "NORMAL").toUpperCase();
    if (state !== "WARN" && state !== "CRITICAL") {
      return { severity: SEVERITY.NORMAL, fault: "NONE" };
    }
    const rawFault = pred.fault;
    const fault = (rawFault && rawFault !== "NONE" && rawFault !== "NORMAL")
      ? String(rawFault).toUpperCase()
      : (state === "CRITICAL" ? "CRITICAL" : "ANOMALY");
    return { severity: state === "CRITICAL" ? SEVERITY.CRITICAL : SEVERITY.WARN, fault };
  }

  function normalizeAlert(alert) {
    if (!alert || typeof alert !== "object") return null;
    const level = String(alert.level || "").toUpperCase();
    if (level === "CRITICAL" || level === "CRIT") {
      return { severity: SEVERITY.CRITICAL, fault: alert.fault ? String(alert.fault).toUpperCase() : "" };
    }
    if (level === "WARN") {
      return { severity: SEVERITY.WARN, fault: alert.fault ? String(alert.fault).toUpperCase() : "" };
    }
    return null; // INFO / unknown levels are log-only, never audible
  }

  // -------------------------------------------------------------------------
  // Incident tracker: pure edge detector, injectable clock + alert sink
  // -------------------------------------------------------------------------
  /**
   * Deduplication rules:
   *  - state NORMAL (severity 0) clears the active incident; nothing plays.
   *  - first anomaly for a fault opens an incident -> one alert.
   *  - repeats of the SAME fault+severity (every 1 Hz prediction) -> silence.
   *  - same fault at higher severity -> one escalation alert.
   *  - different fault while another is active -> one alert, rate-limited by
   *    warnFlapGuardMs for warnings (critical always fires).
   *  - seed() syncs state WITHOUT alerting (page load / SSE reconnect).
   */
  function createIncidentTracker(options) {
    const opts = options || {};
    const onAlert = typeof opts.onAlert === "function" ? opts.onAlert : function () {};
    const now = typeof opts.now === "function" ? opts.now : function () { return Date.now(); };
    const flapGuardMs = opts.warnFlapGuardMs != null ? opts.warnFlapGuardMs : AUDIO_CONFIG.warnFlapGuardMs;

    let active = null; // { fault, severity } | null (null = machine healthy)
    let lastWarnAlertAt = -Infinity;

    function emit(kind, severity, fault) {
      const evt = { kind, severity, level: severity >= SEVERITY.CRITICAL ? "CRITICAL" : "WARN", fault, ts: now() };
      if (severity === SEVERITY.WARN) lastWarnAlertAt = evt.ts;
      onAlert(evt);
      return evt;
    }

    function resolveFault(input) {
      const raw = input && input.fault ? String(input.fault).toUpperCase() : "";
      if (raw && raw !== "NONE" && raw !== "NORMAL") return raw;
      return active ? active.fault : "ANOMALY";
    }

    function update(input) {
      if (!input || typeof input !== "object") return null;
      const severity = clampSeverity(input.severity);
      if (severity === SEVERITY.NORMAL) {
        active = null; // incident cleared -> next anomaly is a fresh alert
        return null;
      }
      const fault = resolveFault(input);
      if (!active) {
        active = { fault, severity };
        return emit("new", severity, fault);
      }
      if (active.fault === fault) {
        if (severity > active.severity) {
          active.severity = severity;
          return emit("escalation", severity, fault); // warn -> critical, one shot
        }
        return null; // repeat of the same active incident: deduplicated
      }
      if (severity === SEVERITY.WARN && now() - lastWarnAlertAt < flapGuardMs) {
        active = { fault, severity }; // track silently; suppress warning flapping
        return null;
      }
      active = { fault, severity };
      return emit("new", severity, fault);
    }

    function seed(input) {
      if (!input || typeof input !== "object") return;
      const severity = clampSeverity(input.severity);
      if (severity === SEVERITY.NORMAL) {
        active = null;
        return;
      }
      active = { fault: resolveFault(input), severity };
      // deliberately silent: historical state must not replay on reload
    }

    return {
      update,
      seed,
      getActive: function () { return active ? { fault: active.fault, severity: active.severity } : null; },
      reset: function () { active = null; lastWarnAlertAt = -Infinity; },
    };
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  function resolveAudioContext() {
    try {
      if (typeof AudioContext !== "undefined") return AudioContext;
      if (typeof webkitAudioContext !== "undefined") return webkitAudioContext;
    } catch (_) { /* unsupported environment */ }
    return null;
  }

  function createMemoryStorage() {
    const map = Object.create(null);
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null; },
      setItem: function (k, v) { map[k] = String(v); },
      removeItem: function (k) { delete map[k]; },
    };
  }

  function detectStorage() {
    try {
      if (typeof localStorage !== "undefined") {
        const probe = "__aiot_probe__";
        localStorage.setItem(probe, "1");
        localStorage.removeItem(probe);
        return localStorage;
      }
    } catch (_) { /* privacy mode / disabled storage */ }
    return createMemoryStorage();
  }

  // -------------------------------------------------------------------------
  // Engine
  // -------------------------------------------------------------------------
  function createEngine(deps) {
    const d = deps || {};
    const cfg = Object.assign({}, AUDIO_CONFIG, d.config || {});
    cfg.patterns = Object.assign({}, AUDIO_CONFIG.patterns, (d.config && d.config.patterns) || {});

    const has = function (key) { return Object.prototype.hasOwnProperty.call(d, key); };
    const storage = has("storage") ? d.storage : detectStorage();
    const doc = has("document") ? d.document : (typeof document !== "undefined" ? document : null);
    const AudioCtxCtor = has("AudioContext") ? d.AudioContext : resolveAudioContext();
    const now = typeof d.now === "function" ? d.now : function () { return Date.now(); };

    let ctx = null;
    let enabled = false;
    let muted = false;
    let pendingRestore = false;
    let lastError = null;
    let restoreArmed = false;
    let restoreHandler = null;
    const subscribers = [];
    const playback = { oscs: [], master: null, endTimer: null };

    // ---- status / notifications -----------------------------------------
    function getStatus() {
      const base = { supported: !!AudioCtxCtor, enabled, muted, pending: pendingRestore };
      if (!AudioCtxCtor) {
        return Object.assign(base, { state: "unsupported", message: "Sound unsupported \u2014 visual alerts only" });
      }
      if (lastError && !enabled) {
        return Object.assign(base, { state: "error", message: lastError, error: lastError });
      }
      if (enabled && muted) return Object.assign(base, { state: "muted", message: "Sound: Muted" });
      if (enabled) return Object.assign(base, { state: "enabled", message: "Sound: Enabled" });
      if (pendingRestore) return Object.assign(base, { state: "pending", message: "Sound: click to restore" });
      return Object.assign(base, { state: "disabled", message: "Sound: Not enabled" });
    }

    function notify(event) {
      for (let i = 0; i < subscribers.length; i++) {
        try { subscribers[i](event); } catch (err) { console.warn("[AudioAlerts] subscriber failed:", err); }
      }
    }

    function notifyStatus() { notify({ type: "status", status: getStatus() }); }

    function subscribe(fn) {
      if (typeof fn !== "function") return function () {};
      subscribers.push(fn);
      return function () {
        const idx = subscribers.indexOf(fn);
        if (idx >= 0) subscribers.splice(idx, 1);
      };
    }

    // ---- preference persistence -----------------------------------------
    function persist() {
      try { storage.setItem(cfg.storageKey, JSON.stringify({ enabled, muted })); } catch (_) { /* best effort */ }
    }

    function readPref() {
      try {
        const raw = storage.getItem(cfg.storageKey);
        return raw ? JSON.parse(raw) || {} : {};
      } catch (_) { return {}; }
    }

    // ---- audio initialization (must run inside a user gesture) -----------
    function armGestureRestore() {
      if (!doc || restoreArmed) return;
      restoreArmed = true;
      restoreHandler = function () {
        // First user gesture after reload resumes the persisted "enabled" pref.
        Promise.resolve(enable({ test: false })).catch(function () {});
      };
      const types = ["pointerdown", "keydown", "touchend"];
      for (let i = 0; i < types.length; i++) {
        doc.addEventListener(types[i], restoreHandler, { once: true, passive: true });
      }
    }

    function disarmGestureRestore() {
      if (!doc || !restoreArmed || !restoreHandler) { restoreArmed = false; return; }
      const types = ["pointerdown", "keydown", "touchend"];
      for (let i = 0; i < types.length; i++) doc.removeEventListener(types[i], restoreHandler);
      restoreArmed = false;
      restoreHandler = null;
    }

    async function enable(options) {
      const opts = options || {};
      if (!AudioCtxCtor) {
        lastError = "Web Audio API is not supported in this browser.";
        notifyStatus();
        return { ok: false, error: lastError };
      }
      try {
        if (!ctx) ctx = new AudioCtxCtor();
        if (ctx.state === "suspended" && typeof ctx.resume === "function") await ctx.resume();
        if (ctx.state !== "running") {
          lastError = "Browser blocked audio (AudioContext is " + ctx.state + "). Click again or allow sound for this site.";
          notifyStatus();
          return { ok: false, error: lastError };
        }
      } catch (err) {
        lastError = "Audio initialization failed: " + ((err && err.message) || err);
        console.warn("[AudioAlerts]", lastError);
        notifyStatus();
        return { ok: false, error: lastError };
      }
      lastError = null;
      enabled = true;
      pendingRestore = false;
      disarmGestureRestore();
      persist();
      if (opts.test !== false) playPattern("test"); // activation self-test tone
      notifyStatus();
      return { ok: true };
    }

    function disable() {
      enabled = false;
      pendingRestore = false;
      stop();
      disarmGestureRestore();
      persist();
      notifyStatus();
    }

    function toggleMute() {
      muted = !muted;
      if (muted) stop(); // silence any in-flight alert immediately
      persist();
      notifyStatus();
      return getStatus();
    }

    function setMuted(value) {
      muted = !!value;
      if (muted) stop();
      persist();
      notifyStatus();
      return getStatus();
    }

    // ---- playback --------------------------------------------------------
    function stop() {
      if (playback.endTimer) {
        clearTimeout(playback.endTimer);
        playback.endTimer = null;
      }
      for (let i = 0; i < playback.oscs.length; i++) {
        try { playback.oscs[i].stop(); } catch (_) { /* already stopped */ }
        try { playback.oscs[i].disconnect(); } catch (_) { /* detached */ }
      }
      playback.oscs = [];
      if (playback.master) {
        try { playback.master.disconnect(); } catch (_) { /* detached */ }
        playback.master = null;
      }
    }

    function playPattern(name) {
      if (!ctx || ctx.state !== "running" || !enabled || muted) return false;
      const pattern = cfg.patterns[name];
      if (!pattern || !pattern.notes || !pattern.notes.length) return false;
      try {
        stop(); // never overlap tones: a new decision silences the previous one
        const master = ctx.createGain();
        master.gain.value = cfg.masterVolume;
        master.connect(ctx.destination);

        const oscs = [];
        const noteDur = pattern.noteMs / 1000;
        const gap = pattern.gapMs / 1000;
        const repeat = Math.max(1, Math.min(pattern.repeat || 1, cfg.maxRepeats));
        let offset = 0;

        for (let r = 0; r < repeat; r++) {
          for (let i = 0; i < pattern.notes.length; i++) {
            const start = ctx.currentTime + 0.03 + offset;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = pattern.type || "sine";
            if (osc.frequency && typeof osc.frequency.setValueAtTime === "function") {
              osc.frequency.setValueAtTime(pattern.notes[i], start);
            }
            // short attack/release envelope avoids audible clicks
            gain.gain.setValueAtTime(0, start);
            gain.gain.linearRampToValueAtTime(1, start + 0.015);
            gain.gain.setValueAtTime(1, Math.max(start + 0.02, start + noteDur - 0.03));
            gain.gain.linearRampToValueAtTime(0, start + noteDur);
            osc.connect(gain);
            gain.connect(master);
            osc.start(start);
            osc.stop(start + noteDur + 0.02);
            oscs.push(osc);
            offset += noteDur + gap;
          }
        }

        const totalMs = Math.min(offset * 1000 + 80, cfg.maxAlertMs);
        playback.oscs = oscs;
        playback.master = master;
        playback.endTimer = setTimeout(function () { stop(); }, totalMs);
        return true;
      } catch (err) {
        console.warn("[AudioAlerts] Tone playback failed:", err);
        stop();
        return false;
      }
    }

    function playTest() { return playPattern("test"); }

    // ---- anomaly event entry points --------------------------------------
    function onTrackerAlert(evt) {
      const audible = playPattern(evt.severity >= SEVERITY.CRITICAL ? "crit" : "warn");
      notify({ type: "alert", severity: evt.level, kind: evt.kind, fault: evt.fault, audible, ts: evt.ts });
    }

    const tracker = createIncidentTracker({ now: now, onAlert: onTrackerAlert });

    function handlePrediction(pred) {
      try { tracker.update(normalizePrediction(pred)); }
      catch (err) { console.warn("[AudioAlerts] prediction handling failed:", err); }
    }

    function handleAlert(payload) {
      try { tracker.update(normalizeAlert(payload)); }
      catch (err) { console.warn("[AudioAlerts] alert handling failed:", err); }
    }

    function seed(pred) {
      try { tracker.seed(normalizePrediction(pred)); }
      catch (err) { console.warn("[AudioAlerts] seed handling failed:", err); }
    }

    // ---- boot: restore persisted preference (sound stays off until a gesture)
    const pref = readPref();
    muted = !!pref.muted;
    if (pref.enabled && AudioCtxCtor) {
      pendingRestore = true;
      armGestureRestore();
    }

    return {
      config: cfg,
      supported: !!AudioCtxCtor,
      enable,
      disable,
      toggleMute,
      setMuted,
      stop,
      playTest,
      handlePrediction,
      handleAlert,
      seed,
      subscribe,
      getStatus,
      getActiveIncident: function () { return tracker.getActive(); },
      getTracker: function () { return tracker; },
    };
  }

  return {
    AUDIO_CONFIG,
    SEVERITY,
    createIncidentTracker,
    createEngine,
    normalizePrediction,
    normalizeAlert,
  };
});
