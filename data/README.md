# Data Directory

Place raw captured sensor CSVs in `raw/` and any cleaned datasets in `processed/`.

## Raw data format (`data/raw/*.csv`)

| Column | Units | Description |
|---|---|---|
| `timestamp` | ms (device uptime) | Time the window was captured |
| `temperature` | °C | DS18B20 temperature |
| `vibration_RMS` | g | Windowed vibration RMS |
| `vibration_peak` | g | Windowed vibration peak |
| `current` | A | Calibrated motor current |
| `label` | string | One of `NORMAL`, `OVERLOAD`, `IMBALANCE`, `OVERHEATING`, `BEARING_INNER`, `BEARING_OUTER`, `MISALIGNMENT`, `CRITICAL` |

## Real dataset

`data/raw/real_kast_dataset.csv` is generated (not hand-collected) from the
Mendeley rotating-machine recordings in `data/external/mendeley/`:

```powershell
python scripts\extract_mendeley_features.py
```

## Collection workflow

Run one labelled capture per machine condition (see `scripts/collect.ps1`):

```powershell
.\scripts\collect.ps1 -Label NORMAL -Duration 120
.\scripts\collect.ps1 -Label IMBALANCE -Duration 120
.\scripts\collect.ps1 -Label OVERLOAD -Duration 120
.\scripts\collect.ps1 -Label OVERHEATING -Duration 120
```

Then train:

```powershell
cd ml_service
.\.venv\Scripts\python.exe train_model.py
```

Generated CSVs are git-ignored; keep them locally.