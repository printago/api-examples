#!/usr/bin/env python3
"""Upload a model to Printago and print it, from the command line.

    python upload_and_print.py benchy.stl --quantity 3
    python upload_and_print.py organizer.3mf --plates 1:2,3:1 --front
    python upload_and_print.py --part-id c8x2dorgp40blck610w4bgg4 --quantity 5 --tags PLA|0.4mm

The whole flow, in order:
  1. POST /v1/storage/signed-upload-urls   get somewhere to put the file
  2. PUT  <uploadUrl>                      send the bytes straight to storage
  3. POST /v1/parts                        turn the upload into a part
  4. GET  /v1/parts/{id}                   wait until processing finishes
  5. POST /v2/builds                       queue copies for printing
  6. GET  /v1/print-jobs?partBuildId=...   follow the jobs until they finish
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import requests

from printago import Printago, PrintagoError, printago_from_env

# File extension -> Printago part type. Python models are ambiguous
# (CadQuery or build123d), so those need --type.
TYPES_BY_EXTENSION = {
    ".gcode.3mf": "gcode3mf",
    ".3mf": "3mf",
    ".stl": "stl",
    ".step": "step",
    ".stp": "step",
    ".gcode": "gcode",
    ".scad": "scad",
}
FINISHED = {"completed", "failed", "cancelled"}


def main() -> None:
    args = parse_args()
    api = printago_from_env()

    try:
        part_id = args.part_id or upload(api, args.file, args.type, args.name)
        part = wait_until_ready(api, part_id)
        build = queue(api, part, args)
        if args.no_follow:
            print(f"\nDone. Follow along in the Printago queue (part build {build['id']}).")
            return
        follow(api, build["id"])
    except PrintagoError as error:
        sys.exit(f"\n✗ {error}")
    except KeyboardInterrupt:
        print("\nStopped watching. The jobs stay in the queue.")


def upload(api: Printago, path: str, part_type: str | None, name: str | None) -> str:
    filename = os.path.basename(path)
    part_type = part_type or detect_type(filename)
    step(1, f"Uploading {filename} ({os.path.getsize(path) / 1024:,.0f} KB)")

    # The filename becomes the file's name in storage, so keep the real one.
    signed = api.post("/v1/storage/signed-upload-urls", {"filenames": [filename]})["signedUrls"][0]

    # The upload goes straight to storage. It must NOT carry the Printago
    # Authorization header, so it does not use the API session. Use the method
    # the API tells you to (PUT in production).
    with open(path, "rb") as f:
        response = requests.request(
            signed["method"],
            signed["uploadUrl"],
            data=f,
            headers={"content-type": "application/octet-stream"},
            timeout=600,
        )
    response.raise_for_status()

    step(2, "Creating the part")
    part = api.post(
        "/v1/parts",
        {
            "name": name or strip_extension(filename),
            "type": part_type,
            "description": "",
            "fileUris": [signed["path"]],  # the "uploads:..." path from step 1
            "parameters": [],
            "printTags": {},
            "overriddenProcessProfileId": None,
        },
        # Return immediately; we poll below. Without this the call waits up
        # to ~20 seconds for processing on its own.
        **{"async": True},
    )
    print(f"   part {part['id']}")
    return part["id"]


def wait_until_ready(api: Printago, part_id: str) -> dict:
    step(3, "Waiting for Printago to process the file")
    deadline = time.time() + 15 * 60
    while True:
        part = api.get(f"/v1/parts/{part_id}")
        status = part.get("processingStatus")
        if status is None:
            print(f"\r   ✓ {part['name']} is ready to print".ljust(60))
            return part
        if status == "failed":
            processing = part.get("processing") or {}
            sys.exit(
                f"   ✗ Processing failed at '{processing.get('failedStage')}': {processing.get('error')}\n"
                "     Fix the file and upload it again, or retry with POST /v1/parts/retry-processing."
            )
        if time.time() > deadline:
            sys.exit("   ✗ Still processing after 15 minutes. Check the part in Printago.")
        stages = (part.get("processing") or {}).get("stages") or []
        running = next((s["key"] for s in stages if s["status"] == "running"), None)
        print(f"\r   … {running or 'processing'}".ljust(60), end="", flush=True)
        time.sleep(3)


def queue(api: Printago, part: dict, args: argparse.Namespace) -> dict:
    step(4, "Queueing the print")
    entry: dict = {"partId": part["id"], "position": "front" if args.front else "back"}
    if args.label:
        entry["label"] = args.label
    if args.plates:
        # Only for 3MF parts: copies per plate, e.g. {"1": 2, "3": 1}.
        entry["plateQuantities"] = dict(pair.split(":") for pair in args.plates.split(","))
        entry["plateQuantities"] = {k: int(v) for k, v in entry["plateQuantities"].items()}
    else:
        entry["quantity"] = args.quantity
    if args.tags:
        # Only printers carrying every one of these tags will take the jobs.
        entry["tags"] = {"user.tags": args.tags}

    body = {"parts": [entry]}
    # Dry run first: see exactly which jobs this will create.
    preview = api.post("/v2/builds/preview", body)
    jobs = preview["summary"]["jobCount"]
    print(f"   {jobs} print job{'s' if jobs != 1 else ''} will be created")

    build = api.post("/v2/builds", body)["partBuilds"][0]
    print(f"   ✓ queued (part build {build['id']})")
    return build


def follow(api: Printago, build_id: str) -> None:
    step(5, "Following the jobs (Ctrl+C to stop watching)")
    last: dict[str, str] = {}
    printers: dict[str, str] = {}
    while True:
        jobs = api.get("/v1/print-jobs", partBuildId=build_id, sort="quantityIndex:asc", limit=1000)
        for job in jobs:
            if last.get(job["id"]) == job["status"]:
                continue
            last[job["id"]] = job["status"]
            where = ""
            if job.get("assignedPrinterId"):
                if job["assignedPrinterId"] not in printers:
                    printers.update({p["id"]: p["name"] for p in api.get("/v1/printers", limit=1000)})
                where = f" on {printers.get(job['assignedPrinterId'], job['assignedPrinterId'])}"
            error = f": {job['errorMessage']}" if job["status"] == "failed" and job.get("errorMessage") else ""
            print(f"   {time.strftime('%H:%M:%S')}  {job['label']} #{job['quantityIndex']}  →  {job['status']}{where}{error}")
        if jobs and all(job["status"] in FINISHED for job in jobs):
            done = sum(job["status"] == "completed" for job in jobs)
            print(f"\nAll jobs finished: {done} of {len(jobs)} completed.")
            return
        # 600 requests/hour is shared by the whole store; every 20s is gentle.
        time.sleep(20)


def detect_type(filename: str) -> str:
    lower = filename.lower()
    for extension, part_type in TYPES_BY_EXTENSION.items():
        if lower.endswith(extension):
            return part_type
    sys.exit(f"Can't tell the part type of {filename}. Pass --type (e.g. --type cadquery).")


def strip_extension(filename: str) -> str:
    lower = filename.lower()
    for extension in TYPES_BY_EXTENSION:
        if lower.endswith(extension):
            return filename[: -len(extension)]
    return os.path.splitext(filename)[0]


def step(number: int, text: str) -> None:
    print(f"\n[{number}] {text}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a model to Printago and print it.")
    parser.add_argument("file", nargs="?", help="model file: .stl .3mf .gcode.3mf .step .scad .gcode .py")
    parser.add_argument("--part-id", help="print an existing part instead of uploading a file")
    parser.add_argument("--name", help="part name (default: the file name)")
    parser.add_argument("--type", help="part type, needed for Python models: cadquery or build123d")
    parser.add_argument("--quantity", type=int, default=1, help="copies to print (default 1)")
    parser.add_argument("--plates", help="3MF only: copies per plate, e.g. 1:2,3:1")
    parser.add_argument("--label", help="how the jobs are named in the queue (an order number, say)")
    parser.add_argument("--tags", help="printer tags required, pipe-separated: PLA|0.4mm")
    parser.add_argument("--front", action="store_true", help="put the jobs at the front of the queue")
    parser.add_argument("--no-follow", action="store_true", help="exit once queued instead of following the jobs")
    args = parser.parse_args()
    if not args.file and not args.part_id:
        parser.error("give a file to upload, or --part-id")
    if args.file and not os.path.isfile(args.file):
        parser.error(f"no such file: {args.file}")
    return args


if __name__ == "__main__":
    main()
