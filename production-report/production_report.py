#!/usr/bin/env python3
"""Production report: what your farm printed, as a CSV and a summary.

    python production_report.py                               # last 7 days
    python production_report.py --from 2026-09-01 --to 2026-09-30 --csv september.csv

Reads print history (GET /v1/print-history): one row per time a job ran on a
printer, with its outcome, timing, printer and filament used. Writes every
attempt to a CSV for your spreadsheet or accounting system, and prints a
summary by printer, by part and by failure reason.

Dates are whole days in your store's timezone, the same days the Reports page
in Printago uses.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from printago import Printago, PrintagoError, printago_from_env

CSV_COLUMNS = [
    ("ended_at", "endedAt"),
    ("status", "finalStatus"),
    ("printer", "printerName"),
    ("printer_model", "printerModelName"),
    ("part", "partName"),
    ("sku", "skuName"),
    ("label", "label"),
    ("order_id", "orderId"),
    ("attempt", "attemptNumber"),
    ("started_at", "printingStartedAt"),
    ("print_minutes", None),
    ("estimated_minutes", None),
    ("filament_grams", "totalUsedGrams"),
    ("error", "errorMessage"),
    ("print_job_id", "printJobId"),
]


def main() -> None:
    args = parse_args()
    api = printago_from_env()
    try:
        run(api, args)
    except PrintagoError as error:
        sys.exit(f"✗ {error}")


def run(api: Printago, args: argparse.Namespace) -> None:
    first, last = args.start, args.end
    zone = store_timezone(api, first, last)
    # [start of first day, start of the day after last) in the store's timezone.
    start = datetime.combine(first, time.min, zone)
    end = datetime.combine(last + timedelta(days=1), time.min, zone)

    attempts = list(
        api.paginate(
            "/v1/print-history",
            page_size=1000,
            **{"endedAt.gte": iso(start), "endedAt.lt": iso(end), "sort": "endedAt:asc"},
        )
    )
    print(f"Print history {first} → {last} ({zone.key}): {len(attempts)} attempt(s)\n")
    if not attempts:
        return

    if args.csv:
        write_csv(args.csv, attempts)
        print(f"Wrote every attempt to {args.csv}\n")

    summarize(attempts, (end - start).days)


def store_timezone(api: Printago, first: date, last: date) -> ZoneInfo:
    """The Reports API answers in the store's timezone; use the same one.

    Needs the analytics.view permission; without it, days are UTC days.
    """
    try:
        report = api.get("/v1/reports/summary", start=first.isoformat(), end=last.isoformat())
        return ZoneInfo(report["range"]["timezone"])
    except (PrintagoError, KeyError, ValueError):
        return ZoneInfo("UTC")


def write_csv(path: str, attempts: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(name for name, _ in CSV_COLUMNS)
        for a in attempts:
            row = []
            for name, field in CSV_COLUMNS:
                if name == "print_minutes":
                    row.append(minutes(a.get("printDurationSeconds")))
                elif name == "estimated_minutes":
                    row.append(minutes(a.get("estimatedPrintTimeSeconds")))
                else:
                    value = a.get(field)
                    row.append("" if value is None else value)
            writer.writerow(row)


def summarize(attempts: list[dict], days: int) -> None:
    outcomes = Counter(a.get("finalStatus") or "unknown" for a in attempts)
    finished = outcomes["completed"] + outcomes["failed"]
    hours = sum(a.get("printDurationSeconds") or 0 for a in attempts) / 3600
    grams = sum(a.get("totalUsedGrams") or 0 for a in attempts)
    wasted = sum(a.get("totalUsedGrams") or 0 for a in attempts if a.get("finalStatus") != "completed")

    print("OVERALL")
    print(f"  {outcomes['completed']} completed · {outcomes['failed']} failed · {outcomes['cancelled']} cancelled")
    if finished:
        print(f"  success rate      {outcomes['completed'] / finished:.1%}  (completed ÷ completed + failed)")
    print(f"  printing time     {hours:,.1f} h  ({hours / days:,.1f} h/day)")
    print(f"  filament          {grams / 1000:,.2f} kg  ({wasted / 1000:,.2f} kg on prints that did not complete)")

    by_printer: dict[str, dict] = defaultdict(lambda: {"completed": 0, "failed": 0, "hours": 0.0, "grams": 0.0})
    for a in attempts:
        row = by_printer[a.get("printerName") or "(deleted printer)"]
        if a.get("finalStatus") in ("completed", "failed"):
            row[a["finalStatus"]] += 1
        row["hours"] += (a.get("printDurationSeconds") or 0) / 3600
        row["grams"] += a.get("totalUsedGrams") or 0

    print("\nBY PRINTER")
    print(f"  {'Printer':<22} {'Done':>6} {'Failed':>7} {'Fail %':>7} {'Hours':>8} {'Util %':>7} {'Kg':>7}")
    for name, row in sorted(by_printer.items(), key=lambda kv: -kv[1]["hours"]):
        if not (row["completed"] or row["failed"] or row["hours"]):
            continue  # cancelled before it ever reached a printer
        total = row["completed"] + row["failed"]
        fail = f"{row['failed'] / total:.0%}" if total else "–"
        utilization = row["hours"] / (days * 24)
        print(
            f"  {name[:22]:<22} {row['completed']:>6} {row['failed']:>7} {fail:>7} "
            f"{row['hours']:>8.1f} {utilization:>7.0%} {row['grams'] / 1000:>7.2f}"
        )

    parts = Counter(a.get("skuName") or a.get("partName") or "?" for a in attempts if a.get("finalStatus") == "completed")
    print("\nMOST PRINTED")
    for name, count in parts.most_common(8):
        print(f"  {count:>5}  {name}")

    failures = Counter((a.get("errorMessage") or "no reason recorded") for a in attempts if a.get("finalStatus") == "failed")
    if failures:
        print("\nWHY PRINTS FAILED")
        for reason, count in failures.most_common(6):
            print(f"  {count:>5}  {reason[:90]}")


def minutes(seconds: float | None) -> str:
    return "" if seconds is None else f"{seconds / 60:.1f}"


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize and export your Printago print history.")
    today = date.today()
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=today - timedelta(days=6), help="first day, YYYY-MM-DD (default: 6 days ago)")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=today, help="last day, YYYY-MM-DD (default: today)")
    parser.add_argument("--csv", help="also write every attempt to this CSV file")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--to is before --from")
    return args


if __name__ == "__main__":
    main()
