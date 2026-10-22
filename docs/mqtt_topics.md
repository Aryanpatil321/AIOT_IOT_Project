# AIoT Predictive Safety System - MQTT Topics Documentation

## Overview

This document defines all MQTT topics used in the system for communication between ESP32, ML Service, and Dashboard via Mosquitto broker.

**Default Broker:** localhost:1883 (TCP), localhost:9001 (WebSocket for Dashboard)

**Starting the broker:** `.\scripts\start_mosquitto.ps1` — uses Eclipse Mosquitto if installed,
otherwise it launches the bundled Python MQTT 3.1.1 broker (`scripts/mqtt_broker.py`, `amqtt`)
with the same ports, anonymous access and topic behavior. Configuration lives in `mosquitto/mosquitto.conf`.

**Watching traffic live:** `python scripts\mqtt_monitor.py` subscribes to `machine/#` and prints
each message (topic, QoS, size, decoded payload) with a per-topic count every 15 seconds.
Use `--raw` for full payloads, `--topic "machine/prediction"` to filter, or
`--broker <ip> --port 1883` for a remote broker.

## Topic Definitions

### 1. `machine/sensors` (ESP32 → ML Service → Dashboard)
**Direction:** Publish from ESP32  
**QoS:** 0 (or 1 for reliability)  
**Frequency:** Every ~1 second (windowed sensor data)

Publishes windowed, preprocessed sensor features from ESP32.

**Payload (JSON):**
```json
{
  "ts": 1728420000,
  "temp": 31.5,
  "vib_rms": 0.182,
  "vib_peak": 0.41,
  "current": 0.725
}
```

**Field Descriptions:**
| Field | Type | Units | Description |
|---|---|---|---|
| `ts` | int/float | Unix timestamp (seconds) | Time when window was captured |
| `temp` | float | °C | Temperature from DS18B20 |
| `vib_rms` | float | g | Vibration RMS (Root Mean Square) of acceleration magnitude over window |
| `vib_peak` | float | g | Peak absolute acceleration magnitude over window |
| `current` | float | A | Motor current from ACS712 (calibrated) |

---

### 2. `machine/prediction` (ML Service → Dashboard)
**Direction:** Publish from ML Service after inference  
**QoS:** 0  
**Frequency:** On new sensor data or at configured interval

Contains AI prediction results including health score and explanations.

**Payload (JSON):**
```json
{
  "ts": 1728420005,
  "state": "NORMAL",
  "fault": "NONE",
  "confidence": 0.94,
  "confidence_band": "HIGH",
  "health": 96,
  "explanation": [
    "Vibration RMS near baseline (+4%)",
    "Current stable (-2%)",
    "Temperature stable (+1%)",
    "Combined pattern matches learned healthy state"
  ],
  "class_probs": {
    "NORMAL": 0.94,
    "IMBALANCE": 0.03,
    "OVERLOAD": 0.02,
    "OVERHEATING": 0.01,
    "BEARING_INNER": 0.00,
    "BEARING_OUTER": 0.00,
    "MISALIGNMENT": 0.00,
    "CRITICAL": 0.00
  },
  "diagnosis": {
    "primary_cause": "Nominal operation",
    "confidence_band": "HIGH",
    "reasons": [],
    "remedies": []
  }
}
```

**Field Descriptions:**
| Field | Type | Description |
|---|---|---|
| `ts` | int | Unix timestamp of prediction |
| `state` | string | High-level state: `NORMAL`, `WARN`, `CRITICAL` |
| `fault` | string | Predicted fault class: `NONE`, `OVERLOAD`, `IMBALANCE`, `OVERHEATING`, `BEARING_INNER`, `BEARING_OUTER`, `MISALIGNMENT`, `CRITICAL` |
| `confidence` | float | Prediction confidence (0.0 - 1.0) |
| `confidence_band` | string | Calibrated confidence band: `LOW`, `MEDIUM`, `HIGH` (null while model uncalibrated) |
| `health` | int | Machine health score (0 - 100) |
| `explanation` | array | Human-readable explanation strings for dashboard panel |
| `class_probs` | object | Probability distribution across all 8 classes |
| `diagnosis` | object | Explainable diagnosis: `primary_cause`, `reasons[]` (feature evidence), `remedies[]` (prioritised actions) |

**State Mapping:**
- `NORMAL` → fault is `NONE`
- `WARN` → fault is `OVERLOAD`, `IMBALANCE`, `OVERHEATING`, `BEARING_INNER`, `BEARING_OUTER`, or `MISALIGNMENT` (non-critical — relay stays on)
- `CRITICAL` → fault is `CRITICAL` (trips the relay after debounce)

---

### 3. `machine/command` (ML Service → ESP32, Dashboard → ESP32 for manual override)
**Direction:** Subscribe on ESP32, Publish from ML Service (auto) or Dashboard (manual)  
**QoS:** 1 (important for relay control)  
**Frequency:** As needed

Commands ESP32 to control relay. Includes reason for audit trail.

**Payload (JSON):**
```json
{
  "ts": 1728420120,
  "relay": "OFF",
  "reason": "CRITICAL_FAULT",
  "confidence": 0.91,
  "fault": "CRITICAL",
  "source": "ml_auto"
}
```

**Payload (Manual Override Example):**
```json
{
  "ts": 1728420150,
  "relay": "ON",
  "reason": "MANUAL_RESET",
  "source": "dashboard"
}
```

