"""AIoT Predictive Safety System - Web Dashboard & Control Station.

A Flask application that bridges MQTT telemetry and ML predictions to the browser
via Server-Sent Events (SSE), provides REST endpoints for history, actuation,
and incorporates an embedded Digital Twin simulator engine for hardware-in-the-loop
or pure software demonstrations.

Usage:
    python app.py
Then open http://localhost:5000
"""

from __future__ import annotations

import json
import queue
import random
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration & Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ML_SERVICE_DIR = ROOT_DIR / "ml_service"
CONFIG_PATHS = [
    BASE_DIR / "config.yaml",
    ML_SERVICE_DIR / "config.yaml",
    ML_SERVICE_DIR / "config.yaml.example",
]
MODEL_PATH = ROOT_DIR / "models" / "rf_model.joblib"
FEATURES_PATH = ROOT_DIR / "models" / "feature_names.json"


def load_config() -> dict:
    for path in CONFIG_PATHS:
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {
        "mqtt": {"broker_host": "localhost", "broker_port": 1883, "keepalive": 60, "qos": 0},
        "topics": {
            "sensors": "machine/sensors",
            "prediction": "machine/prediction",
            "command": "machine/command",
            "status": "machine/status",
            "alerts": "machine/alerts",
        },
    }


CONFIG = load_config()
TOPICS = CONFIG.get("topics", {
    "sensors": "machine/sensors",
    "prediction": "machine/prediction",
    "command": "machine/command",
    "status": "machine/status",
    "alerts": "machine/alerts",
})

# ---------------------------------------------------------------------------
# Model Fallback Loader (for standalone dashboard or offline broker mode)
# ---------------------------------------------------------------------------
ML_MODEL = None
CLASS_PROFILES = None

# Share the ML service's utils (feature names, health score, Defect Diagnosis AI)
if ML_SERVICE_DIR.exists():
    sys.path.insert(0, str(ML_SERVICE_DIR))

try:
    import joblib
    if MODEL_PATH.exists():
        ML_MODEL = joblib.load(MODEL_PATH)
        print(f"[Dashboard] Loaded local ML model from {MODEL_PATH}")
    PROFILES_PATH = ROOT_DIR / "models" / "class_profiles.json"
    if PROFILES_PATH.exists():
        with open(PROFILES_PATH, "r", encoding="utf-8") as fh:
            CLASS_PROFILES = json.load(fh)
except Exception as exc:
    print(f"[Dashboard] Notice: Local model loader bypassed: {exc}")

# Import shared explainability + diagnosis engine from the ML service
DIAGNOSEER = None
try:
    from utils.explain import compute_health_score
    from utils.diagnosis import DefectDiagnoser
    if ML_MODEL is not None:
        calibrated = True
        if FEATURES_PATH.exists():
            with open(FEATURES_PATH, "r", encoding="utf-8") as fh:
                calibrated = bool(json.load(fh).get("calibrated", True))
        DIAGNOSEER = DefectDiagnoser(
            ML_MODEL, class_profiles=CLASS_PROFILES, calibrated=calibrated
        )
        print("[Dashboard] Defect Diagnosis AI loaded from ml_service.utils")
except Exception as exc:
    print(f"[Dashboard] Notice: Diagnosis engine import bypassed: {exc}")

FEATURE_NAMES = ["temperature", "vibration_RMS", "vibration_peak", "current"]
CLASS_LABELS = ["NORMAL", "OVERLOAD", "IMBALANCE", "BEARING_INNER", "BEARING_OUTER",
                "MISALIGNMENT", "OVERHEATING", "CRITICAL"]
PENALTIES = {"NORMAL": 0, "IMBALANCE": 15, "OVERLOAD": 15, "OVERHEATING": 20,
             "MISALIGNMENT": 18, "BEARING_INNER": 25, "BEARING_OUTER": 25, "CRITICAL": 40}

# ---------------------------------------------------------------------------
# In-memory State & History
# ---------------------------------------------------------------------------
LATEST = {
    "sensors": {},
    "prediction": {},
    "status": {
        "relay_state": "ON",
        "wifi_rssi": -62,
        "ip_addr": "Online",
        "uptime": int(time.time()),
    },
    "alert": {},
}
HISTORY = deque(maxlen=100)          # rolling telemetry for trend graphs
FAULT_LOG = deque(maxlen=80)         # blackbox fault history
SSE_QUEUES: list[queue.Queue] = []
SSE_LOCK = threading.Lock()

