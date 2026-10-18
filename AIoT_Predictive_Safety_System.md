# AIoT Predictive Safety System for Small Machines

**Tagline:** *"Detect a machine fault before the machine fails."*

**Category:** AIoT (Artificial Intelligence + Internet of Things) — Predictive Maintenance / Industrial Safety

---

## 1. Project Overview

This project builds a mini predictive-maintenance and safety system for a small machine (DC motor, exhaust fan, water pump, or conveyor motor). Instead of reacting to a fault *after* it happens, the system continuously monitors vibration, temperature, and current, uses a trained AI model to detect abnormal behavior patterns, classifies the type of fault, explains *why* it flagged the condition, and automatically disconnects the motor via a relay before damage occurs.

The core idea: **Sense → Understand → Predict → Explain → Act**

This is deliberately *not* a simple `if(temp > 50) relayOff();` threshold system — the AI learns the *combined relationship* between all three sensor signals, which is what distinguishes a genuine predictive-maintenance system from a basic automation project.

---

## 2. Why This Project (Research Justification)

This project is grounded in active, recent research rather than being an arbitrary idea:

1. A 2025 study on low-cost predictive maintenance used an ESP32 with an accelerometer and microphone, extracting RMS/FFT features and applying anomaly detection to identify abnormal motor states.
2. A 2025 study on motor condition monitoring combined current, voltage, temperature, and humidity measurements with Random Forest classification and MQTT/cloud monitoring.
3. Research on industrial motors found that combining vibration with temperature, current, sound, and rotational data improves fault classification accuracy compared to relying on vibration alone.
4. A 2026 paper on ESP32/edge AI vibration monitoring found that lightweight classical ML models (Random Forest, SVM) are well-suited to constrained hardware due to low inference cost.
5. A 2026 paper on lightweight Edge AI predictive maintenance highlighted the growing shift toward **multi-sensor fusion** (vibration + temperature + current + other operating variables) over single-sensor approaches, for detecting multiple fault types.

**Conclusion:** This project takes an active, published research direction (multi-sensor AIoT predictive maintenance) and implements a smaller, affordable, student-accessible version of it — rather than inventing an arbitrary use case.

---

## 3. System Architecture

```
       ┌──────────────┐
       │  Vibration   │
       │   MPU6050    │
       └──────┬───────┘
              │
       ┌──────▼───────┐
       │ Temperature  │
       │   DS18B20    │
       └──────┬───────┘
              │
       ┌──────▼───────┐
       │   Current    │
       │   ACS712     │
       └──────┬───────┘
              │
              ▼
        ┌───────────┐
        │   ESP32   │
        │           │
        │ AI MODEL  │
        └─────┬─────┘
              │
       ┌──────┴───────────┐
       │                  │
       ▼                  ▼
   MQTT/Web           RELAY
   Dashboard             │
                         ▼
                    Motor OFF
```

### How It Works (Pipeline)

| Stage | Description |
|---|---|
| **Sensors** | MPU6050 + DS18B20 + ACS712 collect real-time vibration, temperature, and current data |
| **ESP32** | Processes sensor data and runs the ML model (or sends data to a host running the model) |
| **Prediction** | Classifies condition as Normal / Fault, with explanation of contributing factors |
| **Actuator (Relay)** | Automatically stops the motor if a critical fault is detected |
| **IoT Dashboard (MQTT)** | Displays live data, alerts, graphs, and fault history |

---

## 4. Components Used

| # | Component | Role |
|---|---|---|
| 1 | **ESP32** | Main controller (WiFi-enabled microcontroller) |
| 2 | **MPU6050** | Vibration sensor (accelerometer + gyroscope) |
| 3 | **DS18B20** | Temperature sensor (digital, 1-Wire) |
| 4 | **ACS712** | Current sensor (motor current draw) |
| 5 | **Relay Module** | Actuator — disconnects/stops the motor |
| 6 | **DC Motor** | The load/machine being monitored and protected |

### Approximate Budget

| Component | Cost (₹) |
|---|---|
| ESP32 | 400 |
| MPU6050 (vibration) | 150 |
| DS18B20 (temperature) + 4.7kΩ resistor | 100 |
| ACS712 (current sensor) | 150 |
| Relay module | 100 |
| Small 6–12V DC motor (load) | 150 |
| Breadboard, jumper wires, 5V power supply | 250 |
| **Total** | **~₹1,300–1,500** |

> **Safety note:** Use a low-voltage DC motor (6–12V) for the prototype demo. Do not experiment directly with mains/AC electricity in a student prototype.

---

## 5. Wiring and Pin Connections

### Wire Colour Guide

| Wire Purpose | Colour |
|---|---|
| 5V (VCC) | Red |
| GND | Black |
| I2C (SCL) | Green |
| I2C (SDA) | Yellow |
| 1-Wire (DS18B20) | Light Blue |
| ACS712 Output | Orange |
| Relay Signal | Purple |

### ESP32 Pin Connections

