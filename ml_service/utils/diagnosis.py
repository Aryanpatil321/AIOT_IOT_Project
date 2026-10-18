"""Defect Diagnosis AI: explainable root-cause analysis + recommended remedies.

This module is the top layer of the ML pipeline. It fuses three sources of
evidence into a single, human-readable diagnosis:

1. **Statistical evidence** - the probability-calibrated Random Forest gives
   WHAT is wrong (fault class) and HOW SURE the model is (confidence).
2. **Physical evidence** - each live sensor value is compared against a rolling
   healthy baseline and the learned per-class signal signature to explain WHY
   the classifier made its decision (which signals corroborate it).
3. **Domain knowledge** - a maintenance knowledge base maps each fault class and
   its dominant signal to prioritised, actionable REMEDIES for the operator.

The result is a structured ``diagnosis`` dictionary that is published over MQTT
and rendered on the dashboard:

    {
        "prediction":       "OVERLOAD",
        "state":            "WARN",
        "confidence":       0.93,               # calibrated probability
        "confidence_band":  "HIGH",             # HIGH / MEDIUM / LOW
        "corroborated":     True,               # physical signals agree?
        "primary_cause":    "...",              # one-line root cause
        "reasons":          [ {"feature":..., "value":..., "delta_pct":...,
                               "interpretation": "..."}, ... ],
        "deltas":           {"temperature": 12.4, ...},
        "remedies":         [ {"priority":1, "category":"Mechanical",
                               "action":"..."}, ... ],
    }
"""

from __future__ import annotations

from typing import Dict, List, Optional

FEATURE_UNITS = {
    "temperature": "\u00b0C",
    "vibration_RMS": "g",
    "vibration_peak": "g",
    "current": "A",
}

FRIENDLY_NAMES = {
    "temperature": "Temperature",
    "vibration_RMS": "Vibration RMS",
    "vibration_peak": "Vibration peak",
    "current": "Current draw",
}

# ---------------------------------------------------------------------------
# Physical interpretation of per-feature deviations
# ---------------------------------------------------------------------------
FEATURE_INTERPRETATION = {
    "temperature": {
        "high": "Casing running hot: frictional losses or winding heat are exceeding the motor's cooling capacity.",
        "mild": "Casing warmer than baseline \u2014 early thermal trend; watch for further rise.",
        "low": "Temperature below the normal envelope (light duty, ambient draft or sensor drift).",
    },
    "vibration_RMS": {
        "high": "Sustained high vibration energy: rotor imbalance, bent shaft, worn bearings or loose mounting.",
        "mild": "Vibration energy trending up \u2014 possible early bearing wear or mounting looseness.",
        "low": "Vibration energy is low \u2014 healthy for a balanced rotor.",
    },
    "vibration_peak": {
        "high": "Sharp impact transients (high crest factor): bearing spall/crack, loose coupling or rub.",
        "mild": "Occasional small impacts appearing \u2014 inspect bearings at the next service window.",
        "low": "No impact transients detected \u2014 bearing surfaces appear smooth.",
    },
    "current": {
        "high": "Current draw far above no-load nominal: mechanical binding, heavy load or winding fault.",
        "mild": "Current running a little above nominal \u2014 light over-load or increased friction.",
        "low": "Current below nominal \u2014 very light load or supply/sensor issue.",
    },
}

# ---------------------------------------------------------------------------
# Learned fault signatures: which signals are EXPECTED to deviate, and how
# strongly, for each fault class. Used to corroborate the classifier.
#   "high"      -> expect >= HIGH_DELTA % above baseline
#   "mild_high" -> expect >= MILD_DELTA % above baseline
#   "nominal"   -> expected to stay below MILD_DELTA %
# ---------------------------------------------------------------------------
HIGH_DELTA = 40.0    # % above healthy baseline counts as a strong deviation
MILD_DELTA = 15.0    # % above healthy baseline counts as a mild deviation