# Rolling baseline tracker for explainability
BASELINE_BUFFER = {f: deque(maxlen=20) for f in FEATURE_NAMES}
CRITICAL_STREAK = 0
RELAY_LATCHED = False
LAST_SENSOR_TS = None  # dedupe key: MQTT + HTTP ingest often deliver the same reading
# Guards LATEST/HISTORY/BASELINE/relay automaton mutations across the paho
# thread, the digital-twin thread and Flask request threads.
STATE_LOCK = threading.Lock()


def broadcast(event_type: str, data: dict) -> None:
    message = {"type": event_type, "data": data, "ts": time.time()}
    with SSE_LOCK:
        for q in list(SSE_QUEUES):
            try:
                q.put_nowait(message)
            except queue.Full:
                pass


def add_fault_log(state: str, detail: str, confidence: float | None = None) -> None:
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "state": state or "INFO",
        "detail": detail or "",
        "confidence": confidence,
    }
    FAULT_LOG.appendleft(entry)
    broadcast("fault_log_entry", entry)


# ---------------------------------------------------------------------------
# Local Inference & Explainability Engine
# ---------------------------------------------------------------------------
DEFAULT_BASELINE = {"temperature": 36.0, "vibration_RMS": 0.18, "vibration_peak": 0.38, "current": 0.48}