| Component | ESP32 Pin |
|---|---|
| MPU6050 (SCL) | GPIO 22 |
| MPU6050 (SDA) | GPIO 21 |
| DS18B20 (Data) | GPIO 4 |
| ACS712 (OUT) | GPIO 34 (ADC) |
| Relay (IN) | GPIO 27 |

### Important Notes
- DS18B20 needs a **4.7kΩ pull-up resistor** between the data line and 5V.
- All sensors operate on 5V, but ESP32 GPIOs are 3.3V logic — use level shifting if required for some modules.
- Use a **separate 5V supply** for the motor and relay if the motor draws high current (don't power it directly off the ESP32's regulator).
- Keep the demo motor on low-voltage DC for safety.

---

## 6. The AI Layer — What Makes This AIoT (Not Just IoT)

### Weak framing (avoid this)
> "We use AI to monitor temperature."

### Correct framing (use this)
> The AI learns the **relationship between vibration, temperature, and current together** — not any single sensor in isolation. A rise in one variable alone may be normal; a *combined* abnormal pattern across all three is what indicates a developing fault.

### Sensor Combination → Interpretation Table

| Vibration | Temperature | Current | AI Interpretation |
|---|---|---|---|
| Low | Normal | Normal | Healthy |
| High | Normal | Normal | Mechanical vibration issue |
| High | Rising | High | Possible overload |
| Low | Very high | High | Possible overheating / electrical problem |
| Very high | High | High | Critical condition |

This is the key academic differentiator: the model is trained on **combinations of sensor behavior** rather than independent thresholds like `if(temp > 50)`.

### Example Scenarios

**Normal Motor**
```
Temperature: 31°C
Vibration RMS: 0.18
Current: 0.72 A
      ↓
   AI MODEL
      ↓
   NORMAL (Confidence: 94%)
→ Motor continues running.
```

**Early Fault**
```
Temperature: 48°C ↑
Vibration RMS: 0.61 ↑
Current: 1.31 A ↑
      ↓
   AI MODEL
      ↓
EARLY FAULT — Probability: 87%
Likely cause: Mechanical imbalance / overload
→ Dashboard gives a warning.
```

**Dangerous Condition**
```
Temperature: 67°C
Vibration RMS: 1.12
Current: 2.05 A
      ↓
   AI MODEL
      ↓
CRITICAL FAULT
      ↓
   RELAY OFF
→ Motor physically stops.
```

---

## 7. AI Model Design (Two-Stage Approach)

### Stage 1 — Random Forest (Primary Classifier)

- **Input features:** `temperature`, `vibration_RMS`, `vibration_peak`, `current`
- **Output classes:** `NORMAL`, `OVERLOAD`, `IMBALANCE`, `OVERHEATING`, `BEARING_INNER`, `BEARING_OUTER`, `MISALIGNMENT`, `CRITICAL` (8-class hybrid taxonomy: 5 synthetic + 3 real-data rotating-machine fault classes)
- Random Forest is practical at this project's scale and is directly used in recent edge predictive-maintenance research.

### Stage 2 — Edge AI / TinyML (Advanced Extension)

- Deploy a lightweight version of the model directly on the ESP32.
- Research shows anomaly detection can run on-device: one ESP32-based study reported Isolation Forest training on-device in seconds, with anomaly inference in milliseconds.
- This stage is optional — mark it as a "future scope" item if time/budget is limited, and keep Stage 1 (laptop/PC-hosted model + MQTT communication) as your working deliverable.

### Alternative/Supplementary Model
- **Isolation Forest** can be used as an unsupervised anomaly detector, especially useful if you don't have enough *labeled* fault data and want to flag "this doesn't look like normal operation" without needing every fault type pre-labeled.

---

## 8. Dashboard Design

Go beyond simple gauges — build a dashboard that tells a story:

- **Machine Health Score:** e.g., `87 / 100`
- **Current Condition:** 🟢 NORMAL / 🟡 EARLY FAULT / 🔴 CRITICAL
- **AI Prediction:** e.g., "Healthy — 94% confidence"
- **Sensor Trend Graphs:** Temperature, Vibration, Current (live, time-series)
- **Fault History Log:**
  ```
  10:42 — Normal
  10:46 — Vibration increasing
  10:48 — Early anomaly detected
  10:49 — Critical condition
  10:49 — Motor automatically stopped
  ```
- **AI Explanation Panel** (this is especially valuable for your viva):
  ```
  Why was an anomaly detected?
  - Vibration increased by 62%
  - Current increased by 38%
  - Temperature increased by 21%
  Combined pattern differs from the learned healthy state.
  ```

**Tech stack suggestion:** MQTT broker (e.g., Mosquitto) + a simple web dashboard (Node-RED, Flask + Chart.js, or a React/HTML page reading from MQTT via a WebSocket bridge).

---

## 9. System Capability Levels (Scope Progression)

Use this table to show a clear "basic → advanced" progression in your report — useful if you need to describe project phases or a fallback scope.