CLASS_SIGNATURES = {
    "NORMAL": {
        "temperature": "nominal", "vibration_RMS": "nominal",
        "vibration_peak": "nominal", "current": "nominal",
    },
    "IMBALANCE": {
        "vibration_RMS": "high", "vibration_peak": "high",
        "temperature": "mild_high", "current": "nominal",
    },
    "OVERLOAD": {
        "current": "high", "temperature": "mild_high",
        "vibration_RMS": "mild_high", "vibration_peak": "mild_high",
    },
    "OVERHEATING": {
        "temperature": "high", "current": "mild_high",
        "vibration_RMS": "nominal", "vibration_peak": "nominal",
    },
    # Bearing defects: impulsive (peak-dominated) vibration, moderate RMS.
    "BEARING_INNER": {
        "vibration_peak": "high", "vibration_RMS": "mild_high",
        "temperature": "mild_high", "current": "nominal",
    },
    "BEARING_OUTER": {
        "vibration_peak": "high", "vibration_RMS": "high",
        "temperature": "mild_high", "current": "nominal",
    },
    # Shaft misalignment: raises torque demand + moderate vibration at 1x/2x.
    "MISALIGNMENT": {
        "current": "mild_high", "vibration_RMS": "mild_high",
        "vibration_peak": "mild_high", "temperature": "mild_high",
    },
    "CRITICAL": {
        "temperature": "high", "vibration_RMS": "high",
        "vibration_peak": "high", "current": "high",
    },
}

# ---------------------------------------------------------------------------
# Root-cause statements (domain knowledge, motor condition monitoring)
# ---------------------------------------------------------------------------
PRIMARY_CAUSE = {
    "NORMAL": "No defect detected \u2014 all indicators sit inside the learned healthy envelope.",
    "IMBALANCE": (
        "Mechanical imbalance: the rotor's mass centre no longer coincides with its "
        "axis of rotation, producing vibration that scales with speed."
    ),
    "OVERLOAD": (
        "Excessive mechanical load: the motor must draw far above its rated no-load "
        "current to keep turning, indicating binding, friction or an over-loaded drive."
    ),
    "OVERHEATING": (
        "Thermal fault: heat generation exceeds the motor's dissipation capacity "
        "(blocked ventilation, stalled cooling fan or winding insulation breakdown)."
    ),
    "BEARING_INNER": (
        "Inner-race bearing defect: a spall or pit on the rotating race is struck "
        "by every rolling element, producing periodic impulsive vibration at the "
        "ball-pass frequency of the inner race (BPFI)."
    ),
    "BEARING_OUTER": (
        "Outer-race bearing defect: a spall on the stationary race is struck at a "
        "fixed frequency (BPFO), producing sustained elevated vibration energy."
    ),
    "MISALIGNMENT": (
        "Shaft misalignment: the motor and driven load axes are offset or angularly "
        "misaligned, forcing the coupling to flex every revolution and raising both "
        "vibration and torque demand."
    ),
    "CRITICAL": (
        "Compound failure: simultaneous electrical, mechanical and thermal stress "
        "with imminent burnout risk \u2014 emergency shutdown is warranted."
    ),
}

# Extra physical evidence lines for the most diagnostic feature of each fault.
SUPPORTING_EVIDENCE = {
    ("IMBALANCE", "vibration_RMS"): "Dominant vibration energy is the classic fingerprint of an unbalanced or eccentric rotor.",
    ("IMBALANCE", "vibration_peak"): "High crest-factor impacts indicate the imbalance is exciting mechanical looseness.",
    ("OVERLOAD", "current"): "Elevated current at (near) normal speed points to mechanical resistance rather than an electrical fault.",
    ("OVERLOAD", "temperature"): "Secondary winding heat is consistent with prolonged over-current.",
    ("OVERHEATING", "temperature"): "Temperature far above the thermal envelope with normal vibration isolates the fault to the cooling path.",
    ("OVERHEATING", "current"): "Only mildly elevated current \u2014 heat is being generated by friction/cooling loss, not load.",
    ("BEARING_INNER", "vibration_peak"): "Impulsive crest-factor spikes are the fingerprint of rolling elements striking an inner-race spall (BPFI).",
    ("BEARING_INNER", "vibration_RMS"): "Broadband energy rise is consistent with bearing surfaces no longer running smooth.",
    ("BEARING_OUTER", "vibration_peak"): "Periodic impacts at a fixed rate indicate an outer-race defect (BPFO) as each ball passes the spall.",
    ("BEARING_OUTER", "vibration_RMS"): "Sustained energy rise matches a stationary-race defect excited continuously.",
    ("MISALIGNMENT", "current"): "Higher torque demand from coupling flex raises current even at unchanged load.",
    ("MISALIGNMENT", "vibration_RMS"): "Offset axes excite 1x and 2x running-speed vibration \u2014 the classic misalignment signature.",
    ("CRITICAL", "current"): "Stall-level current combined with heat and vibration indicates seized or binding mechanics.",
    ("CRITICAL", "vibration_RMS"): "Extreme vibration alongside over-current and over-temperature signals advanced mechanical failure.",
}