def compute_local_inference(sensors: dict) -> dict | None:
    """Run local inference when MQTT inference service is not broadcasting."""
    global CRITICAL_STREAK, RELAY_LATCHED
    if ML_MODEL is None:
        return None

    def _num(key, default):
        try:
            return float(sensors.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    temp = _num("temp", 35.0)
    vib_rms = _num("vib_rms", 0.2)
    vib_peak = _num("vib_peak", 0.4)
    curr = _num("current", 0.5)
    features_dict = {"temperature": temp, "vibration_RMS": vib_rms,
                     "vibration_peak": vib_peak, "current": curr}

    vector = [[temp, vib_rms, vib_peak, curr]]

    if DIAGNOSEER is not None:
        # ---- Defect Diagnosis AI path (calibrated + explainable) ----
        class_probs = DIAGNOSEER.predict_probs(vector)
        fault = max(class_probs, key=class_probs.get)
        confidence = float(class_probs[fault])

        state = "NORMAL"
        if fault == "CRITICAL":
            state = "CRITICAL"
        elif fault != "NORMAL":
            state = "WARN"

        if fault == "NORMAL":
            for f, v in features_dict.items():
                BASELINE_BUFFER[f].append(v)
        baselines = {
            f: (sum(b) / len(b) if b else DEFAULT_BASELINE[f])
            for f, b in BASELINE_BUFFER.items()
        }

        health = compute_health_score(class_probs, floor=5)
        diagnosis = DIAGNOSEER.diagnose(
            features=features_dict,
            class_probs=class_probs,
            baseline=baselines,
            state=state,
        )
        explanation = [r["interpretation"] for r in diagnosis["reasons"]]
        deltas = diagnosis["deltas"]
    else:
        # ---- Legacy path (no diagnosis engine available) ----
        probs = ML_MODEL.predict_proba(vector)[0]
        classes = list(getattr(ML_MODEL, "classes_", CLASS_LABELS))
        class_probs = {cls: float(p) for cls, p in zip(classes, probs)}
        fault = max(class_probs, key=class_probs.get)
        confidence = float(class_probs[fault])

        state = "NORMAL"
        if fault == "CRITICAL":
            state = "CRITICAL"
        elif fault != "NORMAL":
            state = "WARN"

        # Health score
        p_normal = float(class_probs.get("NORMAL", 0.0))
        penalty = sum(PENALTIES.get(c, 0) * p for c, p in class_probs.items())
        health = int(max(5, min(100, round(100.0 * p_normal - penalty))))

        # Baseline & explainability
        if fault == "NORMAL":
            BASELINE_BUFFER["temperature"].append(temp)
            BASELINE_BUFFER["vibration_RMS"].append(vib_rms)
            BASELINE_BUFFER["vibration_peak"].append(vib_peak)
            BASELINE_BUFFER["current"].append(curr)

        baselines = {
            f: (sum(b) / len(b) if b else DEFAULT_BASELINE[f])
            for f, b in BASELINE_BUFFER.items()
        }

        deltas = {}
        explanation = []
        for f in FEATURE_NAMES:
            b_val = baselines[f]
            cur_val = features_dict[f]
            d_pct = ((cur_val - b_val) / max(0.001, b_val)) * 100.0
            deltas[f] = round(d_pct, 1)

        if fault == "NORMAL":
            explanation.append("All physical parameters align with nominal baseline profiles.")
            explanation.append("Zero anomalous harmonic peaks or thermal surges detected.")
        elif fault == "IMBALANCE":
            explanation.append(f"Vibration RMS surged by +{deltas['vibration_RMS']:.0f}% over baseline (eccentric shaft load).")
            explanation.append(f"Vibration peak rose to {vib_peak:.2f}g (crest factor alert).")
        elif fault == "OVERLOAD":
            explanation.append(f"Current draw spiked by +{deltas['current']:.0f}% ({curr:.2f}A vs {baselines['current']:.2f}A nominal).")
            explanation.append("Rotor encountering heavy mechanical resistance or frictional binding.")
        elif fault == "OVERHEATING":
            explanation.append(f"Motor casing temperature rose to {temp:.1f}°C (+{deltas['temperature']:.0f}% above baseline).")
            explanation.append("Thermal threshold breach: potential ventilation stall or winding breakdown.")
        elif fault == "BEARING_INNER":
            explanation.append(f"Impulsive vibration peak reached {vib_peak:.2f}g (+{deltas['vibration_peak']:.0f}% vs baseline) - inner-race spall signature.")
            explanation.append("Rolling elements striking a race defect at BPFI rate.")
        elif fault == "BEARING_OUTER":
            explanation.append(f"Elevated vibration energy (RMS {vib_rms:.2f}g, +{deltas['vibration_RMS']:.0f}% vs baseline) - outer-race defect signature.")
            explanation.append("Fixed-rate impacts as balls pass the outer-race spall (BPFO).")
        elif fault == "MISALIGNMENT":
            explanation.append(f"Current raised to {curr:.2f}A (+{deltas['current']:.0f}%) with elevated vibration - coupling misalignment.")
            explanation.append("Offset axes force the coupling to flex every revolution.")
        elif fault == "CRITICAL":
            explanation.append(f"Severe compound failure: Current at {curr:.2f}A, Temp at {temp:.1f}°C, Vib at {vib_rms:.2f}g.")
            explanation.append("Imminent catastrophic burnout. Emergency safety trip triggered.")
        else:
            explanation.append(f"Classified as {fault} with {confidence:.0%} confidence.")
            explanation.append("Enable the diagnosis engine (ml_service.utils) for root-cause details.")
        # Minimal diagnosis payload so the dashboard card is never empty even
        # when the full diagnosis engine is unavailable.
        diagnosis = {
            "prediction": fault,
            "state": state,
            "confidence": round(confidence, 4),
            "confidence_band": "MEDIUM" if confidence >= 0.6 else "LOW",
            "calibrated": False,
            "primary_cause": explanation[0] if explanation else f"Classified as {fault}.",
            "reasons": [{"feature": f, "value": features_dict[f], "delta_pct": deltas.get(f, 0.0),
                         "interpretation": e} for f, e in zip(FEATURE_NAMES, explanation[:4])],
            "deltas": deltas,
            "remedies": [{"priority": 1, "category": "Verification",
                          "action": "Verify sensor readings and inspect the machine before trusting this offline classification."}],
        }

    pred_payload = {
        "ts": int(time.time()),
        "local": True,  # dashboard-local inference (yields to MQTT ML service)
        "state": state,
        "fault": "NONE" if fault == "NORMAL" else fault,
        "confidence": round(confidence, 4),
        "confidence_band": (diagnosis or {}).get("confidence_band"),
        "health": health,
        "explanation": explanation,
        "diagnosis": diagnosis,
        "class_probs": {k: round(v, 4) for k, v in class_probs.items()},
        "deltas": deltas,
    }

    # Autonomous safety cutoff with 2-consecutive debounce + confidence gate
    # (mirrors ml_service/infer_realtime.py so both paths agree).
    crit_min = float(CONFIG.get("safety", {}).get("critical_confidence_min", 0.80))
    if state == "CRITICAL" and confidence >= crit_min:
        CRITICAL_STREAK += 1
        if CRITICAL_STREAK >= 2 and not RELAY_LATCHED:
            RELAY_LATCHED = True
            add_fault_log("CRITICAL", "AUTOMATIC CUTOFF: Disconnected motor relay due to sustained critical fault", confidence)
            execute_relay_command("OFF", "CRITICAL_FAULT", source="safety_automaton")
    else:
        CRITICAL_STREAK = 0

    return pred_payload


# ---------------------------------------------------------------------------
# MQTT Integration
# ---------------------------------------------------------------------------
def _make_mqtt_client():
    try:
        return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                           client_id="aiot-dashboard")
    except AttributeError:
        return mqtt.Client(client_id="aiot-dashboard")  # paho-mqtt 1.x


