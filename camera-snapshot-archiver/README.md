# Camera snapshot archiver

When a print fails overnight, the question is when and how: first layer, a layer
shift at 60%, spaghetti at the top? Printago keeps each printer's latest camera
still. This keeps all of them, filed one folder per print, so you can scroll back
through a failure or turn a print into a timelapse.

```
snapshots/
  P1S-03/
    Desk Organizer - Plate 1/
      2026-09-30T20-18-32-846Z.jpg
      2026-09-30T20-19-02-857Z.jpg
      ...
  X1C-01/
    ...
```

While a printer prints, Fuse uploads a snapshot about every 30 seconds.

## Run it

```bash
cp .env.example .env     # add your API key and store ID
npm install
npm start
```

| Setting | |
|---|---|
| `OUTPUT_DIR` | Where to save. Default `./snapshots`. |
| `ONLY_WHILE_PRINTING` | `true` (default) keeps snapshots taken while printing. `false` also keeps idle ones, about one every 10 minutes per printer. |

Snapshots come from printers connected through Fuse that have a camera, with
snapshots turned on in Fuse.

**API key permissions:** `printer.view`, `printer.stats`, `printer.camera`.

Make a timelapse from a print's folder with ffmpeg:

```bash
ffmpeg -framerate 12 -pattern_type glob -i 'snapshots/P1S-03/Desk Organizer - Plate 1/*.jpg' \
  -c:v libx264 -pix_fmt yuv420p desk-organizer.mp4
```

## How it works

Two facts keep this cheap on your 600-requests-an-hour limit:

1. **MQTT says when there is a new image.** Each printer's stat carries
   `data.camera.lastSnapshotAt`. When it changes, a new snapshot has been uploaded.
   No polling.
2. **The latest snapshot always lives at the same place.**
   `GET /v1/printers/{id}/snapshot` returns a signed URL, valid for 60 minutes, to the
   printer's latest snapshot. Fetching that same URL again returns the newer image,
   so the app signs once per printer per hour and reuses the URL until shortly
   before `expiresAt`.

Five printers printing around the clock cost about five API requests an hour.

A `404` from the signed URL means no snapshot has been uploaded yet: a new printer,
or a model without a camera. The app skips it.

## Make it yours

- **Keep only failures.** Subscribe to `stores/{storeId}/entities/print_jobs` as
  well, and delete a print's folder when its job completes.
- **Attach the last frame to an alert.** Post the newest image in a print's folder
  with the failure message.
- **Look for failures.** Send each frame to a vision model or your own detector.