# ---------------------------------------------------------------------------
# Maintenance knowledge base: prioritised remedies per fault class
# ---------------------------------------------------------------------------
REMEDIES = {
    "NORMAL": [
        {"priority": 1, "category": "Preventive", "action": "Continue routine condition monitoring; no intervention required."},
        {"priority": 2, "category": "Housekeeping", "action": "Keep ventilation slots and the fan shroud free of dust to preserve the thermal margin."},
        {"priority": 3, "category": "Trending", "action": "Watch the vibration RMS trend \u2014 a slow rise is the earliest sign of bearing wear."},
    ],
    "IMBALANCE": [
        {"priority": 1, "category": "Safety", "action": "Reduce speed / shut down and inspect the rotor before continued operation."},
        {"priority": 2, "category": "Mechanical", "action": "Inspect the rotor/armature for eccentricity, bend, or debris buildup; clean or replace."},
        {"priority": 3, "category": "Mechanical", "action": "Field-balance the rotor (trim weights) or replace an eccentric armature."},
        {"priority": 4, "category": "Assembly", "action": "Retighten coupling set-screws, pulley grub screws and mounting bolts; check shaft runout < 0.05 mm."},
        {"priority": 5, "category": "Verification", "action": "Re-run at nominal speed and confirm vibration RMS returns below 0.3 g."},
    ],
    "OVERLOAD": [
        {"priority": 1, "category": "Safety", "action": "Unload or de-energise the motor to prevent winding overheating."},
        {"priority": 2, "category": "Mechanical", "action": "Check for mechanical binding: jammed load, over-tight belt, misaligned coupling or dry bearings."},
        {"priority": 3, "category": "Lubrication", "action": "Inspect and regrease bearings; replace if rough or discoloured."},
        {"priority": 4, "category": "Electrical", "action": "Verify supply voltage and phase balance \u2014 low voltage raises I\u00b2R heating at the same load."},
        {"priority": 5, "category": "Process", "action": "Reduce the driven load or fit a motor one size up if the duty cycle stays above rating."},
    ],
    "OVERHEATING": [
        {"priority": 1, "category": "Safety", "action": "Stop the motor and let it cool below 60 \u00b0C before any restart attempt."},
        {"priority": 2, "category": "Cooling", "action": "Clear blocked ventilation slots and clean the cooling fan and shroud."},
        {"priority": 3, "category": "Cooling", "action": "Verify the fan is actually spinning and the motor is not mounted against a hot surface."},
        {"priority": 4, "category": "Electrical", "action": "Measure winding resistance/insulation \u2014 inter-turn shorts produce heat with normal load."},
        {"priority": 5, "category": "Process", "action": "Lower the ambient heat load or duty-cycle the motor (e.g. 15 min on / 5 min off)."},
    ],
    "BEARING_INNER": [
        {"priority": 1, "category": "Safety", "action": "Reduce speed and plan a bearing replacement \u2014 inner-race defects grow quickly under load."},
        {"priority": 2, "category": "Lubrication", "action": "Check lubrication condition; regrease with the manufacturer-specified grease (over-greasing damages seals)."},
        {"priority": 3, "category": "Mechanical", "action": "Replace the bearing with a correctly specified one; inspect the shaft seat for fretting or ovality before fitting."},
        {"priority": 4, "category": "Assembly", "action": "Verify bearing fit and preload/clearance \u2014 incorrect mounting fits accelerate inner-race damage."},
        {"priority": 5, "category": "Verification", "action": "After replacement, run in and confirm vibration peak returns below the baseline envelope."},
    ],
    "BEARING_OUTER": [
        {"priority": 1, "category": "Safety", "action": "Plan an inspection shutdown \u2014 outer-race defects degrade bearing integrity over hours-to-days."},
        {"priority": 2, "category": "Lubrication", "action": "Inspect grease for metallic particles; flush and regrease."},
        {"priority": 3, "category": "Mechanical", "action": "Replace the bearing; check the housing bore for looseness or fretting that let the outer race move."},
        {"priority": 4, "category": "Assembly", "action": "Ensure the housing fit is correct (light interference) \u2014 a spinning outer race destroys the housing bore."},
        {"priority": 5, "category": "Verification", "action": "Confirm vibration RMS and peak both settle after the replacement run-in."},
    ],
    "MISALIGNMENT": [
        {"priority": 1, "category": "Safety", "action": "Stop and inspect the coupling before continued operation \u2014 misalignment destroys bearings and seals."},
        {"priority": 2, "category": "Mechanical", "action": "Realign the motor-to-load shaft alignment (laser or dial-indicator) to within the coupling tolerance."},
        {"priority": 3, "category": "Assembly", "action": "Check for soft foot (warped mounting surface) and correct it before final alignment."},
        {"priority": 4, "category": "Assembly", "action": "Inspect the coupling element (jaw/spider/insert) for wear caused by the misalignment and replace if damaged."},
        {"priority": 5, "category": "Verification", "action": "Re-run at speed and confirm current and vibration RMS both drop toward nominal."},
    ],
    "CRITICAL": [
        {"priority": 1, "category": "Safety", "action": "KEEP THE RELAY OFF. Do not restart until the fault is physically inspected."},
        {"priority": 2, "category": "Electrical", "action": "Isolate supply and check for a seized rotor (try turning the shaft by hand \u2014 it should spin freely)."},
        {"priority": 3, "category": "Mechanical", "action": "Replace seized or collapsed bearings; check the shaft and coupling for heat discoloration."},
        {"priority": 4, "category": "Electrical", "action": "Test winding continuity and insulation resistance \u2014 burnout is likely once CRITICAL is sustained."},
        {"priority": 5, "category": "Recovery", "action": "After repair, reset the latch, run unloaded and confirm all four signals sit inside the healthy envelope."},
    ],
}

