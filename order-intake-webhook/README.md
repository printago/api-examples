# Order intake webhook

Printago connects to Shopify, Etsy, eBay, TikTok Shop and WooCommerce on its own.
Orders from anywhere else (your own storefront, a wholesale portal, an ERP, a quote
form) get typed in by hand, and every hand-typed order is a chance to print the wrong
thing. This is a small server that takes those orders as a webhook, creates them in
Printago with the customer and line items, and sends them to print.

## Run it

```bash
cp .env.example .env     # add your API key and a webhook secret
npm install
npm start                # listens on http://127.0.0.1:3702

# in another terminal: send a signed test order for the first SKU in your store
npm run send-test-order
npm run send-test-order -- BENCHY-RED 2     # or a specific SKU code and quantity
```

Orders need a commercial Printago account.

**API key permissions:** `order.view`, `order.create`, `order.print`, `queue.view`
(and `sku.view` for `send-test-order`).

## The webhook

```http
POST /webhooks/orders
x-signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with WEBHOOK_SECRET>

{
  "id": "sq-ord-88214",
  "number": "1042",
  "customer": { "id": "cust-42", "name": "Jordan Lee", "email": "jordan@example.com" },
  "items": [{ "sku": "BENCHY-RED", "quantity": 2 }],
  "total": 34.5,
  "currency": "USD",
  "note": "Gift wrap, please",
  "shipBy": "2026-10-05"
}
```

| Answer | Meaning |
|---|---|
| `201 { orderId, printing, unmatchedSkus, printErrors }` | Created, and sent to print if `AUTO_PRINT=true` |
| `200 { orderId, duplicate: true }` | Already received this order `id`. Nothing was created. |
| `401` | Signature did not match |
| `422` | Printago rejected the order. Retrying will not help. |
| `502` | Printago was unreachable. Retry later. |

Your platform's payload will look different. Change `toPrintagoOrder()` in
`src/server.ts` to map it; that is the only function that knows the incoming shape.

To show customers how far along their order is:

```http
GET /orders/sq-ord-88214
Authorization: Bearer <WEBHOOK_SECRET>

{ "orderNumber": "1042", "orderStatus": "open",
  "jobs": { "total": 2, "completed": 1, "printing": 1, "preparing": 0, "waiting": 0, "failed": 0, "cancelled": 0 } }
```

## How it works

1. **Verify the signature** with a constant-time compare, before parsing anything.
2. **Look for the order first**: `GET /v1/orders?externalId=<your order id>`.
   Webhooks are delivered at least once, and this makes a redelivery harmless.
3. **`POST /v1/orders`** with the order, the customer and the items. Each item's
   `sku` is matched against your Printago SKU codes, including variant suffixes:
   `BENCHY-RED` finds SKU `BENCHY` with Color set to Red, using the SKU suffixes on
   your variant values. An item whose code matches nothing is saved anyway, with no
   SKU, so you can map it in Printago later. It comes back in `unmatchedSkus`.
4. **`POST /v1/orders/print`** with the order ID and the matched SKU IDs. Creating an
   order queues nothing on its own; this call creates the print jobs, and every job
   carries the order's ID.
5. **Progress** comes from `GET /v1/print-jobs?orderId=...`. Jobs appear a few
   seconds after step 4.

## Before you put it on the internet

- Run it behind HTTPS (a reverse proxy such as Caddy or nginx, or your platform's
  tunnel), with `HOST=0.0.0.0`.
- Two copies of the same webhook arriving at the same instant can both pass the
  duplicate check. If your platform retries aggressively, put a lock or a unique
  constraint on the incoming order ID in front of `receiveOrder()`.
- Set `AUTO_PRINT=false` to have orders land in Printago for review instead of going
  straight to the queue.
