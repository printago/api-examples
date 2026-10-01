/**
 * Live printer dashboard.
 *
 * Holds one MQTT connection to Printago, keeps the latest stats for every
 * printer in memory, and streams them to any number of browsers over
 * Server-Sent Events. The browser never sees your API key.
 *
 *   MQTT printer-stats/# ──► this server ──► SSE /events ──► public/index.html
 */
import { createServer, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { printagoFromEnv, requireEnv } from './printago.js';
import { connectRealtime, parseMessage, topics, type PrinterStat } from './realtime.js';

const storeId = requireEnv('PRINTAGO_STORE_ID');
const port = Number(process.env.PORT ?? 3700);
// Bound to localhost by default: this page shows your whole farm to anyone who can reach it.
const host = process.env.HOST ?? '127.0.0.1';

const api = printagoFromEnv();

interface Printer {
  id: string;
  name: string;
  modelName: string | null;
  enabled: boolean;
}

const printers = new Map<string, Printer>();
const stats = new Map<string, PrinterStat>();
const browsers = new Set<ServerResponse>();

// ── Printer names and models come from REST; they rarely change. ────────────
async function refreshPrinters() {
  try {
    const list = await api.get<Printer[]>('/v1/printers', { sort: 'name:asc', limit: 1000 });
    printers.clear();
    for (const { id, name, modelName, enabled } of list) printers.set(id, { id, name, modelName, enabled });
    broadcast('printers', [...printers.values()]);
  } catch (error) {
    console.error(`Could not load printers: ${(error as Error).message}`);
  }
}

await refreshPrinters();
setInterval(refreshPrinters, 5 * 60_000);

// ── Live stats come from MQTT. ──────────────────────────────────────────────
const mqtt = connectRealtime({
  apiKey: requireEnv('PRINTAGO_API_KEY'),
  storeId,
  url: process.env.PRINTAGO_MQTT_URL || undefined,
});
const t = topics(storeId);

mqtt.on('connect', () => mqtt.subscribe(t.allPrinterStats));

mqtt.on('message', (topic, payload) => {
  const printerId = topic.split('/').pop()!;
  const stat = parseMessage<PrinterStat>(payload);

  // An empty retained message means the printer was removed.
  if (!stat) {
    stats.delete(printerId);
    broadcast('remove', { printerId });
    return;
  }

  stats.set(printerId, stat);
  // A printer we have not seen yet was probably just added.
  if (!printers.has(printerId)) void refreshPrinters();
  broadcast('stat', slim(stat));
});

/** Send the browser only what the page renders. */
function slim(stat: PrinterStat) {
  const { printerId, state, isOnline, receivedAt, expiresAt, data } = stat;
  return {
    printerId,
    state,
    isOnline,
    receivedAt,
    expiresAt,
    temperatures: data.temperatures ?? {},
    print: data.print ?? {},
    errors: data.health?.errors ?? [],
    warnings: data.health?.warnings ?? [],
    filament: data.filament ?? null,
  };
}

// ── HTTP: the page, and the event stream. ───────────────────────────────────
function broadcast(event: string, data: unknown) {
  const frame = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of browsers) res.write(frame);
}

const page = new URL('../public/index.html', import.meta.url);

createServer(async (req, res) => {
  if (req.url === '/events') {
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    // Everything we know so far, then live updates.
    res.write(`event: printers\ndata: ${JSON.stringify([...printers.values()])}\n\n`);
    for (const stat of stats.values()) res.write(`event: stat\ndata: ${JSON.stringify(slim(stat))}\n\n`);
    browsers.add(res);
    const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 25_000);
    req.on('close', () => {
      clearInterval(keepAlive);
      browsers.delete(res);
    });
    return;
  }

  if (req.url === '/' || req.url === '/index.html') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(await readFile(page));
    return;
  }

  res.writeHead(404).end();
}).listen(port, host, () => {
  console.log(`Dashboard running at http://${host}:${port}`);
});
