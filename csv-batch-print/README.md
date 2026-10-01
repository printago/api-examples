# CSV batch print

Production plans, restock lists and wholesale orders tend to live in a spreadsheet or
come out of an ERP as a CSV. Keying fifty lines into the queue by hand takes an
afternoon, and one wrong SKU code means a bin of the wrong part. This queues the
whole file at once, and refuses to queue any of it until every row checks out.

```
$ python csv_batch_print.py sample-batch.csv
Read 4 row(s) from sample-batch.csv

This will queue:

  Jobs  From              Label
     5  part              Restock bin 7  (5 plates)
     1  part              Order 1042
     4  BENCHY-STD        Benchy
     2  CUBE-20           Cube

  12 job(s) in total

Queue these prints? [y/N] y

✓ Queued 12 job(s) (2 SKU build(s), 4 part build(s)).
```

## Run it

```bash
cp .env.example .env            # add your API key
pip install -r requirements.txt
python csv_batch_print.py sample-batch.csv          # edit it to use your SKUs and parts first
python csv_batch_print.py plan.csv --yes            # no prompt, for scheduled runs
```

## The CSV

```csv
sku,part,quantity,label,options,tags
BENCHY,,4,,Color=Red,
,Cable Clip,20,Restock,,PETG
,p8f2k6m1q9w3e7r5t0y4u2i6,1,Order 1042,,
```

| Column | |
|---|---|
| `sku` | A SKU code, exactly as in Printago. |
| `part` | A part name or ID, for rows that are not a SKU. |
| `quantity` | Copies, or SKU units for a SKU row. Default 1. |
| `label` | Part rows: how the jobs are named in the queue. |
| `options` | SKU rows: variant choices, `Color=Red; Size=L`. |
| `tags` | Printer tags the jobs need, `PLA\|0.4mm`. |

Before anything is queued, every row is checked: the SKU code exists, the part name
matches exactly one part, the part has finished processing, the quantity is a whole
number. Any problem stops the run and lists every bad row.

**API key permissions:** `sku.view`, `part.view`, `build.create`.

## How it works

1. `GET /v1/skus` and `GET /v1/parts`, paged, to resolve every row locally. One pass,
   however long the file.
2. `POST /v2/builds/preview` with the whole batch: a dry run that returns every job
   it would create, with plate names.
3. `POST /v2/builds` with the same body. One request queues the lot.

**Why check SKU codes yourself?** `POST /v2/builds` skips a SKU whose code it does
not recognize rather than rejecting the request. In a batch of fifty, a typo would
quietly print forty-nine. Variant codes such as `BENCHY-RED` are resolved for
orders, not builds, so name variants in the `options` column.
