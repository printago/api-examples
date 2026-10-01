# Printago API examples

Working reference apps for the [Printago API](https://developers.printago.io): the
jobs people most often automate around a print farm, each written the way we would
write it ourselves.

Printago is 3D print farm software that runs production from order to finished
print. It works for a side hustle with three printers and for a facility with three
hundred. These examples are for the moment your farm needs to talk to something
Printago does not connect to out of the box: a wall display, a chat channel, an ERP,
a storefront, a monitoring stack. (For Discord, Slack, Telegram or webhook alerts,
use **Settings → Notifications** in Printago; no code needed.)

Each folder stands on its own. Copy one out, fill in `.env`, and run it.

| Example | What it does | Uses | Language |
|---|---|---|---|
| [live-printer-dashboard](live-printer-dashboard) | A wall-display dashboard of every printer: state, progress, time left, temperatures, loaded filament | MQTT printer stats | TypeScript |
| [upload-and-print](upload-and-print) | Upload an STL, 3MF or STEP file from the command line and print N copies | Storage, parts, builds | Python |
| [parts-catalog-kiosk](parts-catalog-kiosk) | A touch-friendly catalog of your parts with a Print button, for makerspaces, classrooms and shop floors | Parts, thumbnails, builds | TypeScript |
| [order-intake-webhook](order-intake-webhook) | Receive orders from any storefront or ERP, create them in Printago and print them | Orders | TypeScript |
| [csv-batch-print](csv-batch-print) | Queue a production plan from a CSV: checks every row, previews the jobs, then queues | Builds preview | Python |
| [queue-control](queue-control) | List, pause, resume, cancel, retry and reorder jobs from the terminal | Print jobs | Python |
| [production-report](production-report) | What printed, by printer, part and failure reason, exported to CSV | Print history | Python |
| [prometheus-exporter](prometheus-exporter) | Printer metrics for Prometheus and Grafana, with alert rules | MQTT printer stats | Python |
| [camera-snapshot-archiver](camera-snapshot-archiver) | Save every camera snapshot, one folder per print, for review or timelapses | MQTT + snapshots | TypeScript |

## Before you start

1. **Create an API key** in Printago under **Settings → API Keys**. Give it only the
   permissions the example's README lists. A key belongs to one store, so it is all
   the REST API needs.
2. **Copy your store ID** from the same page. The MQTT examples use it as the MQTT
   username.
3. **Install a runtime.** TypeScript examples need Node.js 20.12 or newer
   (`npm install`, then `npm start`). Python examples need Python 3.10 or newer
   (`pip install -r requirements.txt`).

## Things every integration should get right

These are baked into every example. They are the difference between an integration
that runs for a year and one that gets rate limited on day two.

**Use MQTT for anything live.** The API allows 600 requests an hour per store,
shared by every key and client. Polling printer status burns through that in
minutes. MQTT pushes printer stats and entity changes the moment they happen, and
printer stats are retained, so you get every printer's current state as soon as you
subscribe. See [Real-time data with MQTT](https://docs.printago.io/api/authentication-and-quotas#real-time-data-with-mqtt).

**Generate the MQTT client ID suffix once per process.** The client ID is
`apiclient_{first 24 characters of your key}_{suffix}`. Two connections with the
same ID knock each other off the broker in a loop. A random suffix generated at
startup and reused across reconnects avoids it.

**Back off on HTTP 429.** A 429 does not say how long to wait. Wait 2 seconds, then
4, then 8. Each example's client does this for you.

**Treat a stat past `expiresAt` as offline.** Printers re-report about once a minute.
A retained stat whose `expiresAt` has passed means the printer stopped reporting.

**Builds are asynchronous.** `POST /v2/builds` answers as soon as the builds are
recorded. The print jobs appear seconds later. Follow them with
`GET /v1/print-jobs?partBuildId=...`, or watch the `print_jobs` MQTT topic.

**Keep the API key on a server.** The two browser apps here (dashboard and kiosk)
serve their pages from a small Node server that holds the key. The browser never
sees it.

## Repository layout

```
_shared/            the REST and MQTT client code the examples share
scripts/            sync-shared.mjs copies _shared/ into each example
<example>/          one self-contained app per folder
```

Each example carries its own copy of `printago.ts` or `printago.py` so it works when
copied out on its own. If you change a file in `_shared/`, run
`node scripts/sync-shared.mjs`; `node scripts/sync-shared.mjs --check` fails when a
copy has drifted.

## More

- API reference: [developers.printago.io](https://developers.printago.io)
- Guides: [docs.printago.io/api](https://docs.printago.io/api/authentication-and-quotas)
- Command line: the [`printago` CLI](https://docs.printago.io/api/cli) covers every endpoint
- Questions and show-and-tell: the [Printago Discord](https://discord.gg/RCFA2u99De)

MIT licensed. See [LICENSE](LICENSE).
