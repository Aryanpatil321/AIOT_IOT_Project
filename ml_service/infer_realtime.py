"""Real-time inference service.

Subscribes to ``machine/sensors``, buffers a sliding window, runs the trained
Random Forest classifier, computes a health score and explanation, then
publishes to ``machine/prediction`` and ``machine/alerts``. On critical faults
(meeting debounce + confidence thresholds) it publishes an OFF command to
``machine/command``.

Usage
-----
    python infer_realtime.py
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path

import joblib
import numpy as np
import paho.mqtt.client as mqtt

from utils.config import ML_SERVICE_DIR, load_config
from utils.diagnosis import DefectDiagnoser
from utils.explain import BaselineTracker, compute_health_score
from utils.features import FEATURE_NAMES, payload_to_features

STATE_MAP = {
    "NORMAL": "NORMAL",
    "IMBALANCE": "WARN",
    "OVERLOAD": "WARN",
    "OVERHEATING": "WARN",
    "BEARING_INNER": "WARN",
    "BEARING_OUTER": "WARN",
    "MISALIGNMENT": "WARN",
    "CRITICAL": "CRITICAL",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time ML inference service.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    return parser.parse_args()


class InferenceService:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model_path = (ML_SERVICE_DIR / cfg["model"]["model_path"]).resolve()
        self.model = joblib.load(self.model_path)
        self.feature_names = cfg["model"]["features"]
        self.classes = list(getattr(self.model, "classes_", cfg["classes"]))

        # Load per-class signal signatures learned at training time; the
        # Defect Diagnosis AI uses them to corroborate the classifier with
        # physical evidence and to generate root-cause + remedy output.
        profiles_path = (ML_SERVICE_DIR / cfg["model"].get(
            "class_profiles_path", "../models/class_profiles.json")).resolve()
        class_profiles = None
        if profiles_path.exists():
            with open(profiles_path, "r", encoding="utf-8") as fh:
                class_profiles = json.load(fh)
            print(f"[Model] Loaded class profiles from {profiles_path}")
        else:
            print(f"[Model] WARNING: {profiles_path} not found - diagnosis falls back to baseline deltas only.")

        # Whether the probabilities are actually calibrated (persisted by
        # train_model.py); the diagnosis payload must not claim otherwise.
        calibrated = True
        fn_path = (ML_SERVICE_DIR / cfg["model"].get(
            "feature_names_path", "../models/feature_names.json")).resolve()
        if fn_path.exists():
            with open(fn_path, "r", encoding="utf-8") as fh:
                calibrated = bool(json.load(fh).get("calibrated", True))

        self.diagnoser = DefectDiagnoser(
            self.model, class_profiles=class_profiles, calibrated=calibrated
        )

        self.baseline = BaselineTracker(
            window_size=cfg["explainability"]["baseline_window_size"]
        )
        # Cold-start seeding: without this the first ~20 samples run against a
        # zero baseline, so even an obvious fault gets "verify the sensors".
        if class_profiles and "NORMAL" in class_profiles:
            normal_profile = class_profiles["NORMAL"]
            seed = {}
            for name in FEATURE_NAMES:
                stats = normal_profile.get(name)
                if isinstance(stats, dict) and "mean" in stats:
                    seed[name] = float(stats["mean"])
            if len(seed) == len(FEATURE_NAMES):
                self.baseline.update(seed)
                print(f"[Model] Baseline seeded from NORMAL class profile: {seed}")
        self.min_delta = cfg["explainability"]["min_delta_percent"]

        self.window = deque(maxlen=cfg["inference"]["window_size"])
        self.critical_streak = 0
        self.warn_streak = 0
        self.last_command_time = 0.0
        self.critical_latched = False
        self.last_prediction_time = 0.0

        self._setup_mqtt()

    def _setup_mqtt(self):
        mqtt_cfg = self.cfg["mqtt"]
        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=mqtt_cfg["client_id"],
            )
        except AttributeError:
            # paho-mqtt 1.x fallback
            self.client = mqtt.Client(client_id=mqtt_cfg["client_id"])
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        # connect_async never raises if the broker is down; loop_forever()
        # retries with backoff, so service startup is order-independent.
        self.client.connect_async(
            mqtt_cfg["broker_host"], mqtt_cfg["broker_port"], mqtt_cfg["keepalive"]
        )

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        sensors_topic = self.cfg["topics"]["sensors"]
        command_topic = self.cfg["topics"]["command"]
        print(f"[MQTT] Connected (rc={rc}). Subscribing to '{sensors_topic}' + '{command_topic}'")
        client.subscribe(sensors_topic, qos=self.cfg["mqtt"].get("qos", 0))
        client.subscribe(command_topic, qos=1)
        # Recover from a previous session's safety latch.
        if self.critical_latched:
            print("[MQTT] Safety latch was set; waiting for MANUAL_RESET before re-arming.")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        print(f"[MQTT] Disconnected (rc={rc}); retrying automatically...")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"[WARN] Bad payload: {exc}")
            return

        # Track MANUAL_RESET commands so the safety latch can re-arm.
        if msg.topic == self.cfg["topics"]["command"]:
            if payload.get("reason") == "MANUAL_RESET" and self.critical_latched:
                self.critical_latched = False
                self.critical_streak = 0
                print("[Safety] Latch cleared by MANUAL_RESET. Re-armed.")
            return

        # One malformed reading must not kill the whole safety service.
        try:
            self.process(payload)
        except Exception as exc:  # noqa: BLE001 - keep the service alive at all costs
            print(f"[WARN] Failed to process payload: {exc}")

    def process(self, payload: dict):
        features = payload_to_features(payload)
        self.window.append(features)

        # Use the mean of the buffered window for a smoother prediction.
        stacked = np.array(
            [[w[name] for name in FEATURE_NAMES] for w in self.window], dtype=float
        )
        vector = stacked.mean(axis=0).reshape(1, -1)
        # Diagnose the SAME signal we classified (window mean, not the raw
        # last sample) so evidence and prediction can never contradict.
        mean_features = {name: float(vector[0, i]) for i, name in enumerate(FEATURE_NAMES)}

        class_probs = self.diagnoser.predict_probs(vector)
        fault = max(class_probs, key=class_probs.get)
        confidence = class_probs[fault]
        state = STATE_MAP.get(fault, "WARN")

        health = compute_health_score(
            class_probs, floor=self.cfg["safety"]["health_floor_min"]
        )

        # Defect Diagnosis AI: calibrated confidence + root-cause reasons +
        # prioritised remedies, corroborated against the rolling baseline.
        diagnosis = self.diagnoser.diagnose(
            features=mean_features,
            class_probs=class_probs,
            baseline=self.baseline.means(),
            state=state,
        )
        explanation = [r["interpretation"] for r in diagnosis["reasons"]]

        self.publish_prediction(state, fault, confidence, health, explanation, class_probs, diagnosis)
        self.evaluate_safety(state, fault, confidence, health, diagnosis)

        # Update baseline only with clearly-normal readings.
        if fault == "NORMAL" and confidence >= self.cfg["safety"]["warn_confidence_min"]:
            self.baseline.update(mean_features)

    def publish_prediction(self, state, fault, confidence, health, explanation, class_probs, diagnosis):
        topic = self.cfg["topics"]["prediction"]
        payload = {
            "ts": int(time.time()),
            "state": state,
            "fault": "NONE" if fault == "NORMAL" else fault,
            "confidence": round(confidence, 4),
            "confidence_band": diagnosis["confidence_band"],
            "health": health,
            "explanation": explanation,
            "deltas": diagnosis["deltas"],
            "diagnosis": diagnosis,
            "class_probs": {k: round(v, 4) for k, v in class_probs.items()},
        }
        self.client.publish(topic, json.dumps(payload))
        flag = "OK " if state == "NORMAL" else ("!! " if state == "CRITICAL" else "?  ")
        print(
            f"[{flag}] {fault:<12} conf={confidence:.2f} ({diagnosis['confidence_band']:<6}) "
            f"health={health:>3} | {diagnosis['primary_cause'][:80]}"
        )

    def evaluate_safety(self, state, fault, confidence, health, diagnosis=None):
        safety = self.cfg["safety"]

        if state == "CRITICAL" and confidence >= safety["critical_confidence_min"]:
            self.critical_streak += 1
        else:
            self.critical_streak = 0

        if state == "WARN" and confidence >= safety["warn_confidence_min"]:
            self.warn_streak += 1
        else:
            self.warn_streak = 0

        # Emit warnings
        if self.warn_streak == safety["warn_consecutive_required"]:
            self.publish_alert(
                "WARN", f"Early fault detected: {fault}", fault, confidence, health, diagnosis
            )

        # Trigger critical cutoff with debounce
        if (
            self.critical_streak >= safety["critical_consecutive_required"]
            and not self.critical_latched
        ):
            self.publish_alert(
                "CRIT", "CRITICAL FAULT detected! Stopping motor.", fault, confidence, health, diagnosis
            )
            self.send_command("OFF", "CRITICAL_FAULT", confidence, fault)
            self.critical_latched = True
            self.critical_streak = 0

    def publish_alert(self, level, msg, fault, confidence, health, diagnosis=None):
        payload = {
            "ts": int(time.time()),
            "level": level,
            "msg": msg,
            "fault": fault,
            "confidence": round(confidence, 4),
            "confidence_band": (diagnosis or {}).get("confidence_band"),
            "primary_cause": (diagnosis or {}).get("primary_cause"),
            "remedies": ((diagnosis or {}).get("remedies") or [])[:3],
            "health": health,
            "persistent": level == "CRIT",
        }
        self.client.publish(self.cfg["topics"]["alerts"], json.dumps(payload))

    def send_command(self, relay, reason, confidence, fault):
        now = time.time()
        cooldown = self.cfg["safety"]["command_cooldown_ms"] / 1000.0
        if (now - self.last_command_time) < cooldown:
            return
        self.last_command_time = now
        payload = {
            "ts": int(now),
            "relay": relay,
            "reason": reason,
            "confidence": round(confidence, 4),
            "fault": fault,
            "source": "ml_auto",
        }
        self.client.publish(self.cfg["topics"]["command"], json.dumps(payload), qos=1)
        print(f"[CMD] relay={relay} reason={reason}")

    def run(self):
        print("[Inference] Running. Ctrl+C to stop.")
        self.client.loop_forever()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    service = InferenceService(cfg)
    try:
        service.run()
    except KeyboardInterrupt:
        print("\n[Inference] Interrupted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())