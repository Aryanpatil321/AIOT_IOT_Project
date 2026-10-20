/**
 * AIoT Predictive Safety System - Browser Audio Alert Tests (zero-dependency)
 *
 * Run (Windows/macOS/Linux, Node 18+):
 *   node dashboard/tests/audio_alert.test.js
 *   node --test dashboard/tests/
 *
 * Uses Node's built-in node:test runner + a mocked Web Audio API, so no npm
 * install, browser, or hardware is required. Browser-only behaviour (real
 * speaker output, autoplay policy, DOM controls) is covered by the manual
 * checklist in docs/audio_alerts.md.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  AUDIO_CONFIG,
  SEVERITY,
  createIncidentTracker,
  createEngine,
  normalizePrediction,
  normalizeAlert,
} = require("../static/js/audio-alert.js");

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
function createMockAudioContextClass(options) {
  const opts = options || {};
  const instances = [];

  class MockParam {
    constructor() { this.value = 0; }
    setValueAtTime(v) { this.value = v; return this; }
    linearRampToValueAtTime(v) { this.value = v; return this; }
  }

  class MockCtx {
    constructor() {
      if (opts.failConstruct) throw new Error("boom: AudioContext denied");
      this.state = opts.initialState || "suspended";
      this.currentTime = 0;
      this.destination = { name: "destination" };
      this.stats = { oscStarts: 0, oscStops: 0, resumes: 0 };
      instances.push(this);
    }
    resume() {
      this.stats.resumes += 1;
      if (!opts.stuckSuspended) this.state = "running";
      return Promise.resolve();
    }
    createGain() {
      const self = this;
      return {
        gain: new MockParam(),
        connect() {},
        disconnect() {},
      };
    }
    createOscillator() {
      const self = this;
      return {
        type: "sine",
        frequency: new MockParam(),
        onended: null,
        _started: false,
        connect() {},
        disconnect() {},
        start() { this._started = true; self.stats.oscStarts += 1; },
        stop() {
          if (!this._started) throw new Error("InvalidStateError: not started");
          self.stats.oscStops += 1;
        },
      };
    }
  }

  MockCtx.instances = instances;
  return MockCtx;
}

function createMemoryStorage(seed) {
  const map = Object.assign(Object.create(null), seed || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null),
    setItem: (k, v) => { map[k] = String(v); },
    removeItem: (k) => { delete map[k]; },
    _map: map,
  };
}

function freshEngine(deps) {
  const MockCtx = (deps && "ctxClass" in deps)
    ? deps.ctxClass
    : createMockAudioContextClass();
  const storage = (deps && deps.storage) || createMemoryStorage();
  const alerts = [];
  const statuses = [];
  const eng = createEngine(Object.assign({
    AudioContext: MockCtx,
    storage,
    document: null,
    now: (deps && deps.now) || (() => Date.now()),
  }, deps && deps.extra ? deps.extra : {}));
  eng.subscribe((evt) => {
    if (evt.type === "alert") alerts.push(evt);
    if (evt.type === "status") statuses.push(evt.status);
  });
  return { eng, MockCtx, storage, alerts, statuses };
}

function stats(MockCtx) {
  return MockCtx.instances.length
    ? MockCtx.instances[MockCtx.instances.length - 1].stats
    : { oscStarts: 0, oscStops: 0, resumes: 0 };
}

// ---------------------------------------------------------------------------
// 1. Incident tracker: anomaly decisions (dedup / escalation / reload)
// ---------------------------------------------------------------------------
test("1. normal operation never triggers an alert", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  for (let i = 0; i < 5; i++) {
    tracker.update(normalizePrediction({ state: "NORMAL", fault: "NONE" }));
  }
  assert.equal(fired.length, 0);
  assert.equal(tracker.getActive(), null);
});

test("2. a new warning anomaly fires exactly one warning alert", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  tracker.update(normalizePrediction({ state: "WARN", fault: "IMBALANCE" }));
  assert.equal(fired.length, 1);
  assert.equal(fired[0].level, "WARN");
  assert.equal(fired[0].kind, "new");
  assert.equal(fired[0].fault, "IMBALANCE");
});

test("3. a new critical fault fires exactly one critical alert", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  tracker.update(normalizePrediction({ state: "CRITICAL", fault: "CRITICAL" }));
  assert.equal(fired.length, 1);
  assert.equal(fired[0].level, "CRITICAL");
  assert.equal(fired[0].kind, "new");
});

test("4. repeated 1 Hz updates for the same active anomaly stay silent", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  tracker.update(normalizePrediction({ state: "WARN", fault: "OVERLOAD" }));
  for (let i = 0; i < 30; i++) {
    tracker.update(normalizePrediction({ state: "WARN", fault: "OVERLOAD" }));
  }
  assert.equal(fired.length, 1, "steady-state warning must sound exactly once");
});

test("5. warning escalates to critical with one escalation alert", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  // Same fault, higher severity (payload-driven escalation)
  tracker.update(normalizePrediction({ state: "WARN", fault: "OVERHEATING" }));
  tracker.update(normalizePrediction({ state: "WARN", fault: "OVERHEATING" }));
  tracker.update(normalizePrediction({ state: "CRITICAL", fault: "OVERHEATING" }));
  assert.equal(fired.length, 2);
  assert.equal(fired[1].kind, "escalation");
  assert.equal(fired[1].level, "CRITICAL");
  // Taxonomy-driven escalation (WARN OVERLOAD -> CRITICAL class)
  const fired2 = [];
  const t2 = createIncidentTracker({ onAlert: (e) => fired2.push(e) });
  t2.update(normalizePrediction({ state: "WARN", fault: "OVERLOAD" }));
  t2.update(normalizePrediction({ state: "CRITICAL", fault: "CRITICAL" }));
  assert.equal(fired2.length, 2);
  assert.equal(fired2[1].level, "CRITICAL");
});

test("6. a cleared incident allows a later new incident to alert again", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  tracker.update(normalizePrediction({ state: "WARN", fault: "IMBALANCE" }));
  tracker.update(normalizePrediction({ state: "NORMAL", fault: "NONE" }));
  tracker.update(normalizePrediction({ state: "WARN", fault: "IMBALANCE" }));
  assert.equal(fired.length, 2, "two distinct incidents must both alert");
});

test("7. reload: seeded historical incident does not replay, escalation still fires", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  // SSE snapshot on page load carries the still-active incident
  tracker.seed(normalizePrediction({ state: "WARN", fault: "BEARING_INNER" }));
  // Subsequent live updates for that same incident stay silent
  tracker.update(normalizePrediction({ state: "WARN", fault: "BEARING_INNER" }));
  tracker.update(normalizePrediction({ state: "WARN", fault: "BEARING_INNER" }));
  assert.equal(fired.length, 0, "historical alert must not replay on reload");
  // ...but a genuine escalation after load still alerts once
  tracker.update(normalizePrediction({ state: "CRITICAL", fault: "CRITICAL" }));
  assert.equal(fired.length, 1);
  assert.equal(fired[0].level, "CRITICAL");
});

test("8. warning flap guard suppresses oscillating different faults", () => {
  let clock = 100000;
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e), now: () => clock });
  tracker.update(normalizePrediction({ state: "WARN", fault: "IMBALANCE" }));
  clock += 500; // model oscillates 0.5 s later
  tracker.update(normalizePrediction({ state: "WARN", fault: "MISALIGNMENT" }));
  assert.equal(fired.length, 1, "second warning inside flap guard is suppressed");
  clock += AUDIO_CONFIG.warnFlapGuardMs;
  tracker.update(normalizePrediction({ state: "WARN", fault: "OVERLOAD" }));
  assert.equal(fired.length, 2, "warning after the guard window alerts again");
});

test("9. MQTT alert payloads merge with the active incident (no duplicates)", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  tracker.update(normalizePrediction({ state: "WARN", fault: "OVERLOAD" }));
  // infer_realtime.py publishes a WARN alert for the same fault right after
  tracker.update(normalizeAlert({ level: "WARN", fault: "OVERLOAD", msg: "Early fault" }));
  assert.equal(fired.length, 1, "prediction + alert for one incident = one sound");
  // Alert without a fault field must fold into the active incident, not open a new one
  tracker.update(normalizeAlert({ level: "WARN", msg: "no fault field" }));
  assert.equal(fired.length, 1);
  // Info/unknown alert levels are log-only
  assert.equal(normalizeAlert({ level: "INFO", msg: "hello" }), null);
  tracker.update(normalizeAlert({ level: "INFO", msg: "hello" }));
  assert.equal(fired.length, 1);
});

test("10. malformed payloads never throw and never alert", () => {
  const fired = [];
  const tracker = createIncidentTracker({ onAlert: (e) => fired.push(e) });
  const junk = [null, undefined, 42, "WARN", {}, { state: "" }, { state: null },
    { state: "STALE" }, { state: "INFO" }, { fault: "X" }];
  for (const j of junk) {
    assert.doesNotThrow(() => tracker.update(normalizePrediction(j)));
    assert.doesNotThrow(() => tracker.seed(normalizePrediction(j)));
    assert.doesNotThrow(() => tracker.update(normalizeAlert(j)));
  }
  assert.equal(fired.length, 0);
  // Case tolerance is intentional, not garbage: lowercase "warn" is a real warning
  assert.equal(normalizePrediction({ state: "warn" }).severity, SEVERITY.WARN);
});

// ---------------------------------------------------------------------------
// 2. Engine: enable / mute / failure paths with a mocked Web Audio API
// ---------------------------------------------------------------------------
test("11. Enable Sound Alerts initializes audio and plays a test tone", async () => {
  const { eng, MockCtx } = freshEngine();
  assert.equal(eng.getStatus().state, "disabled");
  const res = await eng.enable();
  assert.equal(res.ok, true);
  assert.equal(eng.getStatus().state, "enabled");
  assert.equal(eng.getStatus().message, "Sound: Enabled");
  assert.ok(stats(MockCtx).resumes >= 1, "AudioContext must be resumed on the user gesture");
  assert.ok(stats(MockCtx).oscStarts >= 1, "activation must play the test tone");
});

test("12. audible warning / critical playback and dedup end-to-end", async () => {
  const { eng, MockCtx, alerts } = freshEngine();
  await eng.enable({ test: false });
  const before = stats(MockCtx).oscStarts;

  eng.handlePrediction({ state: "WARN", fault: "IMBALANCE" });
  const afterWarn = stats(MockCtx).oscStarts;
  assert.ok(afterWarn > before, "new warning must synthesize tone samples");
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].audible, true);
  const warnOsc = afterWarn - before;

  // repeats of the same incident: no extra sound
  for (let i = 0; i < 10; i++) eng.handlePrediction({ state: "WARN", fault: "IMBALANCE" });
  assert.equal(stats(MockCtx).oscStarts, afterWarn, "steady state must not re-sound");

  // critical uses a longer (more urgent) pattern than the warning
  eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" });
  const critOsc = stats(MockCtx).oscStarts - afterWarn;
  assert.ok(critOsc > warnOsc, "critical pattern must be longer/urgent vs warning");
  assert.equal(alerts.length, 2);
  assert.equal(alerts[1].severity, "CRITICAL");
  assert.equal(alerts[1].audible, true);
});

test("13. muting silences playback while detection keeps running", async () => {
  const { eng, MockCtx, alerts } = freshEngine();
  await eng.enable({ test: false });
  eng.handlePrediction({ state: "WARN", fault: "OVERLOAD" });
  assert.ok(stats(MockCtx).oscStarts > 0);

  const status = eng.toggleMute();
  assert.equal(status.state, "muted");
  assert.ok(stats(MockCtx).oscStops >= 1, "mute must stop tones already playing");

  const startBefore = stats(MockCtx).oscStarts;
  eng.handlePrediction({ state: "NORMAL", fault: "NONE" });
  eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" });
  assert.equal(stats(MockCtx).oscStarts, startBefore, "muted engine must stay silent");
  assert.equal(alerts[alerts.length - 1].audible, false, "visual alert still fires while muted");
  assert.ok(eng.getActiveIncident(), "detection state must survive mute");

  eng.toggleMute();
  assert.equal(eng.getStatus().state, "enabled");
  // same incident still active -> no replay after unmute
  eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" });
  assert.equal(stats(MockCtx).oscStarts, startBefore, "unmute must not replay active incident");
});

test("14. audio initialization failure never breaks the dashboard", async () => {
  const { eng, alerts } = freshEngine({ ctxClass: createMockAudioContextClass({ failConstruct: true }) });
  const res = await eng.enable();
  assert.equal(res.ok, false);
  assert.match(res.error, /boom/);
  assert.equal(eng.getStatus().state, "error");

  // Anomaly handling must keep working (visual path) without throwing
  assert.doesNotThrow(() => eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" }));
  assert.doesNotThrow(() => eng.handleAlert({ level: "CRIT", fault: "CRITICAL" }));
  assert.equal(alerts.length, 1, "alert decision still emitted for visual UI");
  assert.equal(alerts[0].audible, false);
});

test("15. browser-blocked (still suspended) context reports an honest error", async () => {
  const { eng } = freshEngine({ ctxClass: createMockAudioContextClass({ stuckSuspended: true }) });
  const res = await eng.enable();
  assert.equal(res.ok, false);
  assert.equal(eng.supported, true);
  assert.equal(eng.getStatus().state, "error");
  assert.equal(eng.getStatus().enabled, false, "never claim enabled when blocked");
});

test("16. unsupported Web Audio degrades gracefully", async () => {
  const { eng } = freshEngine({ ctxClass: null });
  assert.equal(eng.supported, false);
  assert.equal(eng.getStatus().state, "unsupported");
  const res = await eng.enable();
  assert.equal(res.ok, false);
  assert.doesNotThrow(() => eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" }));
  assert.doesNotThrow(() => eng.handleAlert({ level: "CRIT" }));
});

test("17. reload with persisted preference stays silent until a user gesture", async () => {
  const storage = createMemoryStorage({ "aiot.soundAlerts": JSON.stringify({ enabled: true, muted: false }) });
  const { eng, MockCtx, alerts } = freshEngine({ storage });
  // Preference was remembered, but autoplay policy keeps sound off...
  assert.equal(eng.getStatus().state, "pending");
  assert.equal(stats(MockCtx).oscStarts, 0, "no context/tone may start without a gesture");
  // ...and an active historical incident must not replay after reload
  eng.seed({ state: "WARN", fault: "IMBALANCE" });
  eng.handlePrediction({ state: "WARN", fault: "IMBALANCE" });
  assert.equal(alerts.length, 0);

  // Explicit user click on Enable Sound Alerts
  const res = await eng.enable({ test: true });
  assert.equal(res.ok, true);
  assert.equal(eng.getStatus().state, "enabled");
  assert.ok(stats(MockCtx).oscStarts >= 1);

  // The same pre-existing incident still must not replay after enabling
  eng.handlePrediction({ state: "WARN", fault: "IMBALANCE" });
  assert.equal(alerts.length, 0);
  // ...but a fresh escalation is audible
  eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" });
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].audible, true);
});

test("18. persisted preference survives disable/mute round-trips", async () => {
  const storage = createMemoryStorage();
  const { eng } = freshEngine({ storage });
  await eng.enable({ test: false });
  eng.toggleMute();
  assert.deepEqual(JSON.parse(storage.getItem("aiot.soundAlerts")), { enabled: true, muted: true });

  // New engine instance (page reload) restores muted+enabled as a pending pref
  const second = freshEngine({ storage, ctxClass: createMockAudioContextClass() });
  assert.equal(second.eng.getStatus().state, "pending");
  assert.equal(second.eng.getStatus().muted, true);

  eng.disable();
  assert.deepEqual(JSON.parse(storage.getItem("aiot.soundAlerts")), { enabled: false, muted: true });
});

test("19. stop() cancels a playing pattern immediately", async () => {
  const { eng, MockCtx } = freshEngine();
  await eng.enable({ test: false });
  eng.handlePrediction({ state: "CRITICAL", fault: "CRITICAL" });
  const started = stats(MockCtx).oscStarts;
  assert.ok(started > 0);
  eng.stop();
  assert.ok(stats(MockCtx).oscStops >= started, "every scheduled oscillator must be stopped");
  // a subsequent new incident still plays
  eng.handlePrediction({ state: "NORMAL", fault: "NONE" });
  eng.handlePrediction({ state: "WARN", fault: "IMBALANCE" });
  assert.ok(stats(MockCtx).oscStarts > started);
});

test("20. severity mapping matches the project's state semantics", () => {
  assert.equal(SEVERITY.NORMAL, 0);
  assert.equal(SEVERITY.WARN, 1);
  assert.equal(SEVERITY.CRITICAL, 2);
  assert.deepEqual(normalizePrediction({ state: "NORMAL" }), { severity: 0, fault: "NONE" });
  assert.deepEqual(normalizePrediction({ state: "WARN", fault: "MISALIGNMENT" }),
    { severity: 1, fault: "MISALIGNMENT" });
  assert.deepEqual(normalizePrediction({ state: "CRITICAL", fault: "NONE" }),
    { severity: 2, fault: "CRITICAL" });
  assert.equal(normalizeAlert({ level: "CRIT", fault: "CRITICAL" }).severity, 2);
  assert.equal(normalizeAlert({ level: "WARN", fault: "IMBALANCE" }).severity, 1);
  assert.equal(normalizePrediction(null), null);
  assert.equal(normalizeAlert(null), null);
});