# Sensor-side remedies when the model itself is not confident.
UNCERTAIN_REMEDIES = [
    {"priority": 1, "category": "Sensors", "action": "Verify sensor wiring and supply: a disconnected/shorted sensor produces plausible-looking but false values."},
    {"priority": 2, "category": "Sensors", "action": "Cross-check the readings against a handheld meter (thermometer, clamp meter, vibration probe)."},
    {"priority": 3, "category": "ML", "action": "Recalibrate the sensors or collect labelled data from this machine \u2014 the operating point may be outside the training distribution."},
]

# Feature-specific add-ons when that feature is the dominant deviator.
FEATURE_REMEDIES = {
    "vibration_peak": {"priority": 6, "category": "Bearings",
                       "action": "Listen for bearing rumble with a stethoscope/screwdriver; replace if rough \u2014 high crest factor usually means surface damage."},
    "vibration_RMS": {"priority": 6, "category": "Mounting",
                      "action": "Check foundation/coupling fasteners and isolation feet \u2014 looseness amplifies even a small imbalance."},
    "temperature": {"priority": 6, "category": "Thermal",
                    "action": "Infrared-scan the casing and wiring terminations to localise the hot spot."},
    "current": {"priority": 6, "category": "Electrical",
                "action": "Clamp-check the actual current against the motor nameplate rating."},
}

BAND_HIGH = 0.85
BAND_MEDIUM = 0.65
UNCERTAIN_MAX = 0.50


