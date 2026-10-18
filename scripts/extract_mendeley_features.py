"""Extract 4-feature training windows from the KAIST Mendeley rotating-machine dataset.

Dataset: "Vibration, Acoustic, Temperature, and Motor Current Dataset of Rotating
Machine Under Varying Load Conditions for Fault Diagnosis"
DOI: 10.17632/ztmf3m7h5x.3 (CC BY 4.0)

Files (inside data/external/mendeley/*.zip, downloaded separately):
    vibration.zip    -> <load>Nm_<condition>_<severity>.mat   (vibration, g, 25.6 kHz)
    current_temp.zip -> <load>Nm_<condition>_<severity>.tdms  (temp degC + 3-phase current A)

Conditions -> system class labels (hybrid taxonomy):
    Normal    -> NORMAL
    Unbalance -> IMBALANCE          (rotor unbalance: same physics as DC-motor imbalance)
    BPFI      -> BEARING_INNER      (ball-pass frequency inner race)
    BPFO      -> BEARING_OUTER      (ball-pass frequency outer race)
    Misalign  -> MISALIGNMENT       (parallel shaft misalignment)

Output: data/raw/real_kast_dataset.csv with the canonical schema
    timestamp,temperature,vibration_RMS,vibration_peak,current,label[,load_Nm,severity]
ready for train_model.py (which trains on every CSV in data/raw/).

Usage:
    python scripts/extract_mendeley_features.py
    python scripts/extract_mendeley_features.py --max-windows-per-file 40
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
EXTERNAL_DIR = ROOT_DIR / "data" / "external" / "mendeley"
VIB_ZIP = EXTERNAL_DIR / "vibration.zip"
CUR_ZIP = EXTERNAL_DIR / "current_temp.zip"
OUTPUT_FILE = ROOT_DIR / "data" / "raw" / "real_kast_dataset.csv"

# Dataset condition token -> system class label
CONDITION_MAP = {
    "Normal": "NORMAL",
    "Unbalance": "IMBALANCE",
    "Unbalalnce": "IMBALANCE",  # typo present in some Mendeley filenames
    "BPFI": "BEARING_INNER",
    "BPFO": "BEARING_OUTER",
    "Misalign": "MISALIGNMENT",
}

FILE_RE = re.compile(r"(\d+)Nm_([A-Za-z]+)_?([A-Za-z0-9]*)\.(mat|tdms)$")

FIELDNAMES = ["timestamp", "temperature", "vibration_RMS", "vibration_peak",
              "current", "label", "load_Nm", "severity"]


def parse_name(filename: str):
    """Return (load_Nm, condition, severity) or None."""
    m = FILE_RE.search(filename.replace(" ", ""))
    if not m:
        return None
    load, cond, sev = int(m.group(1)), m.group(2), m.group(3) or "na"
    return load, cond, sev


# ---------------------------------------------------------------------------
# Per-file feature extraction -> {(load, condition, severity): {second: features}}
# ---------------------------------------------------------------------------
def bin_uniform(values: dict[str, np.ndarray], rate_hz: float):
    """Vectorized per-second aggregation of uniformly sampled channels.

    Returns {second_index: {channel: {"mean":..,"rms":..,"peak":..}}} for
    seconds that actually contain samples. Binning by integer second lets us
    join the 25.6 kHz vibration recording with the (differently-clocked) NI
    temperature/current recording safely.
    """
    n = len(next(iter(values.values())))
    if n == 0:
        return {}
    sec = (np.arange(n) / rate_hz).astype(np.int64)
    n_bins = int(sec[-1]) + 1

    out: dict[int, dict] = {}
    stats: dict[str, dict[str, np.ndarray]] = {}
    for name, arr in values.items():
        arr = np.asarray(arr, dtype=float)
        counts = np.bincount(sec, minlength=n_bins)
        sums = np.bincount(sec, weights=arr, minlength=n_bins)
        sqs = np.bincount(sec, weights=arr * arr, minlength=n_bins)
        edges = np.clip(np.searchsorted(sec, np.arange(n_bins + 1)), 0, n - 1)
        peaks = np.maximum.reduceat(np.abs(arr), edges[:-1])
        stats[name] = {"counts": counts, "sums": sums, "sqs": sqs, "peaks": peaks}

    first = next(iter(stats.values()))["counts"] > 0
    for s in np.nonzero(first)[0]:
        out[int(s)] = {
            name: {
                "mean": st["sums"][s] / st["counts"][s],
                "rms": float(np.sqrt(st["sqs"][s] / max(1, st["counts"][s]))),
                "peak": float(st["peaks"][s]),
            }
            for name, st in stats.items()
        }
    return out


def _first_float(v) -> float:
    """Recursively drill into nested MATLAB struct arrays until a scalar."""
    while isinstance(v, np.ndarray):
        v = v.ravel()[0]
    return float(v)


def extract_vibration(zf: zipfile.ZipFile, member: str):
    """Siemens Test.Lab .mat: struct Signal -> y_values.values (N,4) in g.

    Columns: [x_housingA, y_housingA, x_housingB, y_housingB].
    Sampling: x_values.increment (25.6 kHz). We reduce the housing-A x/y pair
    to a single acceleration magnitude channel (matches the triaxial MPU6050).
    """
    from scipy.io import loadmat

    with zf.open(member) as fh:
        data = loadmat(io.BytesIO(fh.read()))
    sig = data["Signal"]          # (1,1) struct array
    xv = sig["x_values"][0, 0]    # (1,1) struct array
    yv = sig["y_values"][0, 0]
    increment = _first_float(xv["increment"])                   # seconds/sample
    vals = np.asarray(yv["values"][0, 0], dtype=float)          # (N, 4)
    mag = np.sqrt(vals[:, 0] ** 2 + vals[:, 1] ** 2)            # housing-A magnitude
    rate_hz = 1.0 / increment
    return bin_uniform({"mag": mag}, rate_hz)


def extract_temp_current(zf: zipfile.ZipFile, member: str):
    """NI cDAQ TDMS: group 'Log' channels Mod1/ai* = thermocouples (degC),
    Mod2/ai* = 3-phase current (A). No time channel: use wf_increment.
    Some files carry only one live current phase (dataset quirk) - handled."""
    from nptdms import TdmsFile

    with zf.open(member) as fh:
        tdms = TdmsFile.read(io.BytesIO(fh.read()))

    temps, currents = [], []
    for g in tdms.groups():
        for c in g.channels():
            if len(c) == 0:
                continue
            if "Mod1" in c.name:
                temps.append(np.asarray(c[:], dtype=float))
            elif "Mod2" in c.name:
                currents.append(np.asarray(c[:], dtype=float))
    if not temps or not currents:
        raise ValueError("missing temperature or current channels")

    ref_len = min(len(a) for a in temps + currents)
    temp = np.mean([a[:ref_len] for a in temps], axis=0)
    # Instantaneous magnitude across the available phase(s)
    cur = np.sqrt(np.mean([a[:ref_len] ** 2 for a in currents], axis=0))

    increment = float(tdms["Log"].channels()[0].properties.get("wf_increment", 3.9e-5))
    rate_hz = 1.0 / increment
    return bin_uniform({"temp": temp, "curr": cur}, rate_hz)


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Extract features from the KAIST Mendeley dataset.")
    parser.add_argument("--max-windows-per-file", type=int, default=60,
                        help="Cap 1-second windows per file (bounds class imbalance and CSV size).")
    args = parser.parse_args()

    for zp in (VIB_ZIP, CUR_ZIP):
        if not zp.exists():
            print(f"[Extract] MISSING: {zp}\n"
                  f"          Download it first (see docs/defect_ai.md or the Mendeley page).")
            return 1

    # Pass 1: vibration features per file
    print("[Extract] Pass 1/2: vibration .mat files ...")
    vib_data = {}
    skipped_unknown = 0
    with zipfile.ZipFile(VIB_ZIP) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".mat")]
        for i, member in enumerate(members, 1):
            meta = parse_name(Path(member).name)
            if meta is None or meta[1] not in CONDITION_MAP:
                skipped_unknown += 1
                print(f"  [{i}/{len(members)}] {Path(member).name}: SKIPPED (unrecognised condition)")
                continue
            try:
                vib_data[meta] = extract_vibration(zf, member)
                print(f"  [{i}/{len(members)}] {Path(member).name}: {len(vib_data[meta])} s")
            except Exception as exc:
                print(f"  [{i}/{len(members)}] {Path(member).name}: SKIPPED ({exc})")

    # Pass 2: temperature/current features per file
    print("[Extract] Pass 2/2: temperature/current .tdms files ...")
    tc_data = {}
    with zipfile.ZipFile(CUR_ZIP) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".tdms")]
        for i, member in enumerate(members, 1):
            meta = parse_name(Path(member).name)
            if meta is None or meta[1] not in CONDITION_MAP:
                skipped_unknown += 1
                print(f"  [{i}/{len(members)}] {Path(member).name}: SKIPPED (unrecognised condition)")
                continue
            try:
                tc_data[meta] = extract_temp_current(zf, member)
                print(f"  [{i}/{len(members)}] {Path(member).name}: {len(tc_data[meta])} s")
            except Exception as exc:
                print(f"  [{i}/{len(members)}] {Path(member).name}: SKIPPED ({exc})")

    # Pass 3: join on (file key, second) and emit rows
    print("[Extract] Pass 3/3: joining and writing CSV ...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    per_class = defaultdict(int)
    ts_base = int(time.time())
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for key in sorted(vib_data.keys()):
            if key not in tc_data:
                continue
            load, cond, sev = key
            label = CONDITION_MAP[cond]
            vib_seconds = vib_data[key]
            tc_seconds = tc_data[key]
            common = sorted(set(vib_seconds) & set(tc_seconds))
            for sec in common[: args.max_windows_per_file]:
                v = vib_seconds[sec]
                t = tc_seconds[sec]
                if "mag" not in v or "temp" not in t or "curr" not in t:
                    continue
                writer.writerow({
                    "timestamp": ts_base + n_rows,
                    "temperature": round(float(t["temp"]["mean"]), 2),
                    "vibration_RMS": round(float(v["mag"]["rms"]), 4),
                    "vibration_peak": round(float(v["mag"]["peak"]), 4),
                    "current": round(float(t["curr"]["rms"]), 3),
                    "label": label,
                    "load_Nm": load,
                    "severity": sev,
                })
                n_rows += 1
                per_class[label] += 1

    print(f"[Extract] Wrote {n_rows} windows -> {OUTPUT_FILE}")
    if skipped_unknown:
        print(f"[Extract] Note: {skipped_unknown} file(s) skipped (unrecognised condition token)")
    print("[Extract] Class distribution:")
    for label, cnt in sorted(per_class.items()):
        print(f"    {label:<16} {cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
