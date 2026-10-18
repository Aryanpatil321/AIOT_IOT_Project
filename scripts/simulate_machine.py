"""DC Motor & Sensor Hardware Simulator for AIoT Predictive Safety System.

Simulates the physical behavior of:
- ESP32 microcontroller
- MPU6050 accelerometer (Vibration RMS & Peak)
- DS18B20 digital thermometer (Temperature °C)
- ACS712 current sensor (Current A)
- 5V Relay Actuator (connected to motor circuit)

Publishes telemetry to MQTT ('machine/sensors', 'machine/status') and/or directly
to the Dashboard HTTP API. Subscribes to 'machine/command' to simulate physical
motor shutdown when the relay cuts power.

Usage:
    python scripts/simulate_machine.py
    python scripts/simulate_machine.py --mode IMBALANCE
    python scripts/simulate_machine.py --mode BEARING_INNER
    python scripts/simulate_machine.py --http ""        # MQTT only (no HTTP ingest)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import threading
import time
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
    HAVE_MQTT = True
except ImportError:
    HAVE_MQTT = False

try:
    import urllib.request
    HAVE_URLLIB = True
except ImportError:
    HAVE_URLLIB = False

# Operational Profiles for each physical state
PROFILES = {
    "STOPPED": {
        "temp_target": 25.0,
        "vib_rms": (0.02, 0.005),
        "vib_peak": (0.05, 0.01),
        "current": (0.00, 0.00),
    },
    "NORMAL": {
        "temp_target": 36.5,
        "vib_rms": (0.18, 0.03),
        "vib_peak": (0.38, 0.06),
        "current": (0.48, 0.05),
    },
    "IMBALANCE": {
        "temp_target": 40.0,
        "vib_rms": (1.15, 0.15),
        "vib_peak": (2.45, 0.35),
        "current": (0.62, 0.06),
    },
    "OVERLOAD": {
        "temp_target": 58.0,
        "vib_rms": (0.46, 0.06),
        "vib_peak": (1.08, 0.15),
        "current": (1.75, 0.18),
    },
    "OVERHEATING": {
        "temp_target": 82.0,
        "vib_rms": (0.28, 0.04),
        "vib_peak": (0.60, 0.08),
        "current": (0.70, 0.08),
    },
    # Mirrors the digital-twin profiles in dashboard/app.py, anchored at the
    # real-data class centroids from models/class_profiles.json.
    "BEARING_INNER": {
        "temp_target": 29.5,
        "vib_rms": (16.8, 2.0),
        "vib_peak": (105.0, 12.0),
        "current": (2.33, 0.12),
    },
    "BEARING_OUTER": {
        "temp_target": 31.0,
        "vib_rms": (13.6, 2.2),
        "vib_peak": (52.0, 8.0),
        "current": (2.47, 0.14),
    },
    "MISALIGNMENT": {
        "temp_target": 31.0,
        "vib_rms": (3.5, 0.6),
        "vib_peak": (13.9, 2.5),
        "current": (2.30, 0.12),
    },
    "CRITICAL": {
        "temp_target": 88.0,
        "vib_rms": (1.85, 0.25),
        "vib_peak": (4.20, 0.50),
        "current": (2.75, 0.30),
    },
}


class MachineSimulator:
    def __init__(self, mode: str = "NORMAL", broker: str = "localhost", port: int = 1883, http_url: str | None = None):
        self.mode = mode.upper()
        self.broker = broker
        self.port = port
        self.http_url = http_url
        self.relay_state = "ON"
        self.current_temp = 32.0  # Ambient starting temperature
        self.running = True
        self.critical_latched = False
        self.mqtt_connected = False
        self.mqtt_client = None

        if HAVE_MQTT:
            self._init_mqtt()

    def _init_mqtt(self):
        try:
            self.mqtt_client = mqtt.Client(client_id="machine-hardware-simulator")
            self.mqtt_client.on_connect = self._on_connect
            self.mqtt_client.on_message = self._on_message
            self.mqtt_client.connect_async(self.broker, self.port, keepalive=60)
            self.mqtt_client.loop_start()
        except Exception as exc:
            print(f"[Sim][MQTT] Notice: broker connection postponed: {exc}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"[Sim][MQTT] Connected to broker at {self.broker}:{self.port}")
            self.mqtt_connected = True
            client.subscribe("machine/command", qos=1)
        else:
            print(f"[Sim][MQTT] Connection refused with code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            cmd = data.get("relay", "").upper()
            reason = data.get("reason", "")
            if cmd == "OFF":
                print(f"\n[ACTUATOR] RELAY OPENED (CUTOFF) -> Motor Stopped! Reason: {reason}")
                self.relay_state = "OFF"
                if reason == "CRITICAL_FAULT":
                    self.critical_latched = True
            elif cmd == "ON":
                if self.critical_latched and reason != "MANUAL_RESET":
                    print("[ACTUATOR] Ignored ON: Safety latch active. Requires MANUAL_RESET.")
                    return
                print(f"\n[ACTUATOR] RELAY CLOSED (POWER ON) -> Motor Running. Reason: {reason}")
                self.relay_state = "ON"
                self.critical_latched = False
        except Exception as exc:
            print(f"[Sim] Bad command format: {exc}")

    def set_mode(self, mode: str):
        mode_upper = mode.upper()
        if mode_upper in PROFILES:
            self.mode = mode_upper
            print(f"[Sim] Fault condition changed to: {self.mode}")
        else:
            print(f"[Sim] Unknown mode: {mode}. Choices: {list(PROFILES.keys())}")

    def step(self) -> dict:
        """Advance physical simulation by 1 second."""
        active_state = self.mode if self.relay_state == "ON" else "STOPPED"
        profile = PROFILES[active_state]

        # Thermal inertia: temperature exponentially approaches target state
        target_temp = profile["temp_target"]
        thermal_rate = 0.08 if target_temp > self.current_temp else 0.04
        self.current_temp += (target_temp - self.current_temp) * thermal_rate
        # Add small measurement noise
        temp_reading = round(self.current_temp + random.gauss(0, 0.2), 2)

        # Vibration & current
        vib_rms = max(0.01, round(random.gauss(*profile["vib_rms"]), 4))
        vib_peak = max(vib_rms * 1.5, round(random.gauss(*profile["vib_peak"]), 4))
        current = max(0.0, round(random.gauss(*profile["current"]), 3))

        reading = {
            "ts": int(time.time()),
            "temp": temp_reading,
            "vib_rms": vib_rms,
            "vib_peak": vib_peak,
            "current": current,
            "sim_condition": self.mode,
            "relay": self.relay_state,
        }
        return reading

    def publish(self, reading: dict):
        payload_str = json.dumps(reading)

        # 1. Publish to MQTT if connected
        if self.mqtt_client and self.mqtt_connected:
            self.mqtt_client.publish("machine/sensors", payload_str, qos=0)
            status_payload = {
                "ts": reading["ts"],
                "relay_state": self.relay_state,
                "wifi_rssi": -58 + random.randint(-2, 2),
                "ip_addr": "192.168.1.142 (Sim)",
                "uptime": int(time.time()),
            }
            self.mqtt_client.publish("machine/status", json.dumps(status_payload), qos=0)

        # 2. Direct HTTP push to dashboard if enabled
        if self.http_url and HAVE_URLLIB:
            try:
                req = urllib.request.Request(
                    self.http_url,
                    data=payload_str.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=1.0)
            except Exception:
                pass

        # Console heartbeat
        p_relay = f"\033[92m{self.relay_state}\033[0m" if self.relay_state == "ON" else f"\033[91m{self.relay_state}\033[0m"
        print(
            f"[ESP32 Sim] State={self.mode:<11} | Relay={p_relay} | "
            f"T={reading['temp']:>4.1f}°C  Vrms={reading['vib_rms']:>5.3f}g  "
            f"Vpk={reading['vib_peak']:>5.3f}g  I={reading['current']:>4.2f}A"
        )

    def run(self, interval_sec: float = 1.0):
        print("\n=======================================================")
        print("  AIoT Motor & Sensor Hardware Simulator Running")
        print("=======================================================")
        print(f"Initial State : {self.mode}")
        print(f"Relay State   : {self.relay_state}")
        print(f"MQTT Broker   : {self.broker}:{self.port}")
        if self.http_url:
            print(f"HTTP Ingest   : {self.http_url}")
        print("Commands (type & hit Enter):")
        print("  n : NORMAL | i : IMBALANCE | o : OVERLOAD | h : OVERHEATING")
        print("  b1: BEARING_INNER | b2: BEARING_OUTER | m : MISALIGNMENT")
        print("  c : CRITICAL | s : STOP | on: RESET/ON | q : QUIT")
        print("-------------------------------------------------------\n")

        # Interactive input listener thread
        def user_input_loop():
            while self.running:
                try:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    cmd = line.strip().lower()
                    if cmd == "q":
                        self.running = False
                        break
                    elif cmd in ("n", "normal"):
                        self.set_mode("NORMAL")
                    elif cmd in ("i", "imbalance"):
                        self.set_mode("IMBALANCE")
                    elif cmd in ("o", "overload"):
                        self.set_mode("OVERLOAD")
                    elif cmd in ("h", "heat", "overheating"):
                        self.set_mode("OVERHEATING")
                    elif cmd in ("b1", "bearing_inner", "bi"):
                        self.set_mode("BEARING_INNER")
                    elif cmd in ("b2", "bearing_outer", "bo"):
                        self.set_mode("BEARING_OUTER")
                    elif cmd in ("m", "misalign", "misalignment"):
                        self.set_mode("MISALIGNMENT")
                    elif cmd in ("c", "crit", "critical"):
                        self.set_mode("CRITICAL")
                    elif cmd in ("s", "stop", "off"):
                        self.relay_state = "OFF"
                        print("[Sim] Manually shut down motor (Relay OFF)")
                    elif cmd in ("on", "reset"):
                        self.relay_state = "ON"
                        self.critical_latched = False
                        print("[Sim] Reset and started motor (Relay ON)")
                except Exception:
                    break

        thread = threading.Thread(target=user_input_loop, daemon=True)
        thread.start()

        while self.running:
            reading = self.step()
            self.publish(reading)
            time.sleep(interval_sec)

        if self.mqtt_client:
            self.mqtt_client.loop_stop()
        print("[Sim] Simulator stopped.")


def main():
    parser = argparse.ArgumentParser(description="AIoT Motor & Hardware Simulator.")
    parser.add_argument("--mode", default="NORMAL", choices=list(PROFILES.keys()), help="Initial machine condition.")
    parser.add_argument("--broker", default="localhost", help="MQTT Broker host.")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker port.")
    parser.add_argument("--http", default="http://localhost:5000/api/telemetry_ingest", help="Dashboard HTTP ingest URL.")
    parser.add_argument("--rate", type=float, default=1.0, help="Publish rate in seconds.")
    args = parser.parse_args()

    sim = MachineSimulator(mode=args.mode, broker=args.broker, port=args.port, http_url=args.http)
    sim.run(interval_sec=args.rate)


if __name__ == "__main__":
    main()
