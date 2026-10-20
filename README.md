# AIoT Predictive Safety System - Software Build

This repository contains the software implementation of the **AIoT Predictive Safety System for Small Machines**. The system continuously monitors vibration, temperature, and current of a small DC motor, uses AI to detect and explain faults, and automatically disconnects the motor via a relay to prevent damage.

## Project Overview

The system follows a **Sense → Understand → Predict → Explain → Act** pipeline:
1. **Sense**: Collect real-time data from MPU6050 (vibration), DS18B20 (temperature), and ACS712 (current) sensors
2. **Understand**: Preprocess and extract features (vibration RMS, vibration peak, temperature, current)
3. **Predict**: Classify machine condition using Random Forest ML model
4. **Explain**: Provide human-readable explanations for AI predictions
5. **Act**: Automatically trigger relay cutoff on critical faults

## System Architecture

```
Sensors (MPU6050, DS18B20, ACS712) → ESP32 (Feature Extraction) → MQTT → ML Service (RF Inference + Explainability) → MQTT → Dashboard + ESP32 (Relay Control)
```

**Software Components:**
- **ESP32 Firmware** (C++/Arduino/PlatformIO) - Sensor acquisition, preprocessing, MQTT communication, relay control with safety fallbacks
- **MQTT Broker** (Mosquitto) - Pub/Sub backbone for telemetry, predictions, and commands
- **ML Service** (Python) - Data collection, model training (Random Forest), real-time inference, health scoring, AI explanations
- **Web Dashboard** (Flask + Chart.js) - Real-time visualization, health monitoring, fault history, AI explanation panel

## Quick Start

### Prerequisites

**Hardware (for deployment):**
- ESP32 Dev Board
- MPU6050 (Accelerometer/Gyroscope)
- DS18B20 Temperature Sensor + 4.7kΩ pull-up resistor
- ACS712 Current Sensor
- 5V Relay Module
- 6-12V DC Motor (for testing)
- Breadboard, jumper wires, separate 5V power supply

