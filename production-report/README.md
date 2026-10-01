# Production report

"Which printer fails most?", "what did we actually print last month?" and "how much
filament went in the bin?" are questions a farm answers from memory until it grows
past the point where memory works. This pulls every print attempt for a date range
into a CSV for your spreadsheet or accounting system, and prints the summary you
would build from it anyway.

```
$ python production_report.py --from 2026-09-24 --to 2026-09-30 --csv week.csv
Print history 2026-09-24 → 2026-09-30 (America/Chicago): 115 attempt(s)

Wrote every attempt to week.csv

OVERALL
  100 completed · 10 failed · 5 cancelled
  success rate      90.9%  (completed ÷ completed + failed)
  printing time     291.9 h  (41.7 h/day)
  filament          2.95 kg  (0.10 kg on prints that did not complete)

BY PRINTER
  Printer                  Done  Failed  Fail %    Hours  Util %      Kg
  P1S-03                     27       2      7%     74.7     44%    1.00
  A1-05                      19       2     10%     60.2     36%    0.64
  X1C-01                     15       2     12%     56.4     34%    0.67
  ...

MOST PRINTED
     40  OpenSCAD Sample
     15  3DBenchy
  ...

WHY PRINTS FAILED
     11  Nozzle temperature malfunction
     10  Filament ran out mid-print
  ...
```

## Run it

```bash
cp .env.example .env            # add your API key
pip install -r requirements.txt
python production_report.py                                    # the last 7 days
python production_report.py --from 2026-09-01 --to 2026-09-30 --csv september.csv
```

Dates are whole days in your store's timezone, the same days the **Reports** page in
Printago uses.

**API key permissions:** `queue.view`, and `analytics.view` so the report can use your
store's timezone (without it, days are UTC days).

## The CSV

One row per attempt: a job that failed and was retried appears twice, once per
attempt.

`ended_at, status, printer, printer_model, part, sku, label, order_id, attempt,
started_at, print_minutes, estimated_minutes, filament_grams, error, print_job_id`

`print_minutes` against `estimated_minutes` is a quick check on how far your slicer
estimates drift for each part and printer.

## How it works

`GET /v1/print-history` returns one row per time a job ran on a printer, written as
the attempt ends and never edited afterwards. Each row already carries the printer
name, part, SKU, order, duration and grams, so no other lookups are needed, and a
report stays correct after a printer or part is renamed or deleted.

```http
GET /v1/print-history?endedAt.gte=2026-09-01T00:00:00-05:00&endedAt.lt=2026-10-01T00:00:00-05:00&sort=endedAt:asc&limit=1000&offset=0&meta=true
```

With `meta=true` the answer is `{ data, meta: { total, hasMore } }`; the client keeps
asking with a larger `offset` until `hasMore` is false. The store's timezone comes
from the `range.timezone` of `GET /v1/reports/summary`.

## Make it yours

- **Send it on a schedule.** Run it from cron every Monday with `--csv` and mail the
  file.
- **Cost it.** Multiply `filament_grams` by your cost per gram, per material, for
  cost of goods per SKU.
- **Bill customers.** Filter the CSV by `order_id` for per-order print time and
  material.
