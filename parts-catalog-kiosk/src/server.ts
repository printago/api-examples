/**
 * Parts catalog kiosk.
 *
 * A browsable, searchable catalog of your Printago parts with a Print button.
 * Put it on a tablet in a makerspace, a classroom or the shop floor, and
 * people can queue prints without a Printago login.
 *
 * The server holds the API key; the browser only talks to this server:
 *   GET  /api/catalog               ready-to-print parts with thumbnails
 *   POST /api/print                 queue copies of a part (POST /v2/builds)
 *   GET  /api/builds/:id            where those copies are in the queue
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { timingSafeEqual } from 'node:crypto';
import { printagoFromEnv, PrintagoError } from './printago.js';

const port = Number(process.env.PORT ?? 3701);
const host = process.env.HOST ?? '127.0.0.1';
const catalogTag = process.env.CATALOG_TAG || null;
const kioskPin = process.env.KIOSK_PIN || null;
const maxQuantity = Number(process.env.MAX_QUANTITY ?? 10);

const api = printagoFromEnv();

interface Part {
  id: string;
  name: string;
  type: string;
  description: string;
  thumbnailUri: string | null;
  folderId?: string | null;
  userTags?: string[];
  processingStatus?: 'processing' | 'failed' | null;
}

interface CatalogItem {
  id: string;
  name: string;
  type: string;
  description: string;
  folder: string | null;
  thumbnail: string | null;
}

// ── Catalog: every part, a page at a time, cached for five minutes. ────────
let catalog: { items: CatalogItem[]; folders: string[]; loadedAt: number } | null = null;

async function loadCatalog() {
  if (catalog && Date.now() - catalog.loadedAt < 5 * 60_000) return catalog;

  const parts: Part[] = [];
  for (let offset = 0; ; offset += 500) {
    const page = await api.get<{ data: Part[]; meta: { hasMore: boolean } }>('/v1/parts', {
      sort: 'name:asc',
      limit: 500,
      offset,
      meta: true,
    });
    parts.push(...page.data);
    if (!page.meta.hasMore) break;
  }

  const folders = await api.get<{ id: string; name: string }[]>('/v1/folders/parts');
  const folderNames = new Map(folders.map((f) => [f.id, f.name]));

  // Only parts that finished processing can be printed.
  const ready = parts.filter(
    (p) => p.processingStatus == null && (!catalogTag || p.userTags?.includes(catalogTag)),
  );
  const thumbnails = await thumbnailUrls(ready.map((p) => p.thumbnailUri));

  const items = ready.map((p, i) => ({
    id: p.id,
    name: p.name,
    type: p.type,
    description: p.description,
    folder: (p.folderId && folderNames.get(p.folderId)) || null,
    thumbnail: thumbnails[i],
  }));
  const usedFolders = [...new Set(items.map((i) => i.folder).filter((f): f is string => !!f))].sort();
  catalog = { items, folders: usedFolders, loadedAt: Date.now() };
  return catalog;
}

/**
 * `thumbnailUri` is usually a storage path that must be signed before a browser
 * can load it (POST /v1/storage/signed-urls, up to 100 per call). Some parts
 * already carry a full URL, and a part whose thumbnail is still being made has
 * a placeholder that is not a path at all.
 */
