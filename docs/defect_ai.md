# Defect Diagnosis AI — Root Cause, Confidence & Remedies

This document describes the top layer of the ML pipeline: the **Defect Diagnosis
AI**. It is what turns a raw class label (`OVERLOAD`) into something an operator
can actually act on:

> *"Excessive mechanical load: the motor must draw far above its rated no-load
> current… Dominant evidence: Current draw at 1.85 A (+285% vs healthy
> baseline). Confidence 99% (HIGH). Priority 1: Unload or de-energise the
> motor."*

Source: [`ml_service/utils/diagnosis.py`](../ml_service/utils/diagnosis.py)

---

## 1. Why three layers, not one?

A single classifier answers **WHAT** is wrong, but not **WHY** or **WHAT TO DO**.
The diagnosis engine fuses three independent sources of evidence so that each
output is trustworthy:

| Layer | Source | Answers |
|---|---|---|
| **Statistical** | Probability-calibrated Random Forest | *What* is wrong + *how sure* |
| **Physical** | Live signals vs. rolling healthy baseline + learned per-class signature | *Why* the classifier decided that (corroboration) |
| **Domain knowledge** | Maintenance knowledge base | *How to fix it* (prioritised remedies) |

Because the three layers are independent, they can **disagree** — and when they
do, the engine says so instead of hiding it (see §4).

---

## 2. Confidence you can trust (probability calibration)

Raw Random-Forest vote fractions are famously **over-confident**: a forest can
report "99% sure" while being wrong much more often than that. At inference time
that number feeds a **safety decision** (auto-cutoff), so it must mean something.

