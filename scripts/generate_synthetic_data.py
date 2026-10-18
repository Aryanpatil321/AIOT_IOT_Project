"""Generate physics-based synthetic training data for AIoT DC Motor Fault Classification.

Produces realistic multi-sensor telemetry for 5 conditions:
- NORMAL: Smooth baseline operation
- IMBALANCE: Eccentric rotor / unbalance (high vibration RMS & peak, nominal current)
- OVERLOAD: High mechanical load / friction (high current, moderate vibration & heat)
- OVERHEATING: Thermal breakdown / lack of cooling (high temperature, nominal vibration/current)
- CRITICAL: Severe compound fault (rotor binding, high current, extreme vibration and heat)

Output: data/raw/synthetic_training_data.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "data" / "raw"
OUTPUT_FILE = OUTPUT_DIR / "synthetic_training_data.csv"

FIELDNAMES = [
    "timestamp",
    "temperature",
    "vibration_RMS",
    "vibration_peak",
    "current",
    "label",
]

SAMPLES_PER_CLASS = 600  # Total 3,000 samples for balanced training set

# Realism knobs: real sensors are noisy and early-stage faults produce readings
# that sit BETWEEN class profiles. Controlled overlap stops the classifier from
# achieving a meaningless 100% accuracy and gives the confidence calibration
# something honest to calibrate.
STD_SCALE = 1.35         # widen each class cloud by this factor
BORDERLINE_FRAC = 0.06   # fraction of samples pulled toward the grand mean

# Class profile parameters: (mean, std_dev, min_val, max_val)
PROFILES = {
    "NORMAL": {
        "temperature": (36.0, 2.5, 28.0, 43.0),
        "vibration_RMS": (0.18, 0.04, 0.10, 0.28),
        "vibration_peak": (0.38, 0.08, 0.22, 0.55),
        "current": (0.48, 0.06, 0.35, 0.65),
    },
    "IMBALANCE": {
        "temperature": (39.0, 3.0, 30.0, 48.0),
        "vibration_RMS": (1.10, 0.18, 0.75, 1.65),
        "vibration_peak": (2.40, 0.45, 1.60, 3.80),
        "current": (0.62, 0.08, 0.45, 0.85),
    },
    "OVERLOAD": {
        "temperature": (56.0, 4.5, 46.0, 68.0),
        "vibration_RMS": (0.45, 0.08, 0.30, 0.70),
        "vibration_peak": (1.05, 0.18, 0.70, 1.50),
        "current": (1.70, 0.22, 1.25, 2.30),
    },
    "OVERHEATING": {
        "temperature": (78.0, 6.0, 66.0, 96.0),
        "vibration_RMS": (0.28, 0.05, 0.15, 0.45),
        "vibration_peak": (0.58, 0.10, 0.35, 0.90),
        "current": (0.68, 0.09, 0.50, 0.95),
    },
    "CRITICAL": {
        "temperature": (85.0, 7.5, 72.0, 108.0),
        "vibration_RMS": (1.80, 0.35, 1.20, 2.90),
        "vibration_peak": (4.10, 0.70, 2.70, 6.20),
        "current": (2.65, 0.38, 1.95, 3.85),
    },
}


def sample_value(mean: float, std: float, min_v: float, max_v: float) -> float:
    val = random.gauss(mean, std)
    return max(min_v, min(max_v, val))


def generate_dataset(std_scale: float = STD_SCALE, borderline_frac: float = BORDERLINE_FRAC):
    random.seed(42)  # deterministic dataset so retraining reproduces the same CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base_ts = int(time.time()) - (len(PROFILES) * SAMPLES_PER_CLASS)

    # Grand mean across all classes: borderline samples drift toward this.
    grand_mean = {
        feat: sum(p[feat][0] for p in PROFILES.values()) / len(PROFILES)
        for feat in ("temperature", "vibration_RMS", "vibration_peak", "current")
    }

    rows = []
    ts = base_ts

    for label, params in PROFILES.items():
        for _ in range(SAMPLES_PER_CLASS):
            borderline = random.random() < borderline_frac
            drift = random.uniform(0.25, 0.6) if borderline else 0.0

            def effective(feat: str) -> tuple[float, float, float, float]:
                mean, std, min_v, max_v = params[feat]
                if drift:
                    mean = mean + (grand_mean[feat] - mean) * drift
                return mean, std * std_scale, min_v, max_v

            temp = sample_value(*effective("temperature"))
            vib_rms = sample_value(*effective("vibration_RMS"))
            # In physical systems, peak is naturally proportional to RMS plus crest noise
            vib_peak = sample_value(*effective("vibration_peak"))
            if vib_peak < vib_rms * 1.5:
                vib_peak = vib_rms * (1.6 + random.random() * 0.8)
            curr = sample_value(*effective("current"))

            rows.append({
                "timestamp": ts,
                "temperature": round(temp, 2),
                "vibration_RMS": round(vib_rms, 4),
                "vibration_peak": round(vib_peak, 4),
                "current": round(curr, 3),
                "label": label,
            })
            ts += 1

    # Shuffle rows so chronological bias doesn't skew cross validation
    random.shuffle(rows)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[Synthetic Data] Successfully generated {len(rows)} samples across {len(PROFILES)} classes.")
    print(f"[Synthetic Data] std_scale={std_scale}, borderline_frac={borderline_frac}")
    print(f"[Synthetic Data] Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic motor telemetry.")
    parser.add_argument("--std-scale", type=float, default=STD_SCALE,
                        help="Multiplier on per-class standard deviations (realism/noise).")
    parser.add_argument("--borderline-frac", type=float, default=BORDERLINE_FRAC,
                        help="Fraction of samples drawn between class profiles.")
    args = parser.parse_args()
    generate_dataset(std_scale=args.std_scale, borderline_frac=args.borderline_frac)
