"""Feature helpers shared by data collection, training and inference."""

from __future__ import annotations

from typing import Dict

# Canonical feature order. MUST match firmware + config.yaml + training data.
FEATURE_NAMES = ["temperature", "vibration_RMS", "vibration_peak", "current"]

# Mapping from MQTT sensor payload keys -> canonical feature names.
SENSOR_KEY_MAP = {
    "temp": "temperature",
    "vib_rms": "vibration_RMS",
    "vib_peak": "vibration_peak",
    "current": "current",
}

CLASS_LABELS = [
    "NORMAL",
    "OVERLOAD",
    "IMBALANCE",
    "BEARING_INNER",
    "BEARING_OUTER",
    "MISALIGNMENT",
    "OVERHEATING",
    "CRITICAL",
]


def payload_to_features(payload: Dict) -> Dict[str, float]:
    """Convert an MQTT sensor payload into the canonical feature dict.

    Non-numeric or missing values become 0.0 with a one-line warning so a
    single bad field never crashes the inference loop.
    """
    features: Dict[str, float] = {}
    for src, dst in SENSOR_KEY_MAP.items():
        raw = payload.get(src, 0.0)
        try:
            features[dst] = float(raw)
        except (TypeError, ValueError):
            print(f"[features] Non-numeric value for '{src}' ({raw!r}); using 0.0")
            features[dst] = 0.0
    return features


def features_to_vector(features: Dict[str, float]) -> list[float]:
    """Order a feature dict into the canonical model input vector."""
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]