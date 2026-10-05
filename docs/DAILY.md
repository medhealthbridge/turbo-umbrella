# Daily Gumroad report

Every morning at **08:00 Manila time** Vercel Cron calls `/api/cron/daily`. The
job:

1. reads your products and recent sales from the Gumroad API,
2. works out sales and revenue for the last 24 hours and 7 days, and your best
   and worst product,
3. writes **drafts** (caption, hashtags, image idea) for Pinterest, Facebook and
   TikTok about the top product, plus one new template idea,
4. saves it all to the `autopost_daily_reports` table in Neon.

Read it at **`/admin`** (same password as the control panel). Nothing is posted
anywhere — the drafts are for you to review and copy.

## Setting it up

Set these in Vercel → Settings → Environment Variables, then redeploy:

| Variable | Needed | What it is |
|---|---|---|
| `GUMROAD_ACCESS_TOKEN` | yes | Gumroad → Settings → Advanced → Applications → generate access token |
| `CRON_SECRET` | yes | 32+ random characters (`openssl rand -hex 32`). Vercel sends it on every cron call. |
| `DATABASE_URL` | yes | Your Neon connection string |
| `PANEL_PASSWORD` | yes | Password for `/admin` |
| `ANTHROPIC_API_KEY` | no | Claude writes the drafts. Without it you get plain template drafts. |
| `ANTHROPIC_MODEL` | no | Default `claude-opus-5-5` |
| `REPORT_TZ` | no | Default `Asia/Manila` |

Crons only run on the **production** deployment, so merge to `main` first.

## The schedule

`vercel.json` has `"schedule": "0 0 * * *"`. Vercel cron is UTC, and Manila is
UTC+8 all year (no daylight saving), so 00:00 UTC is 08:00 Manila. Vercel may
start the run a little after the minute.

## Testing it by hand

The endpoint needs the secret in a header:

```
curl -i https://YOUR-SITE.vercel.app/api/cron/daily \
  -H "Authorization: Bearer $CRON_SECRET"
```

You get back JSON with `"ok": true` and the whole report; then open
`/admin`. Running it again the same day replaces that day's report. Without the
header it answers `401`; with `CRON_SECRET` unset it answers `503` and does
nothing. You can also press **Run** next to the cron in Vercel → Settings →
Cron Jobs, or use `vercel crons run /api/cron/daily`.

## What the Gumroad API can and can't do

The job uses only `GET /products` and `GET /sales`. It never writes to Gumroad.

I couldn't open Gumroad's API docs from the build environment, and the sources
I could see disagree about whether the API can **create** a product. So this
job doesn't try. **Manual step for a new template:** make it in Canva, export
the PDF, then either add the product in Gumroad's dashboard, or drop the PDF in
your Drive folder and let the existing publisher list it.

The field names the report reads (`price` in cents, `created_at`, `refunded`,
`product_id`, `next_page_key`) come from Gumroad's published API, but the tests
use a mock, not the live service. If the first real run errors, the response
body is in the message — send it over.

## Privacy

Reports hold totals, product names and drafts only. Buyer emails and individual
sales are not stored.
