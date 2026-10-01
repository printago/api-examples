#!/usr/bin/env python3
"""Queue a batch of prints from a CSV file.

Export a production plan, a pick list or a day's orders from another system
(ERP, spreadsheet, MES), then:

    python csv_batch_print.py batch.csv            # preview, then ask before queueing
    python csv_batch_print.py batch.csv --yes      # queue without asking

Each row is either a SKU or a part:

    sku,part,quantity,label,options,tags
    BENCHY,,4,Order 1042,Color=Red,
    ,Cable Clip,20,Restock,,PETG
    ,p8f2k6m1q9w3e7r5t0y4u2i6,1,,,

  sku       a SKU code, exactly as in Printago (pick variants with `options`)
  part      a part name or ID, when the row is not a SKU
  quantity  copies (SKU units for a SKU row); default 1
  label     optional: how the jobs are named in the queue
  options   optional, SKU rows: variant choices, "Color=Red; Size=L"
  tags      optional: printer tags the jobs need, "PLA|0.4mm"

The whole file is checked before anything is queued, and everything is sent in
one POST /v2/builds, after a POST /v2/builds/preview dry run shows you exactly
which jobs it will create.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from printago import Printago, PrintagoError, printago_from_env


def main() -> None:
    args = parse_args()
    api = printago_from_env()

    rows = read_rows(args.csv)
    print(f"Read {len(rows)} row(s) from {args.csv}")

    try:
        body = build_request(api, rows)
        preview = api.post("/v2/builds/preview", body)
        show_preview(preview)

        if not args.yes and input("\nQueue these prints? [y/N] ").strip().lower() != "y":
            print("Nothing queued.")
            return

        result = api.post("/v2/builds", body)
        builds = len(result["partBuilds"]) + sum(len(s.get("partBuilds") or []) for s in result["skuBuilds"])
        print(
            f"\n✓ Queued {preview['summary']['jobCount']} job(s) "
            f"({len(result['skuBuilds'])} SKU build(s), {builds} part build(s))."
        )
    except PrintagoError as error:
        sys.exit(f"\n✗ {error}")


def read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {"quantity"} - set(reader.fieldnames or [])
        if missing or not ({"sku", "part"} & set(reader.fieldnames or [])):
            sys.exit("The CSV needs a header row with `quantity` and a `sku` and/or `part` column.")
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in reader
            if any((v or "").strip() for v in row.values())
        ]


def build_request(api: Printago, rows: list[dict]) -> dict:
    """Turn CSV rows into a /v2/builds body, failing on any row that would not print."""
    # Look everything up once, not once per row.
    skus = {s["sku"].lower(): s for s in api.paginate("/v1/skus")}
    parts = list(api.paginate("/v1/parts"))
    parts_by_id = {p["id"]: p for p in parts}
    parts_by_name: dict[str, list[dict]] = {}
    for p in parts:
        parts_by_name.setdefault(p["name"].lower(), []).append(p)

    body: dict = {"parts": [], "skus": []}
    problems: list[str] = []

    for number, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"row {number}"
        try:
            quantity = int(row.get("quantity") or 1)
            if quantity < 1:
                raise ValueError
        except ValueError:
            problems.append(f"{where}: quantity must be a whole number of at least 1")
            continue

        entry: dict = {"quantity": quantity}
        if row.get("label"):
            entry["label"] = row["label"]
        if row.get("tags"):
            entry["tags"] = {"user.tags": row["tags"]}

        if row.get("sku"):
            code = row["sku"]
            # Unknown SKU codes are skipped by the API rather than rejected, so
            # check them here. Variant codes like BENCHY-RED are only resolved
            # for orders (POST /v1/orders); for builds, name the options instead.
            if code.lower() not in skus:
                problems.append(f"{where}: no SKU with code {code!r}")
                continue
            entry["sku"] = skus[code.lower()]["sku"]
            if row.get("options"):
                try:
                    entry["options"] = parse_options(row["options"])
                except ValueError as error:
                    problems.append(f"{where}: {error}")
                    continue
            entry.pop("label", None)  # SKU jobs are labelled from the SKU
            body["skus"].append(entry)
        elif row.get("part"):
            ref = row["part"]
            part = parts_by_id.get(ref)
            if not part:
                matches = parts_by_name.get(ref.lower(), [])
                if len(matches) > 1:
                    problems.append(f"{where}: {len(matches)} parts are named {ref!r}; use the part ID")
                    continue
                part = matches[0] if matches else None
            if not part:
                problems.append(f"{where}: no part named {ref!r}")
                continue
            if part.get("processingStatus") is not None:
                problems.append(f"{where}: part {part['name']!r} is {part['processingStatus']}, not ready to print")
                continue
            entry["partId"] = part["id"]
            body["parts"].append(entry)
        else:
            problems.append(f"{where}: give a sku or a part")

    if problems:
        sys.exit("Fix these rows first, nothing was queued:\n  " + "\n  ".join(problems))
    return {k: v for k, v in body.items() if v}


def parse_options(text: str) -> list[dict]:
    """ "Color=Red; Size=L" -> [{"name": "Color", "value": "Red"}, {"name": "Size", "value": "L"}] """
    options = []
    for pair in filter(None, (p.strip() for p in text.split(";"))):
        name, sep, value = pair.partition("=")
        if not sep or not name.strip() or not value.strip():
            raise ValueError(f"options must look like 'Color=Red; Size=L', got {pair!r}")
        options.append({"name": name.strip(), "value": value.strip()})
    return options


def show_preview(preview: dict) -> None:
    print("\nThis will queue:\n")
    print(f"  {'Jobs':>4}  {'From':<16}  Label")
    for build in preview["partBuilds"]:
        source = build["sku"]["sku"] if build.get("sku") else "part"
        plates = Counter(job.get("plateName") for job in build["jobs"] if job.get("plateName"))
        detail = f"  ({len(plates)} plates)" if len(plates) > 1 else ""
        print(f"  {len(build['jobs']):>4}  {source:<16}  {build['label']}{detail}")
    s = preview["summary"]
    print(f"\n  {s['jobCount']} job(s) in total")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue a batch of Printago prints from a CSV file.")
    parser.add_argument("csv", help="path to the CSV file")
    parser.add_argument("--yes", "-y", action="store_true", help="queue without asking for confirmation")
    return parser.parse_args()


if __name__ == "__main__":
    main()
