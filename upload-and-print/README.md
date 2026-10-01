# Upload and print

Getting a file from your CAD tool, a customer upload or a generator script onto a
printer usually means opening the app, uploading, waiting and clicking Print. This
does it in one command, and shows the full flow any integration follows to print a
new file.

```
$ python upload_and_print.py dogbone.gcode.3mf --quantity 2 --label "API example"

[1] Uploading dogbone.gcode.3mf (660 KB)
[2] Creating the part
   part e7tpj60c4nlbyhq6iraotve0
[3] Waiting for Printago to process the file
   ✓ dogbone is ready to print
[4] Queueing the print
   2 print jobs will be created
   ✓ queued (part build en5v8ye2ysf9m2wn4035ld9s)
[5] Following the jobs (Ctrl+C to stop watching)
   16:18:27  API example - Plate 1 #1  →  printing on P1S-03
   16:24:08  API example - Plate 1 #1  →  completed on P1S-03
```

## Run it

```bash
cp .env.example .env            # add your API key
pip install -r requirements.txt
python upload_and_print.py benchy.stl --quantity 3
```

| Option | |
|---|---|
| `--quantity 3` | Copies to print. For a 3MF, copies of every plate. |
| `--plates 1:2,3:1` | 3MF only: two of plate 1 and one of plate 3. |
| `--label "Order 1042"` | How the jobs are named in the queue. |
| `--tags "PLA\|0.4mm"` | Only printers with all of these tags take the jobs. |
| `--front` | Put the jobs at the front of the queue. |
| `--part-id <id>` | Print a part already in Printago instead of uploading. |
| `--type cadquery` | Needed for Python models (`cadquery` or `build123d`). |
| `--no-follow` | Exit once queued. |

Supported files: `.stl`, `.3mf`, `.gcode.3mf`, `.step`/`.stp`, `.scad`, `.gcode`,
and `.py` with `--type`.

**API key permissions:** `part.create`, `part.view`, `build.create`, `queue.view`,
`printer.view`.

## How it works

1. **`POST /v1/storage/signed-upload-urls`** with the file name returns an
   `uploadUrl` (valid for 5 minutes), the HTTP `method` to use, and a `path` that
   starts with `uploads:`.
2. **Upload the bytes** to `uploadUrl` with that method. This request goes straight
   to storage and must not carry your Printago `Authorization` header.
3. **`POST /v1/parts?async=true`** with the `path` in `fileUris` creates the part.
   Without `async=true` the call waits up to about 20 seconds for processing.
4. **`GET /v1/parts/{id}`** until `processingStatus` is `null` (ready) or `"failed"`.
   Printago reads the file's metadata and filament channels, renders thumbnails,
   splits 3MF plates and estimates print time. A part that is still processing
   cannot be printed.
5. **`POST /v2/builds/preview`**, then **`POST /v2/builds`**, with the same body. The
   preview is a dry run that says exactly which jobs will be created.
6. **`GET /v1/print-jobs?partBuildId=...`** every 20 seconds until every job has
   finished. The jobs appear a few seconds after the build is created.

Polling every 20 seconds keeps this well inside the 600-requests-an-hour limit. For
something that runs all day, listen on MQTT instead: subscribe to
`stores/{storeId}/entities/print_jobs` and Printago pushes every job change, with a
`changes` map of exactly which fields moved.

## Make it yours

- **Watch a folder.** Run the upload for every file that lands in a directory: a
  drop folder for a design team, or the output of a generator script.
- **Reuse parts.** Uploading the same file again makes a new part each time. Look it
  up first with `GET /v1/parts?name=...` and print it with `--part-id`.
- **Pick colors.** Add `"materials": {"0": [{"variantId": "..."}]}` to the build
  entry to force filament channel 0 to a specific material. See
  [`POST /v2/builds`](https://developers.printago.io).