class DefectDiagnoser:
    """Hybrid model + physics + knowledge-base defect diagnosis engine.

    Parameters
    ----------
    model : fitted classifier exposing ``predict_proba`` (ideally calibrated).
    class_profiles : optional ``{class: {feature: {"mean": m, "std": s}}}``
        learned from the training data; used to say how typical the reading is.
    """

    def __init__(self, model, class_profiles: Optional[Dict] = None,
                 calibrated: bool = True):
        self.model = model
        self.classes = list(getattr(model, "classes_", []))
        self.class_profiles = class_profiles or {}
        self.calibrated = calibrated

    # ------------------------------------------------------------------
    def predict_probs(self, vector) -> Dict[str, float]:
        """Run the calibrated model and return ``{class: probability}``."""
        probs = self.model.predict_proba(vector)[0]
        return {cls: float(p) for cls, p in zip(self.classes, probs)}

    # ------------------------------------------------------------------
    def diagnose(
        self,
        features: Dict[str, float],
        class_probs: Dict[str, float],
        baseline: Dict[str, float],
        state: str = "NORMAL",
    ) -> Dict:
        """Produce a full diagnosis dict for one telemetry window."""
        fault = max(class_probs, key=class_probs.get)
        confidence = float(class_probs[fault])

        deltas = {
            name: _percent_delta(features.get(name, 0.0), baseline.get(name, 0.0))
            for name in FEATURE_UNITS
        }

        corroboration = self._corroborate(fault, deltas)
        band = self._band(confidence, fault, corroboration["fraction"])

        reasons = self._build_reasons(fault, features, baseline, deltas)
        primary_cause = PRIMARY_CAUSE.get(fault, "Unknown condition.")
        if corroboration["dominant"]:
            dominant = corroboration["dominant"]
            primary_cause += (
                f" Dominant evidence: {FRIENDLY_NAMES[dominant]} at "
                f"{features.get(dominant, 0.0):.2f} {FEATURE_UNITS[dominant]} "
                f"({_fmt_delta(deltas[dominant])} vs healthy baseline)."
            )
            key = (fault, dominant)
            if key in SUPPORTING_EVIDENCE:
                reasons.append({"feature": dominant, "value": features.get(dominant, 0.0),
                                "delta_pct": round(deltas[dominant], 1),
                                "interpretation": SUPPORTING_EVIDENCE[key]})

        # Caution note when the statistical and physical evidence disagree.
        if fault != "NORMAL" and corroboration["fraction"] < 0.5:
            reasons.append({
                "feature": "meta",
                "value": confidence,
                "delta_pct": round(corroboration["fraction"] * 100, 1),
                "interpretation": (
                    f"Caution: the classifier is {confidence:.0%} sure of {fault}, but only "
                    f"{corroboration['met']}/{corroboration['expected']} expected signals deviate "
                    "from baseline \u2014 verify the sensors before acting."
                ),
            })

        # Caution note when the probability distribution itself is flat.
        if confidence < UNCERTAIN_MAX:
            reasons.append({
                "feature": "meta",
                "value": confidence,
                "delta_pct": 0.0,
                "interpretation": (
                    f"Model uncertainty is high (best class probability only {confidence:.0%}). "
                    "The reading may reflect a sensor fault or an operating point outside the "
                    "training distribution \u2014 verify instrumentation before trusting this call."
                ),
            })

        remedies = self._build_remedies(fault, corroboration["dominant"], confidence)

        return {
            "prediction": fault,
            "state": state,
            "confidence": round(confidence, 4),
            "confidence_band": band,
            "calibrated": self.calibrated,
            "corroborated": corroboration["fraction"] >= 0.5,
            "primary_cause": primary_cause,
            "reasons": reasons,
            "deltas": {k: round(v, 1) for k, v in deltas.items()},
            "remedies": remedies,
            "class_probs": {k: round(v, 4) for k, v in class_probs.items()},
        }

    # ------------------------------------------------------------------
    def _corroborate(self, fault: str, deltas: Dict[str, float]) -> Dict:
        """Check the live signals against the expected signature of `fault`."""
        signature = CLASS_SIGNATURES.get(fault, {})
        expected = 0
        met = 0
        dominant = None
        dominant_score = 0.0

        for feat, expectation in signature.items():
            delta = deltas.get(feat, 0.0)
            if expectation == "high":
                expected += 1
                if delta >= HIGH_DELTA:
                    met += 1
                score = max(0.0, delta - HIGH_DELTA)
            elif expectation == "mild_high":
                expected += 1
                if delta >= MILD_DELTA:
                    met += 1
                score = max(0.0, delta - MILD_DELTA)
            else:  # nominal
                score = 0.0
                if delta >= HIGH_DELTA:
                    score = -abs(delta)  # contradiction: signal should be flat
            if score > dominant_score:
                dominant_score = score
                dominant = feat

        fraction = (met / expected) if expected else 1.0
        return {"expected": expected, "met": met, "fraction": fraction, "dominant": dominant}

    # ------------------------------------------------------------------
    def _band(self, confidence: float, fault: str, corroboration_fraction: float) -> str:
        # A flat probability distribution is uncertain regardless of which
        # class happens to be the argmax.
        if confidence < UNCERTAIN_MAX:
            return "LOW"
        if confidence >= BAND_HIGH:
            band = "HIGH"
        elif confidence >= BAND_MEDIUM:
            band = "MEDIUM"
        else:
            band = "LOW"
        # Un-corroborated fault calls are never reported as HIGH certainty.
        if fault != "NORMAL" and corroboration_fraction < 0.5 and band == "HIGH":
            band = "MEDIUM"
        return band

    # ------------------------------------------------------------------
    def _build_reasons(
        self,
        fault: str,
        features: Dict[str, float],
        baseline: Dict[str, float],
        deltas: Dict[str, float],
    ) -> List[Dict]:
        """Ranked, human-readable evidence lines for the XAI panel."""
        reasons: List[Dict] = []

        if fault == "NORMAL":
            for name in ("temperature", "vibration_RMS", "vibration_peak", "current"):
                reasons.append({
                    "feature": name,
                    "value": round(features.get(name, 0.0), 3),
                    "delta_pct": round(deltas[name], 1),
                    "interpretation": (
                        f"{FRIENDLY_NAMES[name]} at {features.get(name, 0.0):.2f} "
                        f"{FEATURE_UNITS[name]} is within the healthy envelope "
                        f"({_fmt_delta(deltas[name])} vs baseline)."
                    ),
                })
            return reasons

        # Rank features by how far they exceed the mild-deviation threshold.
        ranked = sorted(
            FEATURE_UNITS.keys(),
            key=lambda name: abs(deltas[name]),
            reverse=True,
        )
        for name in ranked:
            delta = deltas[name]
            if abs(delta) < MILD_DELTA:
                continue
            if delta >= HIGH_DELTA:
                direction = "high"
            elif delta >= MILD_DELTA:
                direction = "mild"
            else:
                direction = "low"
            value = features.get(name, 0.0)
            profile_note = self._profile_note(fault, name, value)
            reasons.append({
                "feature": name,
                "value": round(value, 3),
                "delta_pct": round(delta, 1),
                "interpretation": (
                    f"{FRIENDLY_NAMES[name]} at {value:.2f} {FEATURE_UNITS[name]} is "
                    f"{_fmt_delta(delta)} vs the healthy baseline"
                    f"{profile_note}. {FEATURE_INTERPRETATION[name][direction]}"
                ),
            })

        if not reasons:
            reasons.append({
                "feature": "meta",
                "value": 0.0,
                "delta_pct": 0.0,
                "interpretation": (
                    f"Classifier selected {fault} from the combined multi-sensor pattern; "
                    "no single signal deviates far from baseline."
                ),
            })
        return reasons

    # ------------------------------------------------------------------
    def _profile_note(self, fault: str, feature: str, value: float) -> str:
        """Compare the live value against the learned class profile, if known."""
        profile = (self.class_profiles.get(fault) or {}).get(feature)
        if not profile:
            return ""
        mean = float(profile.get("mean", 0.0))
        if mean <= 0:
            return ""
        return f", close to the typical {fault} signature ({mean:.2f} {FEATURE_UNITS[feature]})"

    # ------------------------------------------------------------------
    def _build_remedies(self, fault: str, dominant: Optional[str], confidence: float) -> List[Dict]:
        if confidence < UNCERTAIN_MAX:
            # The model itself is unsure (flat probabilities): the most likely
            # explanation is a sensor problem or an out-of-distribution
            # operating point, so verification steps come first.
            remedies = [dict(r) for r in UNCERTAIN_REMEDIES]
        else:
            remedies = [dict(r) for r in REMEDIES.get(fault, [])]
            if dominant and fault != "NORMAL" and dominant in FEATURE_REMEDIES:
                add_on = dict(FEATURE_REMEDIES[dominant])
                if not any(r["action"] == add_on["action"] for r in remedies):
                    remedies.append(add_on)
        remedies.sort(key=lambda r: r["priority"])
        return remedies


# ---------------------------------------------------------------------------
def _percent_delta(value: float, base: float, eps: float = 1e-6) -> float:
    if abs(base) < eps:
        return 0.0
    return 100.0 * (value - base) / base


def _fmt_delta(delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f}%"