| Basic Machine Monitoring | Machine Predictive Maintenance | Enhanced Version (AIoT + Safety) |
|---|---|---|
| Collect sensor data | Train ML model (Random Forest) | Sensor fusion + explainable AI |
| Send to dashboard (MQTT) | Predict faults (normal / overload / imbalance / overheating / critical) | Automatic relay action (stop motor) |
| Visualize real-time values | Show health score & explanation | Health score + fault type |
| No AI / No actuator | Dashboard + alerts | Edge AI / TinyML (later) |
| — | — | Full IoT dashboard with history |

This lets you scope the project realistically: **minimum viable submission = first two columns; full AIoT vision = third column.**

---

## 10. Extended Direction (Optional / Stretch Goal)

### "AIoT Machine Health Digital Twin — Mini Version"

Instead of a flat "motor is faulty" output, maintain a virtual health state that fuses all sensors before deciding:

```
                  MACHINE
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   Vibration     Temperature     Current
       │             │             │
       └─────────────┼─────────────┘
                     ▼
              SENSOR FUSION
                     ▼
                AI MODEL
                     ▼
          ┌──────────┴──────────┐
          ▼                     ▼
     Health Score          Fault Type
          │                     │
          └──────────┬──────────┘
                     ▼
               ACTION ENGINE
                     ▼
                  RELAY
```

This framing directly echoes your earlier **PredictTwin** concept (RUL prediction + digital twin + explainability on NASA C-MAPSS), scaled down to real, affordable hardware — giving your AIoT and IoT submissions a consistent conceptual thread if they're being evaluated together.

---

## 11. Alternate Project Variant (Backup Idea)

### AIoT Smart Electrical Fault Prevention

If you want a variant focused purely on electrical safety rather than mechanical motor health:

- **Sensors:** Voltage sensor, Current sensor, Temperature sensor
- **Actuator:** Relay
- **AI detects:** Normal / Overload / Abnormal current / Overheating / Electrical anomaly
- **Flow:**
  ```
  AI detects dangerous condition
             ↓
         Relay OFF
             ↓
     Equipment isolated
  ```
- Supported by 2025/2026 research demonstrating AI-based anomaly detection for electrical equipment using ESP32/TinyML, with abnormal states triggering alerts and optionally cutting power via relay.
- **Safety note:** Keep this on low-voltage DC loads for a student prototype — do not interface directly with mains electricity.

---

## 12. Research References Summary

| # | Topic | Relevance to Project |
|---|---|---|
| 1 | ESP32 + accelerometer/microphone low-cost predictive maintenance (2025) | RMS/FFT feature extraction + anomaly detection baseline |
| 2 | Motor condition monitoring with current/voltage/temperature/humidity + Random Forest + MQTT (2025) | Direct architectural match (sensors + RF + MQTT dashboard) |
| 3 | Multi-variable fault classification (vibration + temp + current + sound) outperforming vibration-only models | Justifies multi-sensor fusion approach |
| 4 | ESP32/edge AI vibration monitoring with lightweight RF/SVM models (2026) | Justifies Random Forest as a practical, low-inference-cost model choice |
| 5 | Multi-sensor fusion for lightweight Edge AI predictive maintenance (2026) | Supports the "Digital Twin" extension direction |
| 6 | On-device Isolation Forest training/inference on ESP32 (seconds to train, milliseconds to infer) | Supports Stage 2 TinyML feasibility |
| 7 | AI-based anomaly detection for electrical equipment via ESP32/TinyML with relay cutoff (2025/2026) | Supports the alternate "Smart Electrical Fault Prevention" variant |

*(Use your citation manager / Google Scholar to pull exact author names, journal names, and DOIs for these before submission — this table is a structured placeholder for your literature review section.)*

---

## 13. Suggested Report/Submission Structure

1. **Abstract**
2. **Introduction & Motivation** (why predictive maintenance matters; cost of unplanned downtime)
3. **Literature Review** (use Section 12 above, expanded with full citations)
4. **System Architecture** (Section 3 diagram)
5. **Hardware Design** (Sections 4–5: components, wiring, pin table)
6. **AI/ML Methodology** (Section 7: model choice, features, classes, training approach)
7. **Software & Dashboard Design** (Section 8)
8. **Results & Evaluation** (accuracy, confusion matrix, example classification scenarios from Section 6)
9. **Discussion** (what makes this AIoT vs. plain IoT — Section 6 framing)
10. **Limitations & Future Scope** (TinyML on-device deployment, Digital Twin extension — Sections 7 & 10)
11. **Conclusion**
12. **References**

---

## 14. One-Line Project Summary (for abstract/report use)

> "This project implements an AIoT predictive-maintenance and safety system that fuses vibration (MPU6050), temperature (DS18B20), and current (ACS712) data on an ESP32 to detect developing machine faults through a Random Forest classifier, provide human-readable explanations for each prediction, and autonomously isolate the motor via a relay before failure occurs — demonstrating a complete sense-understand-predict-explain-act pipeline rather than simple threshold-based automation."
