/**
 * AIoT Predictive Safety System - Industrial Cockpit Controller
 *
 * Matches dashboard/templates/index.html element IDs and the Flask backend
 * (dashboard/app.py): SSE stream at /api/stream with named events
 * (snapshot, sensors, prediction, status, command, alert, fault_log_entry),
 * POST /api/command {relay, reason}, POST /api/simulator/mode {mode}.
 */

"use strict";

const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 68; // r=68 in the SVG dial
const MAX_CHART_POINTS = 60;
const FAULT_LOG_MAX = 60;
const PREDICTION_STALE_MS = 5000;
const CLASSES = [
  "NORMAL", "IMBALANCE", "OVERLOAD", "OVERHEATING",
  "BEARING_INNER", "BEARING_OUTER", "MISALIGNMENT", "CRITICAL",
];

// ---------------------------------------------------------------------------
// DOM Element Registry (IDs verified against index.html)
// ---------------------------------------------------------------------------
const els = {
  liveClock: document.getElementById("live-clock"),
  connectionPill: document.getElementById("connection-pill"),
  connectionText: document.getElementById("connection-text"),
  deviceMeta: document.getElementById("device-meta"),

  emergencyBanner: document.getElementById("emergency-banner"),
  bannerTitle: document.getElementById("banner-title"),
  bannerDesc: document.getElementById("banner-desc"),
  bannerResetBtn: document.getElementById("banner-reset-btn"),

  gaugeBar: document.getElementById("gauge-bar"),
  healthValue: document.getElementById("health-value"),
  healthTag: document.getElementById("health-tag"),

  conditionChip: document.getElementById("condition-chip"),
  conditionIconBox: document.getElementById("condition-icon-box"),
  primaryFaultTitle: document.getElementById("primary-fault-title"),
  conditionDesc: document.getElementById("condition-desc"),
  confidencePercentage: document.getElementById("confidence-percentage"),
  confidenceFill: document.getElementById("confidence-fill"),

  relayPill: document.getElementById("relay-pill"),
  relaySwitchIndicator: document.getElementById("relay-switch-indicator"),
  relayStatusText: document.getElementById("relay-status-text"),
  latchStatusVal: document.getElementById("latch-status-val"),

  sensorTemp: document.getElementById("sensor-temp"),
  sensorVibRms: document.getElementById("sensor-vib-rms"),
  sensorVibPeak: document.getElementById("sensor-vib-peak"),
  sensorCurrent: document.getElementById("sensor-current"),
  trendChart: document.getElementById("trendChart"),

  deltaValTemp: document.getElementById("delta-val-temp"),
  deltaValVib: document.getElementById("delta-val-vib"),
  deltaValPeak: document.getElementById("delta-val-peak"),
  deltaValCurr: document.getElementById("delta-val-curr"),
  explanationList: document.getElementById("explanation-list"),

  logCountBadge: document.getElementById("log-count-badge"),
  faultLog: document.getElementById("fault-log"),

  diagBandChip: document.getElementById("diag-band-chip"),
  diagnosisCause: document.getElementById("diagnosis-cause"),
  diagConfidenceFill: document.getElementById("diag-confidence-fill"),
  diagConfidenceVal: document.getElementById("diag-confidence-val"),
  reasonList: document.getElementById("reason-list"),
  remedyList: document.getElementById("remedy-list"),

  btnEmergencyStop: document.getElementById("btn-emergency-stop"),
  btnManualReset: document.getElementById("btn-manual-reset"),
  btnRelayOn: document.getElementById("btn-relay-on"),

  btnEnableSound: document.getElementById("btn-enable-sound"),
  btnEnableSoundLabel: document.getElementById("btn-enable-sound-label"),
  btnTestSound: document.getElementById("btn-test-sound"),
  btnMuteSound: document.getElementById("btn-mute-sound"),
  btnMuteSoundLabel: document.getElementById("btn-mute-sound-label"),
  audioStatus: document.getElementById("audio-status"),
  audioFlash: document.getElementById("audio-flash"),

  scenarioButtons: Array.from(document.querySelectorAll(".btn-scenario")),
};

