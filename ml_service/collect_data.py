"""Data collection script.

Subscribes to the ``machine/sensors`` MQTT topic and appends windowed sensor
readings to a CSV in ``data/raw``.

Usage
-----
    python collect_data.py --label NORMAL
    python collect_data.py --label OVERLOAD --duration 60

Run the motor in each condition while labelled collection is active to build
a supervised dataset for ``train_model.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

from utils.config import ML_SERVICE_DIR, load_config

FIELDNAMES = [
    "timestamp",
    "temperature",
    "vibration_RMS",
    "vibration_peak",
    "current",
    "label",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect MQTT sensor data into CSV.")
    parser.add_argument("--label", default="", help="Label applied to captured rows.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument(
        "--duration", type=float, default=0, help="Seconds to run (0 = until Ctrl+C)."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    raw_dir = (ML_SERVICE_DIR / cfg["data_collection"]["raw_data_dir"]).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = cfg["data_collection"]["csv_filename_prefix"]
    out_path = raw_dir / f"{prefix}_{ts_str}.csv"

    mqtt_cfg = cfg["mqtt"]
    topic = cfg["topics"]["sensors"]
    label = args.label

    file_handle = open(out_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(file_handle, fieldnames=FIELDNAMES)
    writer.writeheader()

    start_time = time.time()
    counter = {"n": 0}

    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"[MQTT] Connected (rc={rc}). Subscribing to '{topic}'")
        client.subscribe(topic, qos=mqtt_cfg.get("qos", 0))

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"[WARN] Bad payload: {exc}")
            return

        writer.writerow(
            {
                "timestamp": payload.get("ts", ""),
                "temperature": payload.get("temp", ""),
                "vibration_RMS": payload.get("vib_rms", ""),
                "vibration_peak": payload.get("vib_peak", ""),
                "current": payload.get("current", ""),
                "label": label,
            }
        )
        file_handle.flush()
        counter["n"] += 1
        print(
            f"[{counter['n']:>6}] T={payload.get('temp')}C "
            f"Vrms={payload.get('vib_rms')} I={payload.get('current')}A "
            f"label={label or '-'}"
        )

        if args.duration and (time.time() - start_time) >= args.duration:
            client.disconnect()

    client = mqtt.Client(client_id="ml-data-collector")
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[Data] Writing to {out_path}")
    if label:
        print(f"[Data] Labeling all rows as '{label}'")

    try:
        client.connect(
            mqtt_cfg["broker_host"], mqtt_cfg["broker_port"], mqtt_cfg["keepalive"]
        )
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[Data] Interrupted by user.")
    finally:
        file_handle.close()
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    print(f"[Data] Saved {counter['n']} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())