mqtt_client = _make_mqtt_client()


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("[Dashboard][MQTT] Connected successfully to broker.")
        for topic in TOPICS.values():
            client.subscribe(topic, qos=CONFIG.get("mqtt", {}).get("qos", 0))
    else:
        print(f"[Dashboard][MQTT] Broker connection returned code: {rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return

    topic = msg.topic
    if topic == TOPICS.get("sensors"):
        try:
            process_incoming_sensor_reading(payload)
        except Exception as exc:  # never kill the paho network thread
            print(f"[Dashboard][MQTT] sensor processing failed: {exc}")
    elif topic == TOPICS.get("prediction"):
        LATEST["prediction"] = payload
        broadcast("prediction", payload)
        state = payload.get("state")
        if state in ("WARN", "CRITICAL"):
            add_fault_log(state, payload.get("fault", ""), payload.get("confidence"))
    elif topic == TOPICS.get("status"):
        LATEST["status"].update(payload)
        broadcast("status", payload)
    elif topic == TOPICS.get("alerts"):
        LATEST["alert"] = payload
        broadcast("alert", payload)
        add_fault_log(payload.get("level", ""), payload.get("msg", ""), payload.get("confidence"))
    elif topic == TOPICS.get("command"):
        # External command (ML auto-cutoff / firmware fallback): keep the
        # relay card and latch in sync with what actually happened on the bus.
        global RELAY_LATCHED
        relay = (payload.get("relay") or "").upper()
        reason = payload.get("reason", "")
        source = payload.get("source", "")
        if relay in ("ON", "OFF"):
            LATEST["status"]["relay_state"] = relay
            if reason == "CRITICAL_FAULT":
                RELAY_LATCHED = True
            elif reason == "MANUAL_RESET":
                RELAY_LATCHED = False
            broadcast("status", dict(LATEST["status"]))
            if source == "ml_auto":
                add_fault_log("ML", f"ML auto-command: relay {relay} ({reason})", payload.get("confidence"))


def process_incoming_sensor_reading(payload: dict) -> None:
    global LAST_SENSOR_TS
    # Dedupe: scripts/simulate_machine.py publishes via MQTT *and* HTTP by
    # default; without this the same second lands in HISTORY twice.
    ts = payload.get("ts")
    if ts is not None and ts == LAST_SENSOR_TS:
        return
    LAST_SENSOR_TS = ts

    LATEST["sensors"] = payload
    sample = {
        "ts": payload.get("ts") or time.time(),
        "temp": payload.get("temp"),
        "vib_rms": payload.get("vib_rms"),
        "vib_peak": payload.get("vib_peak"),
        "current": payload.get("current"),
    }
    HISTORY.append(sample)
    broadcast("sensors", payload)

    # If ML prediction hasn't arrived via MQTT, perform local inference
    # Recompute on every reading when the last prediction came from this local
    # engine (1 Hz updates + fast safety debounce). When a prediction arrived
    # via MQTT from the ML service (no "local" flag), only recompute if stale.
    _pred = LATEST["prediction"]
    _pred_is_local = bool(_pred and _pred.get("local"))
    if _pred_is_local or not _pred or (time.time() - _pred.get("ts", 0)) > 2.5:
        pred = compute_local_inference(payload)
        if pred:
            LATEST["prediction"] = pred
            broadcast("prediction", pred)


def execute_relay_command(state: str, reason: str, source: str = "dashboard") -> dict:
    global RELAY_LATCHED
    state = state.upper()
    if reason == "MANUAL_RESET":
        RELAY_LATCHED = False

    # Safety interlock: while latched (auto-cutoff active) only a MANUAL_RESET
    # may re-energise the relay - never a plain ON/MANUAL_OVERRIDE.
    if state == "ON" and RELAY_LATCHED and reason != "MANUAL_RESET":
        payload = {
            "ts": int(time.time()),
            "relay": "REFUSED",
            "reason": reason,
            "source": source,
            "detail": "Relay latched after critical cutoff. Send MANUAL_RESET first.",
        }
        add_fault_log("SAFETY", "ON refused: relay latched - manual reset required", None)
        return payload

    payload = {
        "ts": int(time.time()),
        "relay": state,
        "reason": reason,
        "source": source,
    }
    LATEST["status"]["relay_state"] = state
    try:
        mqtt_client.publish(TOPICS.get("command", "machine/command"), json.dumps(payload), qos=1)
    except Exception:
        pass

    broadcast("command", payload)
    broadcast("status", dict(LATEST["status"]))  # copy: SSE thread serializes later
    return payload


def start_mqtt():
    mqtt_cfg = CONFIG.get("mqtt", {})
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    try:
        mqtt_client.connect_async(
            mqtt_cfg.get("broker_host", "localhost"),
            mqtt_cfg.get("broker_port", 1883),
            mqtt_cfg.get("keepalive", 60),
        )
        mqtt_client.loop_start()
    except Exception as exc:
        print(f"[Dashboard][MQTT] Local broker offline ({exc}); running in standalone/simulator mode.")


# ---------------------------------------------------------------------------
# Built-In Digital Twin Simulator Worker
# ---------------------------------------------------------------------------
SIM_STATE = {
    "active": True,
    "mode": "NORMAL",
    "temp": 34.0,
}


def digital_twin_loop():
    profiles = {
        "STOPPED": {"temp": 26.0, "v_rms": 0.02, "v_pk": 0.04, "curr": 0.00},
        "NORMAL": {"temp": 36.5, "v_rms": 0.18, "v_pk": 0.38, "curr": 0.48},
        "IMBALANCE": {"temp": 40.0, "v_rms": 1.15, "v_pk": 2.45, "curr": 0.62},
        "OVERLOAD": {"temp": 58.0, "v_rms": 0.46, "v_pk": 1.08, "curr": 1.75},
        "OVERHEATING": {"temp": 82.0, "v_rms": 0.28, "v_pk": 0.60, "curr": 0.70},
        # Bearing faults: impulsive (peak-dominated) vibration. Profiles sit at the
        # real-data class centroids (models/class_profiles.json) so the local model
        # classifies them correctly.
        "BEARING_INNER": {"temp": 29.5, "v_rms": 16.8, "v_pk": 105.0, "curr": 2.33},
        "BEARING_OUTER": {"temp": 31.0, "v_rms": 13.6, "v_pk": 52.0, "curr": 2.47},
        # Misalignment: raised current + moderate vibration.
        "MISALIGNMENT": {"temp": 31.0, "v_rms": 3.5, "v_pk": 13.9, "curr": 2.30},
        "CRITICAL": {"temp": 88.0, "v_rms": 1.85, "v_pk": 4.20, "curr": 2.75},
    }

    while True:
        time.sleep(1.0)
        if not SIM_STATE["active"]:
            continue

        relay_is_on = LATEST["status"].get("relay_state", "ON") == "ON"
        active_condition = SIM_STATE["mode"] if relay_is_on else "STOPPED"
        p = profiles.get(active_condition, profiles["NORMAL"])

        # Thermal inertia (fast enough for a live demo: ~5-8 s to settle)
        target_t = p["temp"]
        alpha = 0.22 if target_t > SIM_STATE["temp"] else 0.35
        SIM_STATE["temp"] += (target_t - SIM_STATE["temp"]) * alpha
        t_val = round(SIM_STATE["temp"] + random.gauss(0, 0.2), 2)

        v_rms = max(0.01, round(random.gauss(p["v_rms"], p["v_rms"] * 0.12), 4))
        v_pk = max(v_rms * 1.5, round(random.gauss(p["v_pk"], p["v_pk"] * 0.12), 4))
        curr = max(0.0, round(random.gauss(p["curr"], max(0.02, p["curr"] * 0.1)), 3))

        reading = {
            "ts": int(time.time()),
            "temp": t_val,
            "vib_rms": v_rms,
            "vib_peak": v_pk,
            "current": curr,
            "sim_condition": active_condition,
        }
        process_incoming_sensor_reading(reading)


# ---------------------------------------------------------------------------
# Routes & API Endpoints
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify({
        "sensors": LATEST["sensors"],
        "prediction": LATEST["prediction"],
        "status": LATEST["status"],
        "sim_state": SIM_STATE,
    })


@app.route("/api/history")
def api_history():
    return jsonify(list(HISTORY))


@app.route("/api/fault_log")
def api_fault_log():
    return jsonify(list(FAULT_LOG))


@app.route("/api/telemetry_ingest", methods=["POST"])
def api_telemetry_ingest():
    data = request.get_json(force=True, silent=True) or {}
    process_incoming_sensor_reading(data)
    return jsonify({"ok": True, "ts": time.time()})


@app.route("/api/simulator/mode", methods=["POST"])
def api_simulator_mode():
    data = request.get_json(force=True, silent=True) or {}
    new_mode = (data.get("mode") or "NORMAL").upper()
    allowed = ("NORMAL", "IMBALANCE", "OVERLOAD", "OVERHEATING",
               "BEARING_INNER", "BEARING_OUTER", "MISALIGNMENT", "CRITICAL", "STOPPED")
    if new_mode in allowed:
        SIM_STATE["mode"] = new_mode
        add_fault_log("SIMULATION", f"Injected fault scenario: {new_mode}", None)
        return jsonify({"ok": True, "mode": new_mode})
    return jsonify({"error": "Unknown mode"}), 400


@app.route("/api/simulator/active", methods=["POST"])
def api_simulator_active():
    """Enable/disable the built-in digital twin.

    Disable it when real ESP32 telemetry is flowing so the two sources never
    mix in the same history/chart/inference stream.
    """
    data = request.get_json(force=True, silent=True) or {}
    SIM_STATE["active"] = bool(data.get("active", not SIM_STATE["active"]))
    state = "ENABLED" if SIM_STATE["active"] else "DISABLED"
    add_fault_log("SIMULATION", f"Digital twin {state}", None)
    return jsonify({"ok": True, "active": SIM_STATE["active"]})


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(force=True, silent=True) or {}
    relay = (data.get("relay") or "OFF").upper()
    reason = data.get("reason", "MANUAL_OVERRIDE")
    payload = execute_relay_command(relay, reason)
    add_fault_log("MANUAL", f"Relay {relay} ({reason})", None)
    return jsonify({"ok": True, "sent": payload})


@app.route("/api/stream")
def api_stream():
    def event_stream():
        q: queue.Queue = queue.Queue(maxsize=120)
        with SSE_LOCK:
            SSE_QUEUES.append(q)
        try:
            # Snapshot
            yield _sse("snapshot", {
                "sensors": LATEST["sensors"],
                "prediction": LATEST["prediction"],
                "status": LATEST["status"],
                "history": list(HISTORY),
                "sim_state": SIM_STATE,
            })
            while True:
                try:
                    message = q.get(timeout=15)
                    yield _sse(message["type"], message["data"])
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with SSE_LOCK:
                if q in SSE_QUEUES:
                    SSE_QUEUES.remove(q)

    return Response(event_stream(), mimetype="text/event-stream")


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def main():
    start_mqtt()
    sim_thread = threading.Thread(target=digital_twin_loop, daemon=True)
    sim_thread.start()
    print("[Dashboard] Server starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)


if __name__ == "__main__":
    main()