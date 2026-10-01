#!/usr/bin/env python3
"""Prometheus exporter for a Printago print farm.

Subscribes to live printer stats over MQTT and serves them as Prometheus
metrics, so you can graph your farm in Grafana and alert on it with
Alertmanager (a printer offline for 10 minutes, a failed print, a bed
that never heats).

    python printago_exporter.py            # metrics on http://localhost:9877/metrics

MQTT does the heavy lifting: messages are pushed as printers change, and the
exporter answers every scrape from memory, so scraping every 15 seconds costs
nothing against your API rate limit. REST is only used to look up printer
names, every 10 minutes.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from prometheus_client.registry import Collector

from printago import PrintagoError, printago_from_env, require_env

log = logging.getLogger("printago-exporter")

# States printers report, exported one-hot so `printago_printer_state{state="RUNNING"} == 1` works.
KNOWN_STATES = ["IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH", "FAILED"]


class Farm:
    """The latest stat for every printer, plus printer names. Shared between threads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stats: dict[str, dict] = {}
        self.printers: dict[str, dict] = {}
        self.connected = False

    def update(self, printer_id: str, stat: dict | None) -> None:
        with self.lock:
            if stat is None:
                self.stats.pop(printer_id, None)  # printer removed
            else:
                self.stats[printer_id] = stat

    def snapshot(self) -> tuple[dict[str, dict], dict[str, dict], bool]:
        with self.lock:
            return dict(self.stats), dict(self.printers), self.connected


class FarmCollector(Collector):
    """Builds the metrics at scrape time from whatever MQTT delivered last."""

    def __init__(self, farm: Farm) -> None:
        self.farm = farm

    def collect(self):
        stats, printers, connected = self.farm.snapshot()
        labels = ["printer_id", "printer", "model"]

        up = GaugeMetricFamily("printago_mqtt_connected", "1 while the exporter is connected to Printago MQTT")
        up.add_metric([], 1 if connected else 0)
        yield up

        online = GaugeMetricFamily("printago_printer_online", "1 if the printer is online", labels=labels)
        enabled = GaugeMetricFamily("printago_printer_enabled", "1 if the printer is enabled in Printago", labels=labels)
        state = GaugeMetricFamily("printago_printer_state", "1 for the state the printer reports", labels=labels + ["state"])
        progress = GaugeMetricFamily("printago_print_progress_ratio", "Progress of the current print, 0 to 1", labels=labels)
        remaining = GaugeMetricFamily("printago_print_remaining_seconds", "Time left on the current print", labels=labels)
        layer = GaugeMetricFamily("printago_print_layer", "Current layer of the current print", labels=labels)
        layers = GaugeMetricFamily("printago_print_layers_total", "Layers in the current print", labels=labels)
        temperature = GaugeMetricFamily(
            "printago_temperature_celsius", "Temperatures by component (nozzle_0, bed_0, chamber_0...)", labels=labels + ["component", "kind"]
        )
        errors = GaugeMetricFamily("printago_printer_errors", "Active errors the printer reports", labels=labels)
        warnings = GaugeMetricFamily("printago_printer_warnings", "Active warnings the printer reports", labels=labels)
        updated = GaugeMetricFamily("printago_printer_last_update_timestamp_seconds", "When the printer last reported", labels=labels)

        for printer_id in sorted(set(stats) | set(printers)):
            printer = printers.get(printer_id, {})
            stat = stats.get(printer_id)
            ids = [printer_id, printer.get("name", printer_id), printer.get("modelName") or ""]
            enabled.add_metric(ids, 1 if printer.get("enabled", True) else 0)
            if stat is None:
                online.add_metric(ids, 0)
                continue

            # Printers re-report about once a minute; past expiresAt the stat is stale.
            expires = parse_time(stat.get("expiresAt"))
            fresh = expires is None or expires > time.time()
            online.add_metric(ids, 1 if stat.get("isOnline") and fresh else 0)
            current = (stat.get("state") or "").upper()
            for name in KNOWN_STATES + ([current] if current and current not in KNOWN_STATES else []):
                state.add_metric(ids + [name], 1 if name == current else 0)

            data = stat.get("data") or {}
            print_data = data.get("print") or {}
            if current in ("PREPARE", "RUNNING", "PAUSE"):
                if print_data.get("percent") is not None:
                    progress.add_metric(ids, print_data["percent"] / 100)
                if print_data.get("timeRemaining") is not None:
                    remaining.add_metric(ids, print_data["timeRemaining"] * 60)  # reported in minutes
                if print_data.get("totalLayers"):
                    layer.add_metric(ids, print_data.get("currentLayer") or 0)
                    layers.add_metric(ids, print_data["totalLayers"])

            for component, reading in (data.get("temperatures") or {}).items():
                for kind in ("current", "target"):
                    if isinstance(reading, dict) and reading.get(kind) is not None:
                        temperature.add_metric(ids + [component, kind], reading[kind])

            health = data.get("health") or {}
            errors.add_metric(ids, len(health.get("errors") or []))
            warnings.add_metric(ids, len(health.get("warnings") or []))

            received = parse_time(stat.get("receivedAt"))
            if received:
                updated.add_metric(ids, received)

        yield from (online, enabled, state, progress, remaining, layer, layers, temperature, errors, warnings, updated)


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    # Timestamps can carry nanoseconds; Python parses microseconds at most.
    head, _, rest = value.rstrip("Z").partition(".")
    try:
        return datetime.fromisoformat(f"{head}.{rest[:6] or '0'}+00:00").timestamp()
    except ValueError:
        return None


