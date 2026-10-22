"""End-to-End System Verification Suite for AIoT Predictive Safety System.

Validates:
1. Trained Random Forest model artifacts and classes
2. Multi-class inference precision across known sensor vectors
3. Explainable AI (XAI) baseline deviations and diagnostic narrative generation
4. Health scoring calculation
5. Dashboard REST API endpoints and state updates
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _ensure_dashboard_deps() -> None:
    """Verify needs Flask (dashboard app), which the ML venv does not ship.

    If the current interpreter lacks Flask, re-run this script with the
    dashboard venv interpreter when available, so the suite works from any
    activated environment.
    """
    try:
        import flask  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    if os.environ.get("AIOT_VERIFY_REEXEC") != "1":
        for candidate in (
            ROOT_DIR / "dashboard" / ".venv" / "Scripts" / "python.exe",
            ROOT_DIR / "dashboard" / ".venv" / "bin" / "python",
        ):
            if candidate.exists() and candidate.resolve() != Path(sys.executable).resolve():
                print(f"[verify] Flask not available in this interpreter - re-running with {candidate}")
                env = dict(os.environ, AIOT_VERIFY_REEXEC="1")
                raise SystemExit(subprocess.call(
                    [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]], env=env
                ))

    print("[verify] Flask is required (it imports the dashboard app).")
    print(r"  Run:  .\dashboard\.venv\Scripts\python.exe scripts\verify_system.py")
    print(r"  Or:   pip install -r dashboard\requirements.txt")
    raise SystemExit(2)


_ensure_dashboard_deps()

sys.path.insert(0, str(ROOT_DIR / "ml_service"))
sys.path.insert(0, str(ROOT_DIR / "dashboard"))

import joblib
from utils.explain import compute_health_score, build_explanation, BaselineTracker
from utils.features import FEATURE_NAMES, payload_to_features
from utils.diagnosis import DefectDiagnoser, UNCERTAIN_REMEDIES
from app import app, LATEST, SIM_STATE, compute_local_inference


class TestAIoTPredictiveSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model_path = ROOT_DIR / "models" / "rf_model.joblib"
        cls.assertTrue(cls.model_path.exists(), "Trained model rf_model.joblib must exist")
        cls.model = joblib.load(cls.model_path)
        cls.client = app.test_client()

    def test_01_model_classes_and_weights(self):
        expected_classes = {"NORMAL", "IMBALANCE", "OVERLOAD", "OVERHEATING", "CRITICAL"}
        actual_classes = set(self.model.classes_)
        # The model must at least cover the core safety classes; it may also
        # include real-dataset classes (BEARING_INNER/OUTER, MISALIGNMENT).
        self.assertTrue(expected_classes.issubset(actual_classes),
                        f"Core classes missing. Expected superset of {expected_classes}, got {actual_classes}")

    def test_02_inference_on_conditions(self):
        # 1. Normal
        normal_vec = [[36.0, 0.18, 0.38, 0.48]]
        pred = self.model.predict(normal_vec)[0]
        self.assertEqual(pred, "NORMAL", f"Expected NORMAL, got {pred}")

        # 2. Imbalance (Vibration dominant)
        imbalance_vec = [[39.0, 1.25, 2.70, 0.60]]
        pred = self.model.predict(imbalance_vec)[0]
        self.assertEqual(pred, "IMBALANCE", f"Expected IMBALANCE, got {pred}")

        # 3. Overload (Current dominant)
        overload_vec = [[56.0, 0.45, 1.05, 1.80]]
        pred = self.model.predict(overload_vec)[0]
        self.assertEqual(pred, "OVERLOAD", f"Expected OVERLOAD, got {pred}")

        # 4. Overheating (Temp dominant)
        overheat_vec = [[82.0, 0.28, 0.58, 0.68]]
        pred = self.model.predict(overheat_vec)[0]
        self.assertEqual(pred, "OVERHEATING", f"Expected OVERHEATING, got {pred}")

        # 5. Critical (Compound failure)
        crit_vec = [[88.0, 1.90, 4.30, 2.80]]
        pred = self.model.predict(crit_vec)[0]
        self.assertEqual(pred, "CRITICAL", f"Expected CRITICAL, got {pred}")

    def test_03_health_score_calculation(self):
        # High confidence normal -> near 100
        normal_probs = {"NORMAL": 0.98, "IMBALANCE": 0.01, "OVERLOAD": 0.01, "OVERHEATING": 0.0, "CRITICAL": 0.0}
        score = compute_health_score(normal_probs)
        self.assertGreaterEqual(score, 90)

        # Critical fault -> low score
        crit_probs = {"NORMAL": 0.02, "IMBALANCE": 0.05, "OVERLOAD": 0.05, "OVERHEATING": 0.08, "CRITICAL": 0.80}
        score = compute_health_score(crit_probs)
        self.assertLessEqual(score, 30)

    def test_04_explainability_narratives(self):
        baseline = {"temperature": 36.0, "vibration_RMS": 0.18, "vibration_peak": 0.38, "current": 0.48}
        imbalance_features = {"temperature": 38.0, "vibration_RMS": 1.20, "vibration_peak": 2.50, "current": 0.60}
        probs = {"IMBALANCE": 0.95, "NORMAL": 0.05}
        
        explanation = build_explanation(
            features=imbalance_features,
            baseline=baseline,
            class_probs=probs,
            fault="IMBALANCE",
        )
        self.assertTrue(any("Vibration" in line for line in explanation), "Explanation should highlight vibration")

    def test_05_dashboard_api(self):
        # Index route
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AIoT Predictive Safety", resp.data)

        # State route
        resp = self.client.get("/api/state")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)
        self.assertIn("sensors", data)

        # Command route (Relay OFF)
        resp = self.client.post("/api/command", json={"relay": "OFF", "reason": "TEST_CUTOFF"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LATEST["status"]["relay_state"], "OFF")

        # Command route (Relay ON)
        resp = self.client.post("/api/command", json={"relay": "ON", "reason": "MANUAL_RESET"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LATEST["status"]["relay_state"], "ON")

        # Simulator mode route
        resp = self.client.post("/api/simulator/mode", json={"mode": "OVERLOAD"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SIM_STATE["mode"], "OVERLOAD")

    # ------------------------------------------------------------------
    # Defect Diagnosis AI (root cause + confidence + remedies)
    # ------------------------------------------------------------------
    @classmethod
    def _diagnoser(cls):
        profiles = ROOT_DIR / "models" / "class_profiles.json"
        class_profiles = None
        if profiles.exists():
            with open(profiles, "r", encoding="utf-8") as fh:
                class_profiles = json.load(fh)
        return DefectDiagnoser(cls.model, class_profiles=class_profiles)

    def test_06_diagnosis_imbalance_root_cause_and_remedies(self):
        diagnoser = self._diagnoser()
        baseline = {"temperature": 36.0, "vibration_RMS": 0.18, "vibration_peak": 0.38, "current": 0.48}
        features = {"temperature": 38.0, "vibration_RMS": 1.20, "vibration_peak": 2.50, "current": 0.60}

        vector = [[features[f] for f in FEATURE_NAMES]]
        probs = diagnoser.predict_probs(vector)
        diag = diagnoser.diagnose(features, probs, baseline, state="WARN")

        self.assertEqual(diag["prediction"], "IMBALANCE")
        self.assertIn(diag["confidence_band"], {"HIGH", "MEDIUM", "LOW"})
        self.assertGreaterEqual(diag["confidence"], 0.5)
        self.assertTrue(diag["primary_cause"], "Diagnosis must state a root cause")
        self.assertTrue(diag["reasons"], "Diagnosis must include evidence reasons")
        self.assertTrue(
            any("ibration" in r["interpretation"] for r in diag["reasons"]),
            "Reasons should highlight the vibration evidence",
        )
        self.assertTrue(diag["remedies"], "Diagnosis must suggest remedies")
        priorities = [r["priority"] for r in diag["remedies"]]
        self.assertEqual(priorities, sorted(priorities), "Remedies must be priority-ordered")
        self.assertTrue(all("action" in r for r in diag["remedies"]))

    def test_07_diagnosis_critical_flagged_high_severity(self):
        diagnoser = self._diagnoser()
        baseline = {"temperature": 36.0, "vibration_RMS": 0.18, "vibration_peak": 0.38, "current": 0.48}
        features = {"temperature": 88.0, "vibration_RMS": 1.90, "vibration_peak": 4.30, "current": 2.80}

        vector = [[features[f] for f in FEATURE_NAMES]]
        probs = diagnoser.predict_probs(vector)
        diag = diagnoser.diagnose(features, probs, baseline, state="CRITICAL")

        self.assertEqual(diag["prediction"], "CRITICAL")
        self.assertTrue(diag["corroborated"], "Critical signals should corroborate the classifier")
        self.assertEqual(diag["remedies"][0]["category"], "Safety")
        self.assertIn("KEEP THE RELAY OFF", diag["remedies"][0]["action"])

    def test_08_diagnosis_uncertain_when_model_not_confident(self):
        diagnoser = self._diagnoser()
        baseline = {"temperature": 36.0, "vibration_RMS": 0.18, "vibration_peak": 0.38, "current": 0.48}
        features = {"temperature": 50.0, "vibration_RMS": 0.5, "vibration_peak": 1.0, "current": 1.0}

        # Flat / unsure probabilities must never be reported as HIGH confidence
        flat_probs = {"NORMAL": 0.4, "IMBALANCE": 0.2, "OVERLOAD": 0.2,
                      "OVERHEATING": 0.1, "CRITICAL": 0.1}
        diag = diagnoser.diagnose(features, flat_probs, baseline, state="WARN")
        self.assertIn(diag["confidence_band"], {"LOW", "MEDIUM"})
        self.assertTrue(
            any(r["category"] == "Sensors" for r in diag["remedies"]),
            "Uncertain diagnoses must recommend sensor verification first",
        )

    def test_09_local_inference_emits_diagnosis(self):
        reading = {"temp": 40.0, "vib_rms": 1.2, "vib_peak": 2.6, "current": 0.6}
        pred = compute_local_inference(reading)
        if pred is None:
            self.skipTest("Local model unavailable")
        self.assertIn("diagnosis", pred)
        self.assertIn("confidence_band", pred)
        self.assertTrue(pred["diagnosis"]["primary_cause"])
        self.assertTrue(pred["diagnosis"]["remedies"])


if __name__ == "__main__":
    unittest.main()
