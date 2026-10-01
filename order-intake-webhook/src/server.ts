/**
 * Order intake webhook.
 *
 * Printago has built-in Shopify, Etsy, eBay, TikTok Shop and WooCommerce
 * integrations. For everything else (your own storefront, an ERP, a B2B
 * portal, a form), point that system's "order created" webhook here:
 *
 *   POST /webhooks/orders            create the order in Printago and print it
 *   GET  /orders/{yourOrderId}       how far along printing is
 *
 * Requests are authenticated with an HMAC-SHA256 signature of the raw body
 * (header `x-signature: sha256=<hex>`), the scheme most platforms use.
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { printagoFromEnv, requireEnv, PrintagoError } from './printago.js';

const port = Number(process.env.PORT ?? 3702);
const host = process.env.HOST ?? '127.0.0.1';
const secret = requireEnv('WEBHOOK_SECRET');
const autoPrint = (process.env.AUTO_PRINT ?? 'true') !== 'false';

const api = printagoFromEnv();

/** The order shape this server accepts. Adapt `toPrintagoOrder` to your platform's payload. */
interface IncomingOrder {
  id: string;
  number?: string;
  customer?: { id?: string; name?: string; email?: string };
  items: { sku: string; quantity: number }[];
  total?: number;
  currency?: string;
  note?: string;
  /** ISO date the order must ship by. */
  shipBy?: string;
}

interface PrintagoOrder {
  id: string;
  orderNumber: string;
  status: string;
  externalId: string | null;
}

interface PrintagoOrderItem {
  id: string;
  skuId: string | null;
  externalSku: string | null;
  quantity: number;
}

function toPrintagoOrder(order: IncomingOrder) {
  return {
    order: {
      orderNumber: order.number ?? order.id,
      // externalId is how we find this order again: for retries and status checks.
      externalId: order.id,
      price: order.total,
      currency: order.currency,
      note: order.note,
      deadline: order.shipBy ? new Date(order.shipBy).toISOString() : undefined,
    },
    customer: order.customer?.name || order.customer?.email
      ? { externalId: order.customer.id, name: order.customer.name, email: order.customer.email }
      : undefined,
    // `sku` is matched against your Printago SKU codes, including variant
    // suffixes (BENCHY-RED → SKU BENCHY with Color = Red).
    items: order.items.map((item) => ({ sku: item.sku, quantity: item.quantity })),
  };
}

async function findOrder(externalId: string): Promise<PrintagoOrder | null> {
  const [order] = await api.get<PrintagoOrder[]>('/v1/orders', { externalId, limit: 1 });
  return order ?? null;
}

async function receiveOrder(order: IncomingOrder) {
  if (!order?.id || !Array.isArray(order.items) || order.items.length === 0) {
    return { status: 400, body: { error: 'An order needs an id and at least one item.' } };
  }

  // Webhooks are delivered at least once. If we have already created this
  // order, say so and succeed, so the sender stops retrying.
  const existing = await findOrder(order.id);
  if (existing) return { status: 200, body: { orderId: existing.id, duplicate: true } };

  const created = await api.post<{ order: PrintagoOrder; items: PrintagoOrderItem[] }>(
    '/v1/orders',
    toPrintagoOrder(order),
  );

  // Items whose SKU code matched nothing are still saved (skuId null) so you
  // can map them in Printago later. They cannot print until you do.
  const unmatched = created.items.filter((i) => !i.skuId).map((i) => i.externalSku);
  const skuIds = [...new Set(created.items.map((i) => i.skuId).filter((id): id is string => !!id))];

  let printErrors: string[] = [];
  if (autoPrint && skuIds.length) {
    // Creating an order queues nothing; this call creates the builds and jobs.
    const result = await api.post<{ errors: string[] }>('/v1/orders/print', {
      orderIds: [created.order.id],
      skuIds,
    });
    printErrors = result.errors ?? [];
  }

  console.log(
    `Order ${created.order.orderNumber}: ${created.items.length} item(s)` +
      (autoPrint && skuIds.length ? ', sent to print' : '') +
      (unmatched.length ? `, unmatched SKUs: ${unmatched.join(', ')}` : ''),
  );
  return {
    status: 201,
    body: {
      orderId: created.order.id,
      printing: autoPrint && skuIds.length > 0 && printErrors.length === 0,
      unmatchedSkus: unmatched,
      printErrors,
    },
  };
}

async function orderProgress(externalId: string) {
  const order = await findOrder(externalId);
  if (!order) return { status: 404, body: { error: 'No such order.' } };

  const jobs = await api.get<{ status: string }[]>('/v1/print-jobs', { orderId: order.id, limit: 1000 });
  const count = (...statuses: string[]) => jobs.filter((j) => statuses.includes(j.status)).length;
  return {
    status: 200,
    body: {
      orderNumber: order.orderNumber,
      orderStatus: order.status,
      jobs: {
        total: jobs.length,
        completed: count('completed'),
        printing: count('printing', 'sending-to-printer', 'paused'),
        preparing: count('assigned', 'slicing', 'sliced', 'finalizing', 'waiting-for-approval', 'ready-for-printer'),
        waiting: count('pending'),
        failed: count('failed'),
        cancelled: count('cancelled'),
      },
    },
  };
}

// ── HTTP. ──────────────────────────────────────────────────────────────────
createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://intake');
  try {
    if (req.method === 'POST' && url.pathname === '/webhooks/orders') {
      const raw = await readBody(req);
      if (!signatureMatches(raw, req.headers['x-signature'])) return send(res, 401, { error: 'Bad signature.' });
      let order: IncomingOrder;
      try {
        order = JSON.parse(raw.toString('utf8'));
      } catch {
        return send(res, 400, { error: 'Body is not JSON.' });
      }
      const result = await receiveOrder(order);
      return send(res, result.status, result.body);
    }

    const progress = /^\/orders\/([^/]+)$/.exec(url.pathname);
    if (req.method === 'GET' && progress) {
      // Status checks use the shared secret as a bearer token.
      if (!tokenMatches(req.headers.authorization)) return send(res, 401, { error: 'Unauthorized.' });
      const result = await orderProgress(decodeURIComponent(progress[1]));
      return send(res, result.status, result.body);
    }

    send(res, 404, { error: 'Not found.' });
  } catch (error) {
    console.error(error);
    // A 4xx from Printago means the order itself is wrong; do not ask the sender to retry it.
    if (error instanceof PrintagoError && error.status < 500 && error.status !== 429) {
      return send(res, 422, { error: error.message });
    }
    send(res, 502, { error: 'Printago is unavailable, try again.' });
  }
}).listen(port, host, () => {
  console.log(`Order intake listening on http://${host}:${port}/webhooks/orders (auto print: ${autoPrint})`);
});

function signatureMatches(raw: Buffer, header: string | string[] | undefined): boolean {
  const expected = Buffer.from(`sha256=${createHmac('sha256', secret).update(raw).digest('hex')}`);
  const given = Buffer.from(String(header ?? ''));
  return given.length === expected.length && timingSafeEqual(given, expected);
}

function tokenMatches(header: string | undefined): boolean {
  const expected = Buffer.from(`Bearer ${secret}`);
  const given = Buffer.from(header ?? '');
  return given.length === expected.length && timingSafeEqual(given, expected);
}

async function readBody(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1_000_000) throw new Error('Request too large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function send(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}
