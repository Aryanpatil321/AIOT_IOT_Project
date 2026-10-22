"""Live MQTT traffic monitor for the AIoT Predictive Safety System.

Subscribes to ``machine/#`` and prints every message with its topic, QoS,
timestamp and a compact payload, plus a periodic per-topic summary. Useful
for demonstrating (and debugging) that the broker is actually carrying
telemetry, predictions, alerts and relay commands.

Broker-down safe: it keeps retrying instead of crashing, so it can run for
the whole demo.

Usage:
    python scripts/mqtt_monitor.py
    python scripts/mqtt_monitor.py --topic "machine/#" --summary-interval 10
    python scripts/mqtt_monitor.py --raw          # print full payloads
    python scripts/mqtt_monitor.py --broker 192.168.0.104 --port 1883
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from datetime import datetime

import paho.mqtt.client as mqtt


class TrafficMonitor:
    def __init__(self, broker: str, port: int, topic: str, raw: bool, summary_interval: float):
        self.broker = broker
        self.port = port
        self.topic = topic
        self.raw = raw
        self.summary_interval = summary_interval
        self.counts: Counter[str] = Counter()
        self.bytes_total = 0
        self.total = 0
        self.lock = threading.Lock()
        self.connected = False

    # ------------------------------------------------------------------ MQTT
    def _make_client(self) -> mqtt.Client:
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="aiot-traffic-monitor",
            )
        except (AttributeError, TypeError):  # paho-mqtt 1.x
            client = mqtt.Client(client_id="aiot-traffic-monitor")
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self.connected = True
        try:
            client.subscribe(self.topic, qos=1)
        except Exception as exc:  # never kill the demo
            print(f"[Monitor] Subscribe failed: {exc}")
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] connected to mqtt://{self.broker}:{self.port} - watching '{self.topic}'")
        print(f"[{stamp}] {'-' * 78}")

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        self.connected = False
        if args:
            reason = args[1] if len(args) >= 2 else args[0]
            print(f"[Monitor] broker connection lost ({reason}); retrying automatically...")
        else:
            print("[Monitor] broker connection lost; retrying automatically...")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = repr(msg.payload)

        preview = payload
        if not self.raw:
            preview = self._compact(payload)
        if len(preview) > 200 and not self.raw:
            preview = preview[:197] + "..."

        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        qos = f"QoS{msg.qos}"
        retain = " RETAINED" if msg.retain else ""
        print(f"{now}  {msg.topic:<26} {qos:<5} {len(msg.payload):>4} B{retain:<9}  {preview}")

        with self.lock:
            self.counts[msg.topic] += 1
            self.bytes_total += len(msg.payload)
            self.total += 1

    @staticmethod
    def _compact(payload: str) -> str:
        """Render JSON on one short line; leave non-JSON untouched."""
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError):
            return payload
        if isinstance(obj, dict):
            parts = []
            for key, value in obj.items():
                if isinstance(value, float):
                    value = f"{value:.3f}"
                parts.append(f"{key}={value}")
            return " ".join(parts)
        return json.dumps(obj, separators=(",", ":"))

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        client = self._make_client()
        print(f"[Monitor] connecting to mqtt://{self.broker}:{self.port} ...")
        client.connect_async(self.broker, self.port, keepalive=30)
        client.loop_start()

        last_summary = time.monotonic()
        try:
            while True:
                time.sleep(0.5)
                if time.monotonic() - last_summary >= self.summary_interval:
                    last_summary = time.monotonic()
                    self._print_summary()
        except KeyboardInterrupt:
            print("\n[Monitor] stopping...")
        finally:
            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                pass
            self._print_summary(final=True)

    def _print_summary(self, final: bool = False) -> None:
        with self.lock:
            counts = dict(self.counts)
            total = self.total
            nbytes = self.bytes_total
        if total == 0:
            state = "connected" if self.connected else "waiting for broker"
            print(f"[Monitor] ({state}) no messages in the last {self.summary_interval:g}s")
            return

        label = "TOTAL" if final else "SUMMARY"
        print(f"\n[Monitor] {label}: {total} messages, {nbytes} bytes")
        for topic, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"          {topic:<32} {count:>6}")
        print()
        if final:
            return
        # Reset the window counters so each summary reflects the last interval.
        with self.lock:
            self.counts.clear()
            self.total = 0
            self.bytes_total = 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live MQTT traffic monitor.")
    parser.add_argument("--broker", default="localhost", help="Broker host (default: localhost).")
    parser.add_argument("--port", type=int, default=1883, help="Broker TCP port (default: 1883).")
    parser.add_argument("--topic", default="machine/#", help="Topic filter to watch (default: machine/#).")
    parser.add_argument("--raw", action="store_true", help="Print full payloads instead of a compact preview.")
    parser.add_argument("--summary-interval", type=float, default=15.0,
                        help="Seconds between throughput summaries (default: 15).")
    args = parser.parse_args()

    monitor = TrafficMonitor(
        broker=args.broker,
        port=args.port,
        topic=args.topic,
        raw=args.raw,
        summary_interval=args.summary_interval,
    )
    try:
        monitor.run()
    except Exception as exc:  # last-resort guard: a demo must not crash
        print(f"[Monitor] stopped: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
