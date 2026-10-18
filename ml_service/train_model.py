"""Train the Random Forest fault classifier with probability calibration.

Loads all CSVs from ``data/raw``, combines them, trains a RandomForestClassifier,
wraps it in a sigmoid probability calibrator (so reported confidences are
trustworthy), evaluates on a held-out test split, and persists:

    models/rf_model.joblib       calibrated classifier
    models/feature_names.json    feature order + classes + importances
    models/class_profiles.json   per-class signal signatures (used by the
                                 Defect Diagnosis AI for root-cause analysis)
    models/metrics.json          accuracy, per-class report, calibration quality

Usage
-----
    python train_model.py
    python train_model.py --config config.yaml
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    log_loss,
)
from sklearn.model_selection import train_test_split

from utils.config import ML_SERVICE_DIR, load_config
from utils.features import FEATURE_NAMES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Random Forest classifier.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument(
        "--test-size", type=float, default=0.2, help="Fraction for test split."
    )
    return parser.parse_args()


def load_dataset(raw_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(raw_dir / "*.csv")))
    if not files:
        raise FileNotFoundError(f"No CSV files found in {raw_dir}")

    frames = [pd.read_csv(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    print(f"[Train] Loaded {len(df)} rows from {len(files)} file(s).")
    return df


def expected_calibration_error(
    y_true, y_proba, class_list, n_bins: int = 10
) -> float:
    """Expected Calibration Error: mean |confidence - accuracy| over bins.

    ECE ~ 0 means when the model says "90% sure" it is right ~90% of the time.
    """
    conf = y_proba.max(axis=1)
    preds = np.asarray(class_list)[y_proba.argmax(axis=1)]
    correct = (preds == np.asarray(y_true)).astype(float)

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf > lo) & (conf <= hi)
        if mask.any():
            ece += (mask.mean() * abs(correct[mask].mean() - conf[mask].mean()))
    return float(ece)


def _extract_forest(clf):
    """Return the underlying RandomForest from a CalibratedClassifierCV."""
    try:
        return clf.calibrated_classifiers_[0].estimator
    except (AttributeError, IndexError):
        return getattr(clf, "estimator", None)


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    raw_dir = (ML_SERVICE_DIR / cfg["data_collection"]["raw_data_dir"]).resolve()
    model_path = (ML_SERVICE_DIR / cfg["model"]["model_path"]).resolve()
    feature_names_path = (ML_SERVICE_DIR / cfg["model"]["feature_names_path"]).resolve()
    metrics_path = (ML_SERVICE_DIR / cfg["model"]["metrics_path"]).resolve()
    profiles_path = (ML_SERVICE_DIR / cfg["model"].get(
        "class_profiles_path", "../models/class_profiles.json")).resolve()
    model_path.parent.mkdir(parents=True, exist_ok=True)

    df = load_dataset(raw_dir)

    # Drop unlabeled rows (NaN labels become the literal string "nan" via
    # astype(str), so filter them out explicitly).
    df = df[df["label"].notna()]
    df = df[~df["label"].astype(str).str.strip().str.lower().isin(("", "nan", "none"))]
    if df.empty:
        raise ValueError("No labelled rows found. Collect data with --label.")

    missing = [c for c in FEATURE_NAMES if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset missing feature columns: {missing}")

    X = df[FEATURE_NAMES].astype(float).to_numpy()
    y = df["label"].astype(str).to_numpy()

    classes, counts = np.unique(y, return_counts=True)
    print("[Train] Class distribution:")
    for cls, cnt in zip(classes, counts):
        print(f"    {cls:<12} {cnt}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=cfg["model"]["random_state"],
        stratify=y if min(counts) >= 2 else None,
    )

    base_clf = RandomForestClassifier(
        n_estimators=cfg["model"]["n_estimators"],
        max_depth=cfg["model"]["max_depth"],
        min_samples_split=cfg["model"]["min_samples_split"],
        min_samples_leaf=cfg["model"]["min_samples_leaf"],
        random_state=cfg["model"]["random_state"],
        class_weight="balanced",
        n_jobs=-1,
    )

    # --- Probability calibration -------------------------------------------
    # Raw RandomForest votes are often over-confident. Wrapping the forest in a
    # sigmoid (Platt) calibrator makes the confidence values published at
    # inference time trustworthy probabilities, which the Defect Diagnosis AI
    # bands into HIGH / MEDIUM / LOW certainty.
    calibration_cv = int(cfg["model"].get("calibration_cv", 5))
    use_calibration = bool(cfg["model"].get("calibrated", True))
    if use_calibration and len(X_train) >= calibration_cv * 10:
        clf = CalibratedClassifierCV(estimator=base_clf, method="sigmoid", cv=calibration_cv)
        print(f"[Train] Wrapping RandomForest in sigmoid probability calibration (cv={calibration_cv}).")
    else:
        clf = base_clf
        print("[Train] WARNING: calibration skipped - confidences are raw forest votes.")
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)
    class_list = list(clf.classes_)

    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred, labels=class_list)
    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    ll = log_loss(y_test, y_proba, labels=class_list)

    # Multiclass Brier score (version-independent manual computation) and
    # Expected Calibration Error: both measure confidence *quality*.
    onehot = np.zeros_like(y_proba)
    for i, label in enumerate(y_test):
        onehot[i, class_list.index(label)] = 1.0
    brier = float(np.mean(np.sum((y_proba - onehot) ** 2, axis=1)))
    ece = expected_calibration_error(y_test, y_proba, class_list)

    print(f"\n[Train] Test accuracy:  {acc:.4f}")
    print(f"[Train] Log loss:       {ll:.4f}  (lower = better calibrated)")
    print(f"[Train] Brier score:    {brier:.4f}  (lower = better calibrated)")
    print(f"[Train] Expected Cal. Error: {ece:.4f}  (confidence vs. actual accuracy)")
    print("[Train] Confusion matrix (rows=true, cols=pred):")
    print(pd.DataFrame(cm, index=class_list, columns=class_list))
    print("\n[Train] Classification report:")
    print(classification_report(y_test, y_pred, zero_division=0))

    # Feature importances come from the forest itself (the calibrated wrapper
    # delegates but does not expose the attribute directly).
    forest = _extract_forest(clf) or base_clf
    importances = {
        name: float(imp)
        for name, imp in zip(FEATURE_NAMES, forest.feature_importances_)
    }
    print("[Train] Feature importances:")
    for name, imp in sorted(importances.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<16} {imp:.4f}")

    # Per-class signal signatures: consumed by the Defect Diagnosis AI to
    # corroborate the classifier with physical evidence at inference time.
    class_profiles = {}
    for cls, group in df.groupby("label"):
        class_profiles[str(cls)] = {
            name: {
                "mean": round(float(group[name].mean()), 4),
                "std": round(float(group[name].std()), 4),
            }
            for name in FEATURE_NAMES
        }

    # Persist artifacts
    joblib.dump(clf, model_path)
    with open(feature_names_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "features": FEATURE_NAMES,
                "classes": class_list,
                "feature_importances": importances,
                "calibrated": bool(use_calibration and clf is not base_clf),
            },
            fh,
            indent=2,
        )
    with open(profiles_path, "w", encoding="utf-8") as fh:
        json.dump(class_profiles, fh, indent=2)

    metrics = {
        "accuracy": acc,
        "calibration": {
            "calibrated": bool(use_calibration and clf is not base_clf),
            "method": "sigmoid" if clf is not base_clf else "none",
            "cv": calibration_cv if clf is not base_clf else 0,
            "log_loss": ll,
            "brier_score": brier,
            "expected_calibration_error": ece,
        },
        "confusion_matrix": cm.tolist(),
        "classes": class_list,
        "classification_report": report,
        "feature_importances": importances,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
    }
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    print(f"\n[Train] Model saved       -> {model_path}")
    print(f"[Train] Features saved    -> {feature_names_path}")
    print(f"[Train] Class profiles    -> {profiles_path}")
    print(f"[Train] Metrics saved     -> {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())