async function thumbnailUrls(uris: (string | null)[]): Promise<(string | null)[]> {
  const result = uris.map((uri) => (uri && /^https?:\/\//.test(uri) ? uri : null));
  const toSign = uris.flatMap((uri, i) => (uri && !result[i] && uri.includes('/') ? [{ uri, i }] : []));
  for (let start = 0; start < toSign.length; start += 100) {
    const batch = toSign.slice(start, start + 100);
    const { signedUrls } = await api.post<{ signedUrls: string[] }>('/v1/storage/signed-urls', {
      paths: batch.map((b) => b.uri),
      duration: 3600, // seconds; the default is five minutes
    });
    batch.forEach((b, n) => (result[b.i] = signedUrls[n] ?? null));
  }
  return result;
}

// ── Printing. ──────────────────────────────────────────────────────────────
async function print(body: { partId?: string; quantity?: number; requester?: string }) {
  const { items } = await loadCatalog();
  const part = items.find((p) => p.id === body.partId);
  if (!part) return { status: 404, body: { error: 'That part is not in the catalog.' } };

  const quantity = Math.floor(Number(body.quantity ?? 1));
  if (!(quantity >= 1 && quantity <= maxQuantity)) {
    return { status: 400, body: { error: `Choose between 1 and ${maxQuantity} copies.` } };
  }
  const requester = String(body.requester ?? '').trim().slice(0, 40);

  const { partBuilds } = await api.post<{ partBuilds: { id: string }[] }>('/v2/builds', {
    parts: [
      {
        partId: part.id,
        quantity,
        // The label is what shows in the Printago queue.
        label: requester ? `${part.name} · ${requester}` : part.name,
      },
    ],
  });
  kioskBuilds.add(partBuilds[0].id);
  console.log(`Queued ${quantity} × ${part.name}${requester ? ` for ${requester}` : ''}`);
  return { status: 201, body: { buildId: partBuilds[0].id, quantity } };
}

/** Builds this kiosk created: the only ones it will report on. */
const kioskBuilds = new Set<string>();

/** Summarize the jobs a build created. Cached briefly so many screens polling cost one call. */
const buildCache = new Map<string, { at: number; value: unknown }>();
async function buildStatus(buildId: string) {
  const cached = buildCache.get(buildId);
  if (cached && Date.now() - cached.at < 15_000) return cached.value;

  const jobs = await api.get<{ status: string }[]>('/v1/print-jobs', { partBuildId: buildId, limit: 1000 });
  const counts: Record<string, number> = {};
  for (const job of jobs) counts[job.status] = (counts[job.status] ?? 0) + 1;
  const value = { total: jobs.length, counts };
  buildCache.set(buildId, { at: Date.now(), value });
  return value;
}

// ── HTTP. ──────────────────────────────────────────────────────────────────
const page = new URL('../public/index.html', import.meta.url);

createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://kiosk');
  try {
    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      return res.end(await readFile(page));
    }
    if (req.method === 'GET' && url.pathname === '/api/catalog') {
      const { items, folders } = await loadCatalog();
      return send(res, 200, { items, folders, pinRequired: !!kioskPin, maxQuantity });
    }
    if (req.method === 'POST' && url.pathname === '/api/print') {
      if (kioskPin && !pinMatches(req.headers['x-kiosk-pin'])) return send(res, 401, { error: 'Wrong PIN.' });
      const result = await print(await readJson(req));
      return send(res, result.status, result.body);
    }
    const build = /^\/api\/builds\/([a-z0-9]{24})$/.exec(url.pathname);
    if (req.method === 'GET' && build && kioskBuilds.has(build[1])) {
      return send(res, 200, await buildStatus(build[1]));
    }

    send(res, 404, { error: 'Not found' });
  } catch (error) {
    console.error(error);
    const message = error instanceof PrintagoError && error.status < 500 ? error.message : 'Something went wrong.';
    send(res, 502, { error: message });
  }
}).listen(port, host, () => {
  console.log(`Kiosk running at http://${host}:${port}${catalogTag ? ` (parts tagged "${catalogTag}")` : ''}`);
});

function send(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

async function readJson(req: IncomingMessage): Promise<Record<string, any>> {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 10_000) throw new Error('Request too large');
  }
  try {
    return JSON.parse(raw || '{}');
  } catch {
    return {};
  }
}

function pinMatches(given: string | string[] | undefined): boolean {
  const a = Buffer.from(String(given ?? ''));
  const b = Buffer.from(kioskPin!);
  return a.length === b.length && timingSafeEqual(a, b);
}
