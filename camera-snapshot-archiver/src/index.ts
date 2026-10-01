/**
 * Camera snapshot archiver.
 *
 * Saves every camera snapshot Printago receives from your printers, grouped
 * into one folder per print, so you can review a failure frame by frame or
 * stitch a timelapse with ffmpeg.
 *
 * How it stays cheap on your rate limit:
 *  - MQTT tells us when a new snapshot exists (`data.camera.lastSnapshotAt`
 *    changes), so we never poll.
 *  - A printer's latest snapshot always lives at the same storage path, so the
 *    signed URL from GET /v1/printers/{id}/snapshot keeps working for new
 *    images until it expires. We sign once per printer per hour, not once per
 *    image.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { printagoFromEnv, requireEnv } from './printago.js';
import { connectRealtime, parseMessage, topics, type PrinterStat } from './realtime.js';

const storeId = requireEnv('PRINTAGO_STORE_ID');
const outputDir = process.env.OUTPUT_DIR ?? './snapshots';
const onlyWhilePrinting = (process.env.ONLY_WHILE_PRINTING ?? 'true') !== 'false';
const PRINTING_STATES = new Set(['PREPARE', 'RUNNING', 'PAUSE']);

const api = printagoFromEnv();

// ── Printer names for folder names. ────────────────────────────────────────
const printerNames = new Map<string, string>();
async function loadPrinterNames() {
  const printers = await api.get<{ id: string; name: string }[]>('/v1/printers', { limit: 1000 });
  for (const p of printers) printerNames.set(p.id, p.name);
}
await loadPrinterNames();

// ── One signed URL per printer, reused until shortly before it expires. ────
const signedUrls = new Map<string, { url: string; expiresAt: number }>();
async function snapshotUrl(printerId: string): Promise<string> {
  const cached = signedUrls.get(printerId);
  if (cached && cached.expiresAt - Date.now() > 5 * 60_000) return cached.url;
  const fresh = await api.get<{ url: string; expiresAt: number }>(`/v1/printers/${printerId}/snapshot`);
  signedUrls.set(printerId, fresh);
  return fresh.url;
}

// ── Watch for new snapshots. ───────────────────────────────────────────────
const lastSeen = new Map<string, number>();

const mqtt = connectRealtime({
  apiKey: requireEnv('PRINTAGO_API_KEY'),
  storeId,
  url: process.env.PRINTAGO_MQTT_URL || undefined,
});
mqtt.on('connect', () => {
  mqtt.subscribe(topics(storeId).allPrinterStats);
  console.log(`Saving snapshots to ${outputDir}${onlyWhilePrinting ? ' (only while printing)' : ''}`);
});

mqtt.on('message', (topic, payload) => {
  const stat = parseMessage<PrinterStat>(payload);
  if (!stat) return;
  const printerId = topic.split('/').pop()!;
  const takenAt = stat.data.camera?.lastSnapshotAt;
  if (!takenAt) return; // No camera, or snapshots are turned off in Fuse.

  const previous = lastSeen.get(printerId);
  lastSeen.set(printerId, takenAt);
  // The first message is the retained one from before we started: we only
  // want snapshots taken from now on.
  if (previous === undefined || takenAt <= previous) return;
  if (onlyWhilePrinting && !PRINTING_STATES.has((stat.state ?? '').toUpperCase())) return;

  void save(printerId, stat, takenAt);
});

async function save(printerId: string, stat: PrinterStat, takenAt: number) {
  try {
    if (!printerNames.has(printerId)) await loadPrinterNames();
    const printer = safeName(printerNames.get(printerId) ?? printerId);
    const job = safeName(stat.data.print?.taskName || stat.data.print?.printJobId || 'not-printing');

    const url = await snapshotUrl(printerId);
    let response = await fetch(url, { cache: 'no-store' });
    if (response.status === 400 || response.status === 403) {
      // The signed URL expired early (clock skew): sign again once.
      signedUrls.delete(printerId);
      response = await fetch(await snapshotUrl(printerId), { cache: 'no-store' });
    }
    if (response.status === 404) return; // No snapshot uploaded yet.
    if (!response.ok) throw new Error(`storage answered ${response.status}`);

    const folder = join(outputDir, printer, job);
    await mkdir(folder, { recursive: true });
    const file = join(folder, `${new Date(takenAt).toISOString().replace(/[:.]/g, '-')}.jpg`);
    await writeFile(file, Buffer.from(await response.arrayBuffer()));
    const progress = stat.data.print?.percent;
    console.log(`${printer}: saved ${file}${progress != null ? ` (${progress}%)` : ''}`);
  } catch (error) {
    console.error(`Could not save snapshot for ${printerId}: ${(error as Error).message}`);
  }
}

function safeName(name: string): string {
  return name.replace(/[^\w.\- ]+/g, '_').trim().slice(0, 80) || 'unnamed';
}