def refresh_printers(farm: Farm) -> None:
    """Printer names and models from REST, every 10 minutes."""
    api = printago_from_env()
    while True:
        try:
            printers = api.get("/v1/printers", limit=1000)
            with farm.lock:
                farm.printers = {p["id"]: p for p in printers}
        except (PrintagoError, OSError) as error:
            log.warning("could not refresh printers: %s", error)
        time.sleep(600)


def connect_mqtt(farm: Farm, api_key: str, store_id: str) -> mqtt.Client:
    url = urlparse(os.environ.get("PRINTAGO_MQTT_URL") or "mqtts://realtime.printago.io:8883")

    # Client ID: apiclient_{first 24 chars of the key}_{suffix}. The suffix is
    # generated once per process and reused across reconnects; two connections
    # with the same client ID knock each other off the broker forever.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"apiclient_{api_key[:24]}_{secrets.token_hex(4)}",
    )
    client.username_pw_set(store_id, api_key)
    if url.scheme in ("mqtts", "ssl"):
        client.tls_set()
    topic = f"stores/{store_id}/printer-stats/#"

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        farm.connected = True
        log.info("connected to %s, subscribing to printer stats", url.hostname)
        client.subscribe(topic)  # retained messages bring every printer's current state

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
        farm.connected = False
        log.warning("MQTT disconnected (%s); reconnecting", reason_code)

    def on_message(_client, _userdata, message):
        printer_id = message.topic.rsplit("/", 1)[-1]
        if not message.payload:
            farm.update(printer_id, None)  # an empty retained message clears a removed printer
            return
        try:
            farm.update(printer_id, json.loads(message.payload))
        except ValueError:
            log.warning("ignoring a message that is not JSON on %s", message.topic)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(url.hostname, url.port or (8883 if url.scheme in ("mqtts", "ssl") else 1883), keepalive=60)
    return client


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = require_env("PRINTAGO_API_KEY")
    store_id = require_env("PRINTAGO_STORE_ID")
    port = int(os.environ.get("PORT", "9877"))
    host = os.environ.get("HOST", "0.0.0.0")

    farm = Farm()
    REGISTRY.register(FarmCollector(farm))
    threading.Thread(target=refresh_printers, args=(farm,), daemon=True).start()

    start_http_server(port, addr=host)
    log.info("metrics on http://%s:%d/metrics", host, port)

    client = connect_mqtt(farm, api_key, store_id)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
