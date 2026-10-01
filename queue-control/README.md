# Queue control

When a batch goes wrong (the wrong filament loaded, a bad file, a rush order that
has to jump the line) you want to act on twenty jobs at once, often from a terminal
on the farm PC or over SSH. This is the queue from the command line, and the
reference for every queue action the API offers.

```
$ python queue_control.py list
ID                        STATUS                JOB                             PRINTER         AGE
mbu1hbozmg1szea2nxzcbgp2  printing              API example - Plate 1 (2/2)     P1S-03          17m
fckfddxii2kxnklttjxqxppp  pending               Benchy (1/4)                                    4m
l8cmyv33tbgfl6kbwftkci1z  pending               Benchy (2/4)                                    4m

1 printing · 2 pending
```

## Run it

```bash
cp .env.example .env            # add your API key
pip install -r requirements.txt
python queue_control.py list
```

| Command | |
|---|---|
| `list [--status failed]` | The active queue in queue order, or jobs in the given statuses |
| `pause <id>...` | Pause printing jobs |
| `resume <id>...` | Resume them |
| `cancel <id>... [--no-stop]` | Cancel jobs. A printing job's printer is stopped too, unless `--no-stop`. |
| `retry <id>...` | Re-queue failed or cancelled jobs |
| `front <id>...` | Move waiting jobs to the front of the queue |
| `retry-failed [--yes]` | Re-queue every failed job in the queue |
| `cancel-pending [--label text] [--yes]` | Cancel waiting jobs whose label contains `text`. Printing jobs are not touched. |

Like git, any unique start of a job ID works: `python queue_control.py cancel mbu1`.

**API key permissions:** `queue.view`, `queue.manage`, `printer.view`, and
`queue.override` for `front`.

## How it works

Reading: `GET /v1/print-jobs` with filters in the query string.

```http
GET /v1/print-jobs?status.in=pending,assigned,printing&hidden=false&sort=priority:asc,queueOrder:asc
```

`field=value` matches exactly, `field.in=a,b` matches any of a list, and
`sort=priority:asc,queueOrder:asc` is the order the Printago queue shows. Add
`hidden=false` to leave out jobs that were cleared from the queue.

Acting: one `PATCH` per action, each taking `{"printJobIds": [...]}`.

| Action | Endpoint |
|---|---|
| Pause | `PATCH /v1/print-jobs/pause` |
| Resume | `PATCH /v1/print-jobs/resume` |
| Cancel | `PATCH /v1/print-jobs/cancel` (plus `"sendStop": false` to leave the printer running) |
| Retry | `PATCH /v1/print-jobs/retry` |
| Move to front | `PATCH /v1/print-jobs/move-to-queue-front` |

Each answers with the IDs it changed. A job in the wrong state for the action (say,
retrying a job that is still waiting) is left out, and the tool tells you which. For
`front`, the answer lists every job whose position shifted, and leaves out a job that
was already at the front.

Copies of the same plate share a `groupId` and a `queueOrder`, so moving one copy to
the front moves its whole group.

## Notes

- A paused print shows on the printer (its stat goes to `PAUSE`); the job itself
  keeps the status `printing`.
- Cancelling a printing job stops the printer mid-print. That is usually what you
  want, but `--no-stop` is there for jobs you have already stopped at the printer.
