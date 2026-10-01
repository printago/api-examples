# Prometheus exporter

Farms that already run Prometheus and Grafana for everything else want the printers
on the same dashboards and paging through the same Alertmanager, not in a separate
app someone has to remember to check. This exporter serves live printer metrics from
Printago in Prometheus format.

```
printago_printer_online{printer="P1S-03",model="Bambu Lab P1S",printer_id="bqc3..."} 1
printago_printer_state{printer="P1S-03",...,state="RUNNING"} 1
printago_print_progress_ratio{printer="P1S-03",...} 0.74
printago_print_remaining_seconds{printer="P1S-03",...} 60
printago_temperature_celsius{printer="P1S-03",...,component="nozzle_0",kind="current"} 220
printago_printer_errors{printer="P1S-03",...} 0
```

## Run it

```bash
cp .env.example .env            # add your API key and store ID
pip install -r requirements.txt
python printago_exporter.py     # http://localhost:9877/metrics
```

Then point Prometheus at it:

```yaml
scrape_configs:
  - job_name: printago
    scrape_interval: 15s
    static_configs:
      - targets: ["exporter-host:9877"]
rule_files:
  - alerts.yml     # the alert rules in this folder
```

**API key permissions:** `printer.view`, `printer.stats`.

## Metrics

All printer metrics carry `printer_id`, `printer` (name) and `model` labels.

| Metric | |
|---|---|
| `printago_printer_online` | 1 if online and reporting |
| `printago_printer_enabled` | 1 if enabled in Printago |
| `printago_printer_state{state}` | 1 for the state the printer reports: `IDLE`, `PREPARE`, `RUNNING`, `PAUSE`, `FINISH`, `FAILED` |
| `printago_print_progress_ratio` | Progress of the current print, 0 to 1 |
| `printago_print_remaining_seconds` | Time left on the current print |
| `printago_print_layer`, `printago_print_layers_total` | Current layer and layer count |
| `printago_temperature_celsius{component,kind}` | `component` is `nozzle_0`, `bed_0`, `chamber_0`...; `kind` is `current` or `target` |
| `printago_printer_errors`, `printago_printer_warnings` | Active errors and warnings the printer reports |
| `printago_printer_last_update_timestamp_seconds` | When the printer last reported |
| `printago_mqtt_connected` | 1 while the exporter is connected to Printago |

Some queries to start a Grafana board with:

```promql
# Printers printing right now
sum(printago_printer_state{state="RUNNING"})

# Farm utilization over the last day
avg_over_time(sum(printago_printer_state{state="RUNNING"})[1d:5m]) / count(printago_printer_online)

# Estimated finish time of each print, for a table panel
time() + printago_print_remaining_seconds
```

[`alerts.yml`](alerts.yml) has rules for a printer offline for 10 minutes, a failed
print, a printer reporting an error, a finished print waiting over 30 minutes for
its bed to be cleared, and the exporter losing its connection.

## How it works

It subscribes to `stores/{storeId}/printer-stats/#` over MQTT and keeps the latest
stat per printer in memory. Each scrape builds the metrics from that memory, so
scraping every 15 seconds costs nothing against your API rate limit. REST is only
used for printer names, every 10 minutes.

A stat past its `expiresAt` counts as offline: printers re-report about once a minute,
so a stat that has expired means the printer stopped reporting.
