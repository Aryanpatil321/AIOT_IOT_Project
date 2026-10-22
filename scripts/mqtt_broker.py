"""Local MQTT broker fallback for the AIoT Predictive Safety System.

Used automatically by ``scripts/start_mosquitto.ps1`` when Eclipse Mosquitto
is not installed (e.g. no admin rights or mosquitto.org is unreachable).
It mirrors ``mosquitto/mosquitto.conf``:

  * MQTT over TCP       -> 0.0.0.0:1883 (ESP32 firmware + Python services)
  * MQTT over WebSockets-> 0.0.0.0:9001 (browser clients, optional)
  * anonymous access enabled (local development only)

This is a full MQTT 3.1.1 broker (amqtt), so paho-mqtt, PubSubClient on the
ESP32 and any other compliant client work unchanged.

Requires: pip install amqtt
Usage:    python scripts/mqtt_broker.py [--port 1883] [--ws-port 9001]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
from pathlib import Path

from amqtt.broker import Broker
from amqtt.errors import BrokerError

CONF_PATH = Path(__file__).resolve().parent.parent / "mosquitto" / "mosquitto.conf"


def ports_from_conf() -> tuple[int, int | None]:
    """Read the first TCP and WebSocket listener ports from mosquitto.conf."""
    tcp, ws = 1883, None
    try:
        lines = [ln.strip() for ln in CONF_PATH.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return tcp, ws

    for i, line in enumerate(lines):
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] != "listener" or len(parts) < 2:
            continue
        try:
            port = int(parts[1])
        except ValueError:
            continue
        # The protocol directive follows the listener line in mosquitto.conf.
        proto = "mqtt"
        for lookahead in lines[i + 1:i + 4]:
            if lookahead.startswith("protocol"):
                proto = lookahead.split()[1]
                break
        if proto == "websockets":
            ws = port
        else:
            tcp = port
    return tcp, ws


def build_config(port: int, ws_port: int | None) -> dict:
    listeners: dict[str, dict] = {
        "default": {"type": "tcp", "bind": f"0.0.0.0:{port}", "max_connections": 128},
    }
    if ws_port:
        listeners["ws"] = {"type": "ws", "bind": f"0.0.0.0:{ws_port}", "max_connections": 64}

    return {
        "listeners": listeners,
        "plugins": {
            "amqtt.plugins.logging_amqtt.EventLoggerPlugin": {},
            "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
            "amqtt.plugins.sys.broker.BrokerSysPlugin": {"sys_interval": 20},
        },
    }


async def serve(port: int, ws_port: int | None) -> None:
    broker = Broker(build_config(port, ws_port))
    await broker.start()

    print("=" * 60)
    print("  AIoT local MQTT broker (amqtt fallback for Mosquitto)")
    print("=" * 60)
    print(f"  MQTT TCP      : mqtt://0.0.0.0:{port}")
    if ws_port:
        print(f"  MQTT WebSocket: ws://0.0.0.0:{ws_port}")
    print("  Auth          : anonymous (local development only)")
    print("  Press Ctrl+C to stop.")
    print("=" * 60, flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows event loop
            pass

    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await broker.shutdown()
        print("\n[Broker] Stopped.")


def port_in_use(port: int) -> bool:
    """Return True when nothing can bind 0.0.0.0:port (another broker is up)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def main() -> int:
    conf_tcp, conf_ws = ports_from_conf()
    parser = argparse.ArgumentParser(description="Local MQTT broker (amqtt fallback).")
    parser.add_argument("--port", type=int, default=conf_tcp, help="MQTT TCP port (default from mosquitto.conf).")
    parser.add_argument("--ws-port", type=int, default=conf_ws, help="WebSocket port (default from mosquitto.conf).")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    busy = [p for p in (args.port, args.ws_port) if p and port_in_use(p)]
    if busy:
        ports = ", ".join(str(p) for p in busy)
        print(f"[Broker] Port(s) {ports} already in use - a broker is probably already running.")
        print("[Broker] Free them and retry:")
        print(f"         Get-NetTCPConnection -LocalPort {busy[0]} -State Listen | "
              "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }")
        print("[Broker] Or use different ports:")
        print(f"         python scripts/mqtt_broker.py --port {args.port + 1} "
              f"--ws-port {(args.ws_port or 9002) + 1}")
        return 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("transitions").setLevel(logging.WARNING)

    try:
        asyncio.run(serve(args.port, args.ws_port))
    except KeyboardInterrupt:
        pass
    except BrokerError as exc:
        print(f"[Broker] Could not start: {exc}")
        print("[Broker] Another broker may still be shutting down; wait a second and retry.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
