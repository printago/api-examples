#!/usr/bin/env python3
"""See and steer your Printago print queue from the terminal.

    python queue_control.py list                    # the active queue, in queue order
    python queue_control.py list --status failed    # just the failed jobs
    python queue_control.py pause   <job-id>...     # pause printing jobs
    python queue_control.py resume  <job-id>...
    python queue_control.py cancel  <job-id>...     # also stops the printer; --no-stop to leave it running
    python queue_control.py retry   <job-id>...     # re-queue finished (failed/cancelled) jobs
    python queue_control.py front   <job-id>...     # jump jobs to the front of the queue
    python queue_control.py retry-failed            # re-queue every failed job in the queue
    python queue_control.py cancel-pending --label "Order 1042"   # clear a batch that has not started

Job IDs are shown by `list`. Any unique start of an ID works (like git).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from printago import Printago, PrintagoError, printago_from_env

WAITING = ["pending"]
IN_PROGRESS = [
    "assigned",
    "slicing",
    "sliced",
    "finalizing",
    "waiting-for-approval",
    "ready-for-printer",
    "sending-to-printer",
    "printing",
    "paused",
]
FINISHED = ["completed", "failed", "cancelled"]

PAST_TENSE = {
    "pause": "paused",
    "resume": "resumed",
    "cancel": "cancelled",
    "retry": "re-queued",
    "front": "moved to the front",
}

COLORS = {"printing": "34", "paused": "33", "failed": "31", "completed": "32", "cancelled": "90", "pending": "37"}


def main() -> None:
    args = parse_args()
    api = printago_from_env()
    try:
        args.run(api, args)
    except PrintagoError as error:
        sys.exit(f"✗ {error}")


# ── Reading the queue ───────────────────────────────────────────────────────


def fetch_jobs(api: Printago, statuses: list[str]) -> list[dict]:
    """Jobs in the given statuses, as the Printago queue orders them."""
    return list(
        api.paginate(
            "/v1/print-jobs",
            page_size=1000,
            **{
                "status.in": statuses,
                # Archived (cleared) jobs are hidden in the app; hide them here too.
                "hidden": False,
                "sort": "priority:asc,queueOrder:asc,createdAt:asc",
            },
        )
    )


def list_jobs(api: Printago, args: argparse.Namespace) -> None:
    statuses = args.status.split(",") if args.status else IN_PROGRESS + WAITING
    jobs = fetch_jobs(api, statuses)
    if not jobs:
        print("The queue is empty." if not args.status else f"No {args.status} jobs.")
        return

    printers = printer_names(api)
    # In-progress jobs first, then the waiting line: the same order as the app.
    jobs.sort(key=lambda j: (j["status"] not in IN_PROGRESS,))
    labels = {j["id"]: job_label(j) for j in jobs}
    width = min(max(len(label) for label in labels.values()), 40)
    print(f"{'ID':<24}  {'STATUS':<20}  {'JOB':<{width}}  {'PRINTER':<14}  AGE")
    for job in jobs:
        label = labels[job["id"]]
        label = label if len(label) <= width else label[: width - 1] + "…"
        printer = printers.get(job.get("assignedPrinterId") or "", "")
        print(
            f"{job['id']:<24}  {paint(job['status'], 20)}  {label:<{width}}  {printer[:14]:<14}  {age(job['createdAt'])}"
        )
        if job["status"] == "failed" and job.get("errorMessage"):
            print(f"{'':<24}  └ {job['errorMessage']}")

    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    print("\n" + " · ".join(f"{n} {status}" for status, n in counts.items()))


def job_label(job: dict) -> str:
    copies = f" ({job['quantityIndex']}/{job['quantityTotal']})" if job["quantityTotal"] > 1 else ""
    return job["label"] + copies


def printer_names(api: Printago) -> dict[str, str]:
    return {p["id"]: p["name"] for p in api.get("/v1/printers", limit=1000)}


# ── Acting on jobs ──────────────────────────────────────────────────────────


def act(api: Printago, args: argparse.Namespace) -> None:
    """pause / resume / cancel / retry / front on the given job IDs."""
    endpoint, pool = {
        "pause": ("/v1/print-jobs/pause", IN_PROGRESS),
        "resume": ("/v1/print-jobs/resume", IN_PROGRESS),
        "cancel": ("/v1/print-jobs/cancel", IN_PROGRESS + WAITING),
        "retry": ("/v1/print-jobs/retry", FINISHED),
        "front": ("/v1/print-jobs/move-to-queue-front", WAITING),
    }[args.command]
    ids = resolve_ids(api, args.ids, pool)

    body: dict = {"printJobIds": ids}
    if args.command == "cancel":
        # Cancelling a printing job stops the printer too, unless told not to.
        body["sendStop"] = not args.no_stop

    # The response lists the IDs that changed. For `front` that includes every
    # job whose position shifted, so count only the ones we asked about.
    changed = set(api.patch(endpoint, body) or []) & set(ids)
    print(f"✓ {len(changed)} job(s) {PAST_TENSE[args.command]}")
    skipped = set(ids) - changed
    if skipped and args.command == "front":
        # Nothing to move: the job (and its group) already leads the queue.
        print(f"  {len(skipped)} job(s) were already at the front: {', '.join(sorted(skipped))}")
    elif skipped:
        print(f"  {len(skipped)} job(s) were not in a state that allows it: {', '.join(sorted(skipped))}")


def retry_failed(api: Printago, args: argparse.Namespace) -> None:
    jobs = fetch_jobs(api, ["failed"])
    if not jobs:
        print("No failed jobs in the queue.")
        return
    for job in jobs:
        print(f"  {job_label(job)}  ({job.get('errorMessage') or 'no error message'})")
    if confirm(f"Retry these {len(jobs)} job(s)?", args.yes):
        changed = api.patch("/v1/print-jobs/retry", {"printJobIds": [j["id"] for j in jobs]}) or []
        print(f"✓ {len(changed)} job(s) re-queued")


def cancel_pending(api: Printago, args: argparse.Namespace) -> None:
    jobs = [j for j in fetch_jobs(api, WAITING) if not args.label or args.label.lower() in j["label"].lower()]
    if not jobs:
        print("No waiting jobs match." if args.label else "No jobs are waiting.")
        return
    for job in jobs:
        print(f"  {job_label(job)}")
    if confirm(f"Cancel these {len(jobs)} waiting job(s)? Printing jobs are not touched.", args.yes):
        changed = api.patch("/v1/print-jobs/cancel", {"printJobIds": [j["id"] for j in jobs]}) or []
        print(f"✓ {len(changed)} job(s) cancelled")


def resolve_ids(api: Printago, given: list[str], statuses: list[str]) -> list[str]:
    """Expand ID prefixes against the jobs the action could apply to."""
    full = [g for g in given if len(g) == 24]
    prefixes = [g for g in given if len(g) != 24]
    if not prefixes:
        return full
    candidates = [j["id"] for j in fetch_jobs(api, statuses)]
    for prefix in prefixes:
        matches = [c for c in candidates if c.startswith(prefix)]
        if len(matches) != 1:
            sys.exit(f"✗ {prefix!r} matches {len(matches)} jobs; use more of the ID")
        full.append(matches[0])
    return full


# ── Helpers ─────────────────────────────────────────────────────────────────


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    return input(f"{question} [y/N] ").strip().lower() == "y"


def age(timestamp: str) -> str:
    seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def paint(status: str, width: int) -> str:
    text = f"{status:<{width}}"
    color = COLORS.get(status)
    return f"\033[{color}m{text}\033[0m" if color and sys.stdout.isatty() else text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="See and steer your Printago print queue.")
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="show the active queue")
    listing.add_argument("--status", help="comma-separated statuses, e.g. failed or pending,printing")
    listing.set_defaults(run=list_jobs)

    for name, text in [
        ("pause", "pause printing jobs"),
        ("resume", "resume paused jobs"),
        ("cancel", "cancel jobs (stops the printer if printing)"),
        ("retry", "re-queue failed or cancelled jobs"),
        ("front", "move waiting jobs to the front of the queue"),
    ]:
        command = commands.add_parser(name, help=text)
        command.add_argument("ids", nargs="+", metavar="job-id")
        if name == "cancel":
            command.add_argument("--no-stop", action="store_true", help="leave the printer running")
        command.set_defaults(run=act)

    retry = commands.add_parser("retry-failed", help="re-queue every failed job")
    retry.add_argument("--yes", "-y", action="store_true")
    retry.set_defaults(run=retry_failed)

    cancel = commands.add_parser("cancel-pending", help="cancel waiting jobs, optionally by label")
    cancel.add_argument("--label", help="only jobs whose label contains this text")
    cancel.add_argument("--yes", "-y", action="store_true")
    cancel.set_defaults(run=cancel_pending)

    return parser.parse_args()


if __name__ == "__main__":
    main()
