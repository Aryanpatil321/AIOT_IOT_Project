"""Health-score and human-readable explanation generation.

The explanation layer is the key academic differentiator of this project: it
turns a raw classification into a "why did the AI flag this?" narrative based
on per-feature deviations from a rolling NORMAL baseline.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List

from .features import FEATURE_NAMES

# Penalty applied to the health score for each non-normal class.
CLASS_HEALTH_PENALTY = {
    "NORMAL": 0,
    "IMBALANCE": 15,
    "OVERLOAD": 15,
    "OVERHEATING": 20,
    "MISALIGNMENT": 18,
    "BEARING_INNER": 25,
    "BEARING_OUTER": 25,
    "CRITICAL": 40,
}

FRIENDLY_NAMES = {
    "temperature": "Temperature",
    "vibration_RMS": "Vibration RMS",
    "vibration_peak": "Vibration peak",
    "current": "Current",
}


class BaselineTracker:
    """Rolling baseline of NORMAL-operation feature values.

    Used to compute percentage deviations for the explanation panel.
    """

    def __init__(self, window_size: int = 20):
        self.window_size = window_size
        self._buffers: Dict[str, Deque[float]] = {
            name: deque(maxlen=window_size) for name in FEATURE_NAMES
        }

    def update(self, features: Dict[str, float]) -> None:
        for name in FEATURE_NAMES:
            self._buffers[name].append(float(features.get(name, 0.0)))

    def ready(self) -> bool:
        return all(len(buf) >= self.window_size for buf in self._buffers.values())

    def means(self) -> Dict[str, float]:
        means: Dict[str, float] = {}
        for name, buf in self._buffers.items():
            if buf:
                means[name] = sum(buf) / len(buf)
            else:
                means[name] = 0.0
        return means


def compute_health_score(
    class_probs: Dict[str, float],
    feature_importances: Dict[str, float] | None = None,
    floor: int = 5,
) -> int:
    """Compute a 0-100 health score from class probabilities.

    The score is dominated by P(NORMAL) but is penalised by the probability
    mass sitting on fault classes weighted by their severity.
    """
    p_normal = float(class_probs.get("NORMAL", 0.0))
    penalty = 0.0
    for cls, prob in class_probs.items():
        penalty += CLASS_HEALTH_PENALTY.get(cls, 0) * float(prob)
    score = 100.0 * p_normal - penalty
    return int(max(floor, min(100, round(score))))


def build_explanation(
    features: Dict[str, float],
    baseline: Dict[str, float],
    class_probs: Dict[str, float],
    fault: str,
    min_delta_percent: float = 5.0,
) -> List[str]:
    """Build human-readable explanation strings for the dashboard panel."""
    lines: List[str] = []

    if fault == "NORMAL":
        for name in FEATURE_NAMES:
            delta = _percent_delta(features.get(name, 0.0), baseline.get(name, 0.0))
            lines.append(f"{FRIENDLY_NAMES[name]} near baseline ({_fmt_delta(delta)})")
        lines.append("Combined pattern matches learned healthy state")
        return lines

    # Fault case: highlight the dominant deviating features.
    deltas = [
        (name, _percent_delta(features.get(name, 0.0), baseline.get(name, 0.0)))
        for name in FEATURE_NAMES
    ]
    deltas.sort(key=lambda kv: abs(kv[1]), reverse=True)

    for name, delta in deltas:
        if abs(delta) >= min_delta_percent:
            lines.append(f"{FRIENDLY_NAMES[name]} {_fmt_delta(delta)} vs. baseline")

    top = deltas[0][0] if deltas else "combined signals"
    lines.append(
        f"Dominant contributor: {FRIENDLY_NAMES.get(top, top)} "
        f"({class_probs.get(fault, 0.0):.0%} confidence on {fault})"
    )
    return lines


def _percent_delta(value: float, base: float, eps: float = 1e-6) -> float:
    if abs(base) < eps:
        return 0.0
    return 100.0 * (value - base) / base


def _fmt_delta(delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f}%"