`train_model.py` therefore wraps the forest in a
[`CalibratedClassifierCV`](https://scikit-learn.org/stable/modules/calibration.html)
with a **sigmoid (Platt) calibration**, fitted over `calibration_cv` folds. The
goal: *when the model says "90% confident", it should be right ~90% of the time.*

Calibration quality is measured at training time and written to
`models/metrics.json` under `calibration`:

- **Log loss** — penalises confidently-wrong predictions.
- **Brier score** — mean squared error between probability and outcome.
- **Expected Calibration Error (ECE)** — mean |confidence − accuracy| across
  10 confidence bins. **ECE ≈ 0 is the target.**

Current run (`models/metrics.json`):

```json
"calibration": {
  "calibrated": true, "method": "sigmoid", "cv": 5,
  "log_loss": 0.0104, "brier_score": 0.0001,
  "expected_calibration_error": 0.0104
}
```

An ECE of ~0.01 means reported confidences track real accuracy to within ~1%.

### Confidence bands

For operators, a raw probability is noisy UI. The engine maps calibrated
confidence to a stable band:

| Band | Confidence | Meaning |
|---|---|---|
| **HIGH** | ≥ 85 % | Act on this diagnosis. |
| **MEDIUM** | 65 – 85 % | Plausible; corroborate before major action. |
| **LOW** | < 65 % | Do **not** trust blindly — see §4. |

---

## 3. Root cause: physical corroboration

The classifier's decision is cross-checked against physics. Each fault class has
a **signal signature** describing which channels *should* deviate and how strongly
(`CLASS_SIGNATURES`), measured against the **rolling healthy baseline** (a
20-sample window of confirmed-normal readings, so the machine self-calibrates to
its own environment).

Deviation tiers (percent above baseline):

- **high** — ≥ 40 % (`HIGH_DELTA`)
- **mild** — ≥ 15 % (`MILD_DELTA`)
- **nominal** — expected flat

The engine then reports a ranked **reasons** list. Each entry carries the raw
value, the % delta, the learned class-typical value, and a physical
interpretation, e.g.:

> *Vibration RMS at 1.25 g is +594% vs the healthy baseline, close to the
> typical IMBALANCE signature (1.10 g). Sustained high vibration energy: rotor
> imbalance, bent shaft, worn bearings or loose mounting.*

The single most explanatory channel becomes the **dominant evidence**, appended
to the one-line `primary_cause`.

---

## 4. Honest uncertainty: when the layers disagree

Two caution paths are built in — a safety system must never fake confidence.

1. **Un-corroborated fault** — the classifier says `OVERLOAD` at 90 %, but no
   expected signal actually deviates. `corroborated` is set to `false`, the band
   is capped at **MEDIUM**, and a caution reason is emitted:

   > *Caution: the classifier is 90% sure of OVERLOAD, but only 0/4 expected
   > signals deviate from baseline — verify the sensors before acting.*

2. **Flat probabilities** — best class probability < 50 % (`UNCERTAIN_MAX`), the
   reading is outside the training distribution or a sensor is faulty. Band is
   forced to **LOW**, and the remedy list is **replaced** by sensor-verification
   steps (`UNCERTAIN_REMEDIES`) rather than machine-repair steps — because at
   that point a wiring fault is more likely than five simultaneous motor faults.

---

## 5. Remedies: a maintenance knowledge base

`REMEDIES` is a curated, **priority-ordered** action list per fault class, each
tagged with a category (`Safety`, `Mechanical`, `Electrical`, `Cooling`,
`Lubrication`, `Process`, `Verification`, `Recovery`…). Example for `CRITICAL`:

| P | Category | Action |
|---|---|---|
| 1 | Safety | KEEP THE RELAY OFF. Do not restart until physically inspected. |
| 2 | Electrical | Isolate supply; check for a seized rotor. |
| 3 | Mechanical | Replace seized bearings; check shaft for heat discoloration. |

Two dynamic adjustments:

- **Feature add-ons** — if `vibration_peak` is the dominant deviator, a
  bearing-inspection remedy is appended (`FEATURE_REMEDIES`); if `temperature`
  dominates, an IR hot-spot scan is added, etc.
- **Uncertainty override** — as described in §4, unsure calls prioritise sensor
  verification over mechanical intervention.

The top 3 remedies are also attached to MQTT **alerts** (`machine/alerts`) so the
first responder sees them without opening the dashboard.

---

## 6. Where it runs

The same `DefectDiagnoser` class is used in two places, so behaviour is identical:

- **`ml_service/infer_realtime.py`** — the primary MQTT inference service.
  Publishes the full `diagnosis` object inside the `machine/prediction` payload.
- **`dashboard/app.py`** — the local fallback inference used when the ML service
  is offline (keeps the dashboard explanatory even standalone).

### Prediction payload (excerpt)

```json
{
  "state": "WARN",
  "fault": "OVERLOAD",
  "confidence": 0.9897,
  "confidence_band": "HIGH",
  "health": 61,
  "diagnosis": {
    "primary_cause": "Excessive mechanical load... Dominant evidence: Current draw at 1.85 A (+285%).",
    "corroborated": true,
    "reasons": [ { "feature": "current", "value": 1.85, "delta_pct": 285.4, "interpretation": "..." } ],
    "remedies": [ { "priority": 1, "category": "Safety", "action": "Unload or de-energise the motor..." } ]
  },
  "class_probs": { "NORMAL": 0.001, "OVERLOAD": 0.989, "...": 0 }
}
```

---

## 7. Dashboard

The **"AI Defect Diagnosis & Recommended Remedies"** card renders:

- a **confidence band** chip (`HIGH` / `MEDIUM` / `LOW`, colour-coded),
- the one-line **root cause**,
- the calibrated **confidence bar**,
- the ranked **evidence list** (why), and
- the **prioritised remedy list** (what to do).

The engine is UI-agnostic — anything consuming `machine/prediction` over MQTT gets
the same structured JSON.

---

## 8. Tests

`scripts/verify_system.py` covers the engine:

- `test_06` — imbalance produces a root cause, vibration evidence, ordered remedies.
- `test_07` — critical is corroborated and leads with a `Safety` "KEEP THE RELAY OFF" remedy.
- `test_08` — flat probabilities yield a LOW band and sensor-verification remedies.
- `test_09` — the dashboard's local inference emits a `diagnosis` object.

Run: `python scripts/verify_system.py`

---

## 9. Real-machine training data (KAIST Mendeley dataset)

The model is trained on a **hybrid union** of synthetic windows and real
rotating-machine measurements from the public dataset:

> *"Vibration, Acoustic, Temperature, and Motor Current Dataset of Rotating
> Machine Under Varying Load Conditions for Fault Diagnosis"* —
> Mendeley Data, DOI [10.17632/ztmf3m7h5x.3](https://data.mendeley.com/datasets/ztmf3m7h5x/3) (CC BY 4.0)

| File | Content | Size |
|---|---|---|
| `vibration.zip` | 4-channel accelerometer, 25.6 kHz `.mat` (Siemens Test.Lab) | 2.5 GB |
| `current_temp.zip` | 2 thermocouples + 3-phase current `.tdms` (NI cDAQ) | 1.5 GB |

**Pipeline** (`scripts/extract_mendeley_features.py`):

1. Streams `.mat` / `.tdms` directly out of the zips (no full extraction).
2. Reduces each recording to the canonical 4 features in **1-second windows**:
   - `vibration_RMS`, `vibration_peak` — magnitude of housing-A X/Y acceleration (g)
   - `temperature` — mean of the two thermocouples (°C)
   - `current` — RMS across available phase channels (A)
3. Joins vibration and temperature/current channels on integer-second bins
   (they run on different clocks), writes `data/raw/real_kast_dataset.csv`.

**Label mapping** (dataset condition → system class, hybrid taxonomy):

| Dataset | System class | Origin |
|---|---|---|
| `Normal` | `NORMAL` | real + synthetic |
| `Unbalance` (5 severities) | `IMBALANCE` | real + synthetic |
| `BPFI` (inner race, 3 severities) | `BEARING_INNER` | real only |
| `BPFO` (outer race, 3 severities) | `BEARING_OUTER` | real only |
| `Misalign` (3 severities) | `MISALIGNMENT` | real only |
| — | `OVERLOAD`, `OVERHEATING`, `CRITICAL` | synthetic only |

All five operating conditions run at 3 loads (0/2/4 Nm) for diversity.
Current model: **2,400 real + 3,000 synthetic windows, 8 classes**,
accuracy 1.00 on hold-out, all 9 verification tests passing.

Retrain after changing data:

```powershell
python scripts\extract_mendeley_features.py   # regenerate real CSV (once)
python ml_service\train_model.py              # trains on every CSV in data\raw\
python scripts\verify_system.py               # full test suite
```

---

## 10. Extending the knowledge base

The remedies and signatures are plain data at the top of `diagnosis.py`, so they
can be tuned without touching model code:

- Add a fault class → add entries to `CLASS_SIGNATURES`, `PRIMARY_CAUSE`,
  `REMEDIES` (and retrain with the new label).
- Tune sensitivity → adjust `HIGH_DELTA` / `MILD_DELTA`.
- Tune trust thresholds → adjust `BAND_HIGH`, `BAND_MEDIUM`, `UNCERTAIN_MAX`.

When real hardware data is collected (`collect_data.py`), `class_profiles.json`
is regenerated from it, so the "typical signature" comparisons drift from the
synthetic profiles toward this specific machine.