let chart = null;
let lastPredictionAt = 0;
let faultLogCount = 0;

// ---------------------------------------------------------------------------
// Live clock
// ---------------------------------------------------------------------------
function tickClock() {
  if (els.liveClock) els.liveClock.textContent = new Date().toLocaleTimeString();
}
setInterval(tickClock, 1000);
tickClock();

// ---------------------------------------------------------------------------
// Chart.js (vendored - guard so the rest of the cockpit survives without it)
// ---------------------------------------------------------------------------
const chartData = { labels: [], temp: [], vib: [], curr: [] };

function initChart() {
  if (typeof Chart === "undefined" || !els.trendChart) {
    console.warn("[Cockpit] Chart.js unavailable; trend chart disabled.");
    return;
  }
  try {
    chart = new Chart(els.trendChart.getContext("2d"), {
      type: "line",
      data: {
        labels: chartData.labels,
        datasets: [
          {
            label: "Temperature (°C)",
            data: chartData.temp,
            borderColor: "#fb923c",
            backgroundColor: "rgba(251,146,60,0.12)",
            yAxisID: "yTemp",
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: "Vib RMS (g)",
            data: chartData.vib,
            borderColor: "#00f2fe",
            backgroundColor: "rgba(0,242,254,0.10)",
            yAxisID: "ySig",
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: "Current (A)",
            data: chartData.curr,
            borderColor: "#10b981",
            backgroundColor: "rgba(16,185,129,0.10)",
            yAxisID: "ySig",
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: "#94a3b8", maxTicksLimit: 8, maxRotation: 0 },
            grid: { color: "rgba(148,163,184,0.08)" },
          },
          yTemp: {
            position: "left",
            min: 0,
            suggestedMax: 80,
            ticks: { color: "#fb923c" },
            grid: { color: "rgba(148,163,184,0.08)" },
            title: { display: true, text: "°C", color: "#fb923c" },
          },
          ySig: {
            position: "right",
            min: 0,
            suggestedMax: 4.5,
            ticks: { color: "#94a3b8" },
            grid: { drawOnChartArea: false },
            title: { display: true, text: "g / A", color: "#94a3b8" },
          },
        },
      },
    });
  } catch (err) {
    console.warn("[Cockpit] Chart init failed:", err);
    chart = null;
  }
}

function pushChartPoint(label, temp, vib, curr) {
  chartData.labels.push(label);
  chartData.temp.push(temp);
  chartData.vib.push(vib);
  chartData.curr.push(curr);
  if (chartData.labels.length > MAX_CHART_POINTS) {
    chartData.labels.shift();
    chartData.temp.shift();
    chartData.vib.shift();
    chartData.curr.shift();
  }
  if (chart) {
    chart.data.labels = [...chartData.labels];
    chart.data.datasets[0].data = [...chartData.temp];
    chart.data.datasets[1].data = [...chartData.vib];
    chart.data.datasets[2].data = [...chartData.curr];
    chart.update("none");
  }
}

function resetChart() {
  chartData.labels.length = 0;
  chartData.temp.length = 0;
  chartData.vib.length = 0;
  chartData.curr.length = 0;
  if (chart) {
    chart.data.labels = [];
    chart.data.datasets.forEach((d) => (d.data = []));
    chart.update("none");
  }
}

// ---------------------------------------------------------------------------
// Emergency banner
// ---------------------------------------------------------------------------
function showEmergencyBanner(title, desc) {
  if (!els.emergencyBanner) return;
  if (els.bannerTitle) els.bannerTitle.textContent = title;
  if (els.bannerDesc) els.bannerDesc.textContent = desc;
  els.emergencyBanner.classList.remove("hidden");
}