**Software (for development):**
- [Python 3.10+](https://www.python.org/)
- [PlatformIO](https://platformio.org/) (recommended) or Arduino IDE
- [Eclipse Mosquitto](https://mosquitto.org/)
- Git

### Installation

1. **Clone/Set up the repository** (already done)
2. **Install Python dependencies** for ML service and dashboard:
```bash
pip install -r ml_service/requirements.txt
pip install -r dashboard/requirements.txt
```

3. **Set up Mosquitto MQTT broker** (local development)
4. **Configure ESP32 firmware**: copy `firmware/src/config.h.example` to `firmware/src/config.h` and set your Wi-Fi SSID/password, MQTT broker address, and calibration constants (`ACS712_VREF_V`, `MPU6050_ACCEL_TO_G`), then flash with PlatformIO
5. **Run the software demo** (no hardware needed): `python scripts\simulate_machine.py` (simulated telemetry over MQTT/HTTP) + `python dashboard\app.py` (cockpit at http://localhost:5000 — includes a built-in digital twin if the simulator is not running)

See detailed setup in each component's README/config files.

## Project Structure

```
AIOT&IOT/
├── AIoT_Predictive_Safety_System.md  # Project specification & research
├── README.md                        # This file
├── firmware/                        # ESP32 firmware (PlatformIO)
│   ├── platformio.ini
│   ├── include/
│   └── src/
│       ├── main.cpp
│       ├── sensors.cpp/h
│       ├── mqtt_client.cpp/h
│       ├── relay.cpp/h
│       └── config.h.example
├── ml_service/                      # ML training & inference
│   ├── requirements.txt
│   ├── config.yaml.example
│   ├── collect_data.py
│   ├── train_model.py
│   ├── infer_realtime.py
│   └── utils/
│       ├── diagnosis.py           # Defect Diagnosis AI (root cause + remedies)
│       ├── explain.py             # Health score + baseline deviations
│       └── features.py
├── dashboard/                       # Web dashboard
│   ├── requirements.txt
│   ├── app.py
│   ├── static/
│   └── templates/
├── data/                            # Training data
│   ├── raw/
│   └── processed/
├── models/                          # Trained ML models (+ class profiles)
├── docs/                            # Documentation
│   ├── wiring.md
│   ├── mqtt_topics.md
│   ├── defect_ai.md                # Diagnosis AI design & knowledge base
│   └── audio_alerts.md             # Browser laptop-speaker anomaly alerts
├── scripts/                         # Utility scripts
└── mosquitto/                        # MQTT broker config (optional)
```

## MQTT Topics

| Topic | Direction | Purpose |
|---|---|---|
| `machine/sensors` | ESP32 → Broker | Windowed sensor telemetry (temp, vib_rms, vib_peak, current, ts) |
| `machine/prediction` | ML → Broker | AI prediction with confidence, health score, explanations |
| `machine/command` | ML/Dashboard → ESP32 | Relay control commands (ON/OFF with reason) |
| `machine/status` | ESP32 → Broker | ESP32 status (relay state, WiFi RSSI, uptime) |
| `machine/alerts` | ML → Broker | Alerts for dashboard (WARN/CRIT with messages) |

## ML Model Design

- **Primary Model**: Probability-calibrated Random Forest Classifier (scikit-learn, sigmoid/Platt calibration)
- **Input Features**: `temperature`, `vibration_RMS`, `vibration_peak`, `current` (4 features)
- **Target Classes** (8, hybrid taxonomy): `NORMAL`, `OVERLOAD`, `IMBALANCE`, `BEARING_INNER`, `BEARING_OUTER`, `MISALIGNMENT`, `OVERHEATING`, `CRITICAL`
- **Training Data**: synthetic windows + real rotating-machine measurements from the [KAIST Mendeley dataset](https://data.mendeley.com/datasets/ztmf3m7h5x/3) (vibration/temperature/3-phase current, 3 loads, 2,400 real 1-second windows) — see `docs/defect_ai.md` §9 and `scripts/extract_mendeley_features.py`
- **Trustworthy Confidence**: `CalibratedClassifierCV` wrapper; calibration quality tracked via log-loss, Brier score and Expected Calibration Error (`models/metrics.json`)
- **Health Scoring**: 0-100 based on probability of normal operation with risk penalties
- **Explainability**: Feature importance analysis + percentage deltas vs. normal baseline
- **Defect Diagnosis AI** (`ml_service/utils/diagnosis.py`): fuses calibrated model confidence, physics-based corroboration against learned fault signatures, and a maintenance knowledge base to output **root cause**, **confidence band (HIGH/MEDIUM/LOW)**, ranked **evidence**, and prioritised **remedies** — with honest uncertainty handling (sensor-verification remedies when the model or the physics disagree). See `docs/defect_ai.md`
- **Extension (Future)**: Isolation Forest for unsupervised anomaly detection, TinyML on-device deployment

## Safety Features

- **Automatic Cutoff**: Relay disconnects motor on CRITICAL fault detection
- **Debounce/Hysteresis**: Requires consecutive critical predictions to prevent false trips
- **Local Safety Fallback**: ESP32 can fallback to rule-based cutoff if ML service disconnects
- **Fail-Safe Boot**: Relay starts in OFF state until system is stable
- **Configurable Thresholds**: Confidence thresholds and cooldown periods
- **Browser Audio Alerts**: Optional software-only laptop speaker warnings/critical tones (Web Audio API, edge-triggered with incident dedup) — see `docs/audio_alerts.md`; never required for safety actuation
- **Power Separation**: Designed for separate 5V supply for motor/relay (documented in wiring)

## Development Phases

| Phase | Status | Description |
|---|---|---|
| P0 | ✅ | Project structure & README setup |
| P1 | ✅ | ESP32 sensor acquisition & windowing |
| P2 | ✅ | MQTT telemetry implementation |
| P3 | ✅ | Data collection pipeline (+ Mendeley real-data extraction) |
| P4 | ✅ | Model training (calibrated Random Forest, 8 classes) |
| P5 | ✅ | Real-time inference with explainability |
| P6 | ✅ | Actuation, safety fallbacks & debounce |
| P7 | ✅ | Web dashboard with explanation panel |
| P8 | 🔲 | End-to-end integration testing (hardware-in-the-loop pending) |
| P9 | ✅ | Hardening & documentation |

## References

Based on recent research in AIoT predictive maintenance (2025-2026) focusing on multi-sensor fusion (vibration + temperature + current) with lightweight ML models for edge deployment. See `AIoT_Predictive_Safety_System.md` for full literature review.

## License

This project is developed for educational/research purposes. Suitable for prototype demonstrations with low-voltage DC motors only. **Do not** interface with mains electricity.