**Field Descriptions:**
| Field | Type | Required | Description |
|---|---|---|---|
| `ts` | int | Yes | Command timestamp |
| `relay` | string | Yes | Target state: `ON` or `OFF` |
| `reason` | string | Yes | Reason: `CRITICAL_FAULT`, `ML_TIMEOUT_FALLBACK`, `MANUAL_OVERRIDE`, `MANUAL_RESET`, `COOLDOWN_EXPIRED` |
| `confidence` | float | No | ML confidence if auto-triggered |
| `fault` | string | No | Fault class if auto-triggered |
| `source` | string | No | Source: `ml_auto`, `dashboard`, `firmware_fallback` |

**Notes:**
- ESP32 must only act on this topic for relay control
- Dashboard should require confirmation for manual override (safety)
- Commands should respect cooldown periods

---

### 4. `machine/status` (ESP32 → ML Service → Dashboard)
**Direction:** Publish from ESP32  
**QoS:** 0  
**Frequency:** Every 1 second (heartbeat)

ESP32 operational status including relay state and connectivity info.

**Payload (JSON):**
```json
{
  "ts": 1728420003,
  "relay": 1,
  "relay_state": "ON",
  "wifi_rssi": -55,
  "uptime_s": 1234,
  "free_heap": 234560,
  "ip_addr": "192.168.1.105",
  "fw_version": "0.1.0"
}
```

**Field Descriptions:**
| Field | Type | Description |
|---|---|---|
| `ts` | int | Status timestamp |
| `relay` | int | Relay state numeric: `0` = OFF, `1` = ON |
| `relay_state` | string | Relay state text: `ON` or `OFF` |
| `wifi_rssi` | int | Wi-Fi signal strength (dBm) |
| `uptime_s` | int | Uptime since boot (seconds) |
| `free_heap` | int | Free heap memory (bytes) |
| `ip_addr` | string | ESP32 IP address on network |
| `fw_version` | string | Firmware version string |

---

### 5. `machine/alerts` (ML Service → Dashboard)
**Direction:** Publish from ML Service  
**QoS:** 0  
**Frequency:** On state change or new warning/critical event

Human-readable alerts for dashboard display (notifications, banners).

**Payload (JSON):**
```json
{
  "ts": 1728420100,
  "level": "WARN",
  "msg": "Early fault detected: OVERLOAD (Confidence: 87%)",
  "fault": "OVERLOAD",
  "confidence": 0.87,
  "health": 72,
  "persistent": false
}
```

**Critical Alert Example:**
```json
{
  "ts": 1728420120,
  "level": "CRIT",
  "msg": "CRITICAL FAULT detected! Motor will be stopped automatically.",
  "fault": "CRITICAL",
  "confidence": 0.91,
  "health": 15,
  "persistent": true
}
```

**Field Descriptions:**
| Field | Type | Description |
|---|---|---|
| `ts` | int | Alert timestamp |
| `level` | string | Alert severity: `INFO`, `WARN`, `CRIT` |
| `msg` | string | Human-readable alert message |
| `fault` | string | Associated fault class (if any) |
| `confidence` | float | ML confidence (if any) |
| `health` | int | Current health score |
| `persistent` | bool | Whether alert should persist in history/UI until cleared |

**Alert Trigger Rules (Suggested):**
- `WARN`: Fault is OVERLOAD/IMBALANCE/OVERHEATING/BEARING_INNER/BEARING_OUTER/MISALIGNMENT with confidence >= `warn_confidence_min` and meets consecutive count
- `CRIT`: Fault is CRITICAL with confidence >= `critical_confidence_min` and meets debounce count (before/at relay cutoff)
- `INFO`: Status changes like "Motor restarted", "Connection restored", etc.

---

## Topic Flow Diagram

```
ESP32 --(sensors)--> Broker --(sensors)--> ML Service
ESP32 <--(command)-- Broker <--(command)-- ML Service
ESP32 <--(command)-- Broker <--(command)-- Dashboard (manual)
ESP32 --(status)-->  Broker --(status)-->  ML Service & Dashboard

ML Service --(prediction)--> Broker --(prediction)--> Dashboard
ML Service --(alerts)-->     Broker --(alerts)-->     Dashboard
ML Service --(command)-->    Broker --(command)-->    ESP32
```

## Mosquitto Configuration Notes

For local development, enable both TCP and WebSocket listeners:

**mosquitto.conf:**
```conf
listener 1883
protocol mqtt

listener 9001
protocol websockets

allow_anonymous true
```

**Dashboard Connection (WebSocket):** `ws://localhost:9001`

## QoS Recommendations

| Topic | QoS | Rationale |
|---|---|---|
| `machine/sensors` | 0 | High frequency, loss of single reading acceptable (windowed) |
| `machine/prediction` | 0 | Real-time updates, can be refreshed |
| `machine/command` | 1 | Critical - must be delivered at least once for relay safety |
| `machine/status` | 0 | Heartbeat, frequent |
| `machine/alerts` | 0 | Important but dashboard can recover state from prediction |

## Security Notes (For Production Consideration)

- **Anonymous access disabled**: Use username/password authentication
- **TLS encryption**: For remote access beyond local network
- **Topic ACLs**: Restrict who can publish to `machine/command` (ideally ML + authorized dashboard users only)
- **Local Network Only**: Keep broker on isolated network for prototype/demo

*This is for educational prototype use - local anonymous access is fine for development.*