function hideEmergencyBanner() {
  if (els.emergencyBanner) els.emergencyBanner.classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------
async function sendCommand(relay, reason) {
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ relay, reason }),
    });
    const data = await res.json();
    const sent = data.sent || {};
    if (sent.relay === "REFUSED") {
      alert(sent.detail || "Command refused: relay is latched. Send MANUAL RESET first.");
      return false;
    }
    return true;
  } catch (err) {
    console.error("[Cockpit] Relay command failed:", err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------
function renderSensors(s) {
  if (!s) return;
  const temp = Number(s.temp) || 0;
  const vibRms = Number(s.vib_rms) || 0;
  const vibPeak = Number(s.vib_peak) || 0;
  const curr = Number(s.current) || 0;

  if (els.sensorTemp) els.sensorTemp.textContent = temp.toFixed(1);
  if (els.sensorVibRms) els.sensorVibRms.textContent = vibRms.toFixed(3);
  if (els.sensorVibPeak) els.sensorVibPeak.textContent = vibPeak.toFixed(3);
  if (els.sensorCurrent) els.sensorCurrent.textContent = curr.toFixed(3);

  // ts is epoch seconds from Python services, milliseconds from the ESP32.
  const ts = Number(s.ts) || 0;
  const label = new Date(ts > 1e12 ? ts : ts * 1000).toLocaleTimeString();
  pushChartPoint(label, temp, vibRms, curr);
}

function renderHealth(health) {
  const pct = Math.max(0, Math.min(100, Number(health) || 0));
  const dash = (GAUGE_CIRCUMFERENCE * pct) / 100;
  if (els.gaugeBar) {
    els.gaugeBar.setAttribute("stroke-dasharray", `${dash} ${GAUGE_CIRCUMFERENCE}`);
  }
  if (els.healthValue) els.healthValue.textContent = Math.round(pct);
  if (els.healthTag) {
    els.healthTag.textContent = pct >= 80 ? "OPTIMAL" : pct >= 50 ? "DEGRADED" : "AT RISK";
  }
}

function renderClassProbs(classProbs) {
  if (!classProbs) return;
  CLASSES.forEach((cls) => {
    const prob = classProbs[cls];
    const bar = document.getElementById("prob-" + cls);
    const val = document.getElementById("val-" + cls);
    if (prob == null) return;
    const pct = Math.round(prob * 100);
    if (bar) bar.style.width = pct + "%";
    if (val) val.textContent = pct + "%";
  });
}

function setDelta(el, value) {
  if (!el || value == null) return;
  const num = Math.round(value);
  el.textContent = (num > 0 ? "+" : "") + num + "%";
  el.className = "delta-num" + (Math.abs(num) >= 30 ? " alert-high" : "");
}

function renderPrediction(p) {
  if (!p) return;
  lastPredictionAt = Date.now();

  const fault = p.fault && p.fault !== "NONE" ? p.fault : "NORMAL";
  const state = p.state || "NORMAL";
  const conf = Number(p.confidence) || 0;
  const confPct = Math.round(conf * 100);

  // Condition card
  if (els.primaryFaultTitle) {
    els.primaryFaultTitle.textContent = fault === "NORMAL" ? "Nominal Operation" : fault;
  }
  if (els.conditionDesc) {
    const lines = Array.isArray(p.explanation) ? p.explanation : [];
    els.conditionDesc.textContent = lines[0] || (fault === "NORMAL"
      ? "Smooth rotation \u2022 Balanced rotor \u2022 Normal thermal dissipation"
      : "Fault classified from multi-sensor fusion.");
  }
  if (els.conditionChip) {
    const cls = state === "CRITICAL" ? "pill-crit" : state === "WARN" ? "pill-warn" : "pill-normal";
    els.conditionChip.className = "pill-badge " + cls;
    els.conditionChip.textContent = state;
  }
  if (els.conditionIconBox) {
    els.conditionIconBox.style.color = state === "CRITICAL" ? "#ef4444"
      : state === "WARN" ? "#f59e0b" : "#10b981";
  }
  if (els.confidencePercentage) els.confidencePercentage.textContent = confPct + "%";
  if (els.confidenceFill) {
    els.confidenceFill.style.width = confPct + "%";
    els.confidenceFill.style.background = state === "CRITICAL" ? "#ef4444"
      : state === "WARN" ? "#f59e0b" : "#10b981";
  }

  renderHealth(p.health);
  renderClassProbs(p.class_probs);

  // XAI deltas + explanations
  if (p.deltas) {
    setDelta(els.deltaValTemp, p.deltas.temperature);
    setDelta(els.deltaValVib, p.deltas.vibration_RMS);
    setDelta(els.deltaValPeak, p.deltas.vibration_peak);
    setDelta(els.deltaValCurr, p.deltas.current);
  }
  if (els.explanationList && Array.isArray(p.explanation) && p.explanation.length) {
    els.explanationList.innerHTML = p.explanation
      .map((line) => `<li>${line}</li>`)
      .join("");
  }

  renderDiagnosis(p.diagnosis);

  // Emergency banner
  if (state === "CRITICAL") {
    showEmergencyBanner(
      "CRITICAL MULTI-SENSOR FAULT DETECTED",
      `Active prediction: ${fault} with ${confPct}% confidence. Emergency cutoff interlock active.`
    );
  } else if (state === "NORMAL") {
    hideEmergencyBanner();
  }
}

function renderDiagnosis(d) {
  if (!d) return;

  if (els.diagBandChip) {
    const band = d.confidence_band || "LOW";
    const faultTag = d.prediction === "NORMAL" ? "HEALTHY" : d.prediction;
    els.diagBandChip.textContent = `${faultTag} \u2022 ${band} CONFIDENCE`;
    els.diagBandChip.className = "card-badge " + (
      d.prediction === "NORMAL" ? "badge-band-ok"
        : band === "HIGH" ? "badge-band-high"
        : band === "MEDIUM" ? "badge-band-medium" : "badge-band-low"
    );
  }

  if (els.diagnosisCause && d.primary_cause) {
    els.diagnosisCause.textContent = d.primary_cause;
  }

  if (els.diagConfidenceVal && d.confidence != null) {
    const pct = Math.round(d.confidence * 100);
    els.diagConfidenceVal.textContent = pct + "%";
    if (els.diagConfidenceFill) els.diagConfidenceFill.style.width = pct + "%";
  }

  if (els.reasonList) {
    const reasons = Array.isArray(d.reasons) ? d.reasons : [];
    els.reasonList.innerHTML = reasons.length
      ? reasons.slice(0, 6).map((r) => `<li>${r.interpretation}</li>`).join("")
      : "<li>No anomalous evidence detected.</li>";
  }

  if (els.remedyList) {
    const remedies = Array.isArray(d.remedies) ? d.remedies : [];
    els.remedyList.innerHTML = remedies.length
      ? remedies.map((r) => `
          <li class="remedy-item">
            <span class="remedy-prio prio-${r.priority}">${r.priority}</span>
            <span class="remedy-body">
              <span class="remedy-cat">${r.category || "General"}</span>
              ${r.action}
            </span>
          </li>`).join("")
      : "<li class='empty-log-item'>No remediation required.</li>";
  }
}

function renderStatus(st) {
  if (!st) return;
  const relayState = (st.relay_state || "").toUpperCase();
  if (relayState === "ON" || relayState === "OFF") {
    const on = relayState === "ON";
    if (els.relayPill) {
      els.relayPill.textContent = on ? "ENERGIZED (ON)" : "DISCONNECTED (OFF)";
      els.relayPill.className = "pill-badge " + (on ? "pill-normal" : "pill-crit");
    }
    if (els.relaySwitchIndicator) {
      els.relaySwitchIndicator.className = "relay-switch-indicator " + (on ? "state-on" : "state-off");
    }
    if (els.relayStatusText) {
      els.relayStatusText.textContent = on ? "MOTOR RUNNING" : "MOTOR CUTOFF (OFF)";
    }
    if (els.latchStatusVal) {
      els.latchStatusVal.textContent = on ? "ARMED / NORMAL" : "LATCHED / TRIPPED";
      els.latchStatusVal.className = "meta-val " + (on ? "text-green" : "text-red");
    }
  }
  if (st.ip_addr && els.deviceMeta) {
    els.deviceMeta.textContent = `ESP32 @ ${st.ip_addr} \u2022 RSSI ${st.wifi_rssi ?? "-"} dBm`;
  }
}

function appendFaultLog(entry) {
  if (!els.faultLog || !entry) return;
  if (els.faultLog.querySelector(".empty-log-item")) els.faultLog.innerHTML = "";

  faultLogCount++;
  if (els.logCountBadge) els.logCountBadge.textContent = faultLogCount + " Events";

  let tagClass = "tag-info";
  const state = entry.state || "INFO";
  if (state === "CRITICAL" || state === "CRIT" || state === "SAFETY") tagClass = "tag-crit";
  else if (state === "WARN") tagClass = "tag-warn";
  else if (state === "MANUAL" || state === "CMD") tagClass = "tag-manual";
  else if (state === "SIMULATION" || state === "SIM") tagClass = "tag-sim";

  const li = document.createElement("li");
  li.className = "log-item";
  const conf = entry.confidence != null ? ` (${Math.round(entry.confidence * 100)}%)` : "";
  li.innerHTML = `
    <span class="log-time">${entry.time || "--:--:--"}</span>
    <span class="log-tag ${tagClass}">${state}</span>
    <span class="log-msg">${entry.detail || ""}${conf}</span>`;
  els.faultLog.prepend(li);
  while (els.faultLog.children.length > FAULT_LOG_MAX) {
    els.faultLog.removeChild(els.faultLog.lastChild);
  }
}

function setConnectionStatus(connected) {
  if (els.connectionPill) {
    els.connectionPill.className = "status-pill " + (connected ? "status-pill-online" : "status-pill-offline");
  }
  if (els.connectionText) {
    els.connectionText.textContent = connected ? "Telemetry Live (1Hz)" : "Disconnected (Reconnecting...)";
  }
}

function markPredictionStale() {
  if (!lastPredictionAt) return;
  if (Date.now() - lastPredictionAt <= PREDICTION_STALE_MS) return;
  if (els.conditionChip) {
    els.conditionChip.className = "pill-badge pill-warn";
    els.conditionChip.textContent = "STALE";
  }
  if (els.primaryFaultTitle) els.primaryFaultTitle.textContent = "Telemetry Stale";
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------
function bindControls() {
  if (els.bannerResetBtn) {
    els.bannerResetBtn.onclick = async () => {
      if (await sendCommand("ON", "MANUAL_RESET")) hideEmergencyBanner();
    };
  }
  if (els.btnEmergencyStop) {
    els.btnEmergencyStop.onclick = () => {
      sendCommand("OFF", "MANUAL_OVERRIDE");
      showEmergencyBanner(
        "MANUAL EMERGENCY CUTOFF ENGAGED",
        "Operator forced immediate motor power disconnect."
      );
    };
  }
  if (els.btnManualReset) {
    els.btnManualReset.onclick = async () => {
      if (await sendCommand("ON", "MANUAL_RESET")) hideEmergencyBanner();
    };
  }
  if (els.btnRelayOn) {
    els.btnRelayOn.onclick = () => sendCommand("ON", "MANUAL_OVERRIDE");
  }

  els.scenarioButtons.forEach((btn) => {
    btn.onclick = async () => {
      const mode = btn.getAttribute("data-mode");
      try {
        const res = await fetch("/api/simulator/mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode }),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.ok) activateScenarioButton(mode);
      } catch (err) {
        console.error("[Cockpit] Simulator mode switch failed:", err);
      }
    };
  });
}

function activateScenarioButton(mode) {
  els.scenarioButtons.forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-mode") === mode);
  });
}

// ---------------------------------------------------------------------------
// Browser sound alerts (window.AudioAlerts from audio-alert.js)
// Pure notification channel: muting/disabling audio never affects ML
// inference, MQTT, relay safety, logging or the visual alert stack.
// ---------------------------------------------------------------------------
let audioControlsBound = false;
let audioFlashTimer = null;
let audioAttentionTimer = null;

function renderAudioStatus(status) {
  if (!status) return;
  const enabled = !!status.enabled;
  if (els.audioStatus) {
    els.audioStatus.textContent = status.message || "Sound: Not enabled";
    els.audioStatus.className = "audio-status audio-status-" + (status.state || "off");
    els.audioStatus.title = status.error || status.message || "";
  }
  if (els.btnEnableSound) {
    els.btnEnableSound.hidden = enabled;
    els.btnEnableSound.disabled = status.state === "unsupported";
    els.btnEnableSound.setAttribute("aria-pressed", String(enabled));
    if (els.btnEnableSoundLabel) {
      els.btnEnableSoundLabel.textContent = status.pending ? "Restore Sound Alerts" : "Enable Sound Alerts";
    }
  }
  if (els.btnTestSound) els.btnTestSound.hidden = !enabled;
  if (els.btnMuteSound) {
    els.btnMuteSound.hidden = !enabled;
    els.btnMuteSound.setAttribute("aria-pressed", String(!!status.muted));
    if (els.btnMuteSoundLabel) els.btnMuteSoundLabel.textContent = status.muted ? "Unmute" : "Mute";
  }
}

// Concise visual confirmation that an anomaly decision produced an audio event
// (always shown; appends "(sound off)" when muted/disabled/unsupported).
function showAudioFlash(alert) {
  if (!els.audioFlash || !alert) return;
  const crit = alert.severity === "CRITICAL";
  const fault = alert.fault && alert.fault !== "ANOMALY" ? " \u2014 " + alert.fault : "";
  els.audioFlash.textContent =
    (crit ? "\u26a0 Critical escalation" : "\u26a0 New anomaly") + fault +
    (alert.audible ? "" : "  (sound off)");
  els.audioFlash.className = "audio-flash is-visible " + (crit ? "flash-crit" : "flash-warn");
  if (audioFlashTimer) clearTimeout(audioFlashTimer);
  audioFlashTimer = setTimeout(() => {
    els.audioFlash.classList.remove("is-visible");
  }, 3500);

  // No sound enabled yet: draw attention to the Enable button (visual path stays on)
  if (!alert.audible && els.btnEnableSound && !els.btnEnableSound.hidden) {
    els.btnEnableSound.classList.add("audio-btn-attention");
    if (audioAttentionTimer) clearTimeout(audioAttentionTimer);
    audioAttentionTimer = setTimeout(() => {
      els.btnEnableSound.classList.remove("audio-btn-attention");
    }, 3500);
  }
}

function bindAudioControls() {
  if (audioControlsBound) return; // never register duplicate handlers/engines
  audioControlsBound = true;

  const engine = window.AudioAlerts;
  if (!engine) {
    if (els.audioStatus) {
      els.audioStatus.textContent = "Sound module unavailable \u2014 visual alerts only";
      els.audioStatus.className = "audio-status audio-status-unsupported";
    }
    if (els.btnEnableSound) els.btnEnableSound.disabled = true;
    console.warn("[Cockpit] audio-alert.js not loaded; visual alerts remain fully functional.");
    return;
  }

  engine.subscribe((event) => {
    if (!event) return;
    if (event.type === "status") renderAudioStatus(event.status);
    else if (event.type === "alert") showAudioFlash(event);
  });
  renderAudioStatus(engine.getStatus());

  if (els.btnEnableSound) {
    els.btnEnableSound.onclick = async () => {
      const res = await engine.enable({ test: true });
      if (!res.ok) console.warn("[Cockpit] Sound enable failed:", res.error);
      renderAudioStatus(engine.getStatus());
    };
  }
  if (els.btnTestSound) {
    els.btnTestSound.onclick = () => {
      if (!engine.playTest()) console.warn("[Cockpit] Test tone was not played.");
    };
  }
  if (els.btnMuteSound) {
    els.btnMuteSound.onclick = () => engine.toggleMute();
  }
}

// ---------------------------------------------------------------------------
// SSE streaming
// ---------------------------------------------------------------------------
function initSSE() {
  const es = new EventSource("/api/stream");

  es.onopen = () => setConnectionStatus(true);
  es.onerror = () => setConnectionStatus(false);

  es.addEventListener("snapshot", (e) => {
    try {
      const data = JSON.parse(e.data);
      // Fresh connection: rebuild the chart from server-side history.
      resetChart();
      if (Array.isArray(data.history)) {
        data.history.forEach((h) => renderSensors(h));
      }
      if (data.sensors && !(Array.isArray(data.history) && data.history.length)) {
        renderSensors(data.sensors);
      }
      if (data.prediction) {
        // Snapshot / SSE reconnect: sync the alert tracker SILENTLY so a page
        // reload never replays an incident that was already active.
        if (window.AudioAlerts) AudioAlerts.seed(data.prediction);
        renderPrediction(data.prediction);
      }
      if (data.status) renderStatus(data.status);
      if (data.sim_state && data.sim_state.mode) {
        activateScenarioButton(data.sim_state.mode);
      }
    } catch (err) {
      console.error("[Cockpit] Snapshot parse failed:", err);
    }
  });

  es.addEventListener("sensors", (e) => {
    try { renderSensors(JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener("prediction", (e) => {
    try {
      const p = JSON.parse(e.data);
      if (window.AudioAlerts) AudioAlerts.handlePrediction(p);
      renderPrediction(p);
    } catch (_) {}
  });
  es.addEventListener("status", (e) => {
    try { renderStatus(JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener("fault_log_entry", (e) => {
    try { appendFaultLog(JSON.parse(e.data)); } catch (_) {}
  });
  es.addEventListener("command", (e) => {
    try {
      const cmd = JSON.parse(e.data);
      if (cmd.relay === "ON" || cmd.relay === "OFF") {
        renderStatus({ relay_state: cmd.relay });
      }
      appendFaultLog({
        time: new Date().toLocaleTimeString(),
        state: "CMD",
        detail: `Relay ${cmd.relay} \u2014 ${cmd.reason || "MANUAL"} (${cmd.source || "dashboard"})`,
      });
    } catch (_) {}
  });
  es.addEventListener("alert", (e) => {
    try {
      const a = JSON.parse(e.data);
      appendFaultLog({
        time: new Date().toLocaleTimeString(),
        state: a.level || "ALERT",
        detail: a.msg || "",
        confidence: a.confidence,
      });
      if (window.AudioAlerts) AudioAlerts.handleAlert(a);
    } catch (_) {}
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function loadFaultLog() {
  try {
    const res = await fetch("/api/fault_log");
    const logs = await res.json();
    if (Array.isArray(logs)) {
      // Newest first from the server; prepend in reverse so oldest lands on top.
      logs.slice().reverse().forEach(appendFaultLog);
    }
  } catch (_) {}
}

initChart();
bindControls();
bindAudioControls();
initSSE();
loadFaultLog();
setInterval(markPredictionStale, 2000);
