/**
 * Send a signed test order to the intake server, the way your storefront would.
 *
 *   npm run send-test-order                  # uses the first SKU in your store
 *   npm run send-test-order -- BENCHY-RED 2  # a specific SKU code and quantity
 */
import { createHmac } from 'node:crypto';
import { printagoFromEnv, requireEnv } from './printago.js';

const secret = requireEnv('WEBHOOK_SECRET');
const target = process.env.INTAKE_URL ?? `http://127.0.0.1:${process.env.PORT ?? 3702}`;

let [sku, quantity = '1'] = process.argv.slice(2);
if (!sku) {
  const [first] = await printagoFromEnv().get<{ sku: string }[]>('/v1/skus', { limit: 1, sort: 'createdAt:asc' });
  if (!first) throw new Error('Your store has no SKUs yet: pass a SKU code as the first argument.');
  sku = first.sku;
}

const orderId = `test-${Date.now()}`;
const body = JSON.stringify({
  id: orderId,
  number: orderId.slice(-6),
  customer: { id: 'cust-42', name: 'Ada Lovelace', email: 'ada@example.com' },
  items: [{ sku, quantity: Number(quantity) }],
  total: 24.0,
  currency: 'USD',
  note: 'Sent by send-test-order',
  shipBy: new Date(Date.now() + 3 * 86_400_000).toISOString(),
});
const signature = `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`;

const created = await fetch(`${target}/webhooks/orders`, {
  method: 'POST',
  headers: { 'content-type': 'application/json', 'x-signature': signature },
  body,
});
console.log(`POST /webhooks/orders → ${created.status}`, await created.json());

// Jobs are created a few seconds after the order is sent to print.
await new Promise((resolve) => setTimeout(resolve, 5000));
const progress = await fetch(`${target}/orders/${orderId}`, { headers: { authorization: `Bearer ${secret}` } });
console.log(`GET /orders/${orderId} → ${progress.status}`, await progress.json());
