# Etsy

Turn Etsy on and every book the publisher lists on Gumroad is also created on
Etsy as a **digital download**: the PDF and cover images uploaded, the
description converted to plain text, tags trimmed to Etsy's limits. It is left
as a **draft** for you to look at unless you set `publish: true`.

> **Not tested against Etsy itself.** I couldn't reach Etsy's developer docs
> from the build environment. The code follows the v3 API as I know it and is
> tested against a mock of it. Run it once with `dry_run`, then once as a draft,
> before trusting it.

## One-time setup

1. **Get an API key.** On Etsy (2-step verification on) go to
   <https://www.etsy.com/developers/your-apps> and create an app. Choose the
   option for managing **your own shop**. New keys wait for Etsy's approval, and
   until then calls return 403. Declare the scopes `listings_r`, `listings_w`
   and `shops_r` up front; Etsy won't let you ask for more later.
2. **Add a redirect URI** to the app: `http://localhost:3003/callback`.
3. **Find your shop id** (a number). Your shop's page URL doesn't show it; the
   API does: `GET https://api.etsy.com/v3/application/shops?shop_name=YourShop`
   with the header `x-api-key: <keystring>`.
4. **Pick a category.** `taxonomy_id` in `config.yml` is required. Look up the
   number with `GET /v3/application/seller-taxonomy/nodes` and choose the one
   for printable or digital kids' activity pages.
5. **Sign in once:**
   ```
   ETSY_API_KEY=<keystring> python scripts/etsy_auth.py
   ```
   Open the link, click Allow, paste back the address you land on. It prints a
   refresh token.
6. **Add GitHub secrets** (Settings → Secrets and variables → Actions):
   `ETSY_API_KEY`, `ETSY_SHOP_ID`, `ETSY_REFRESH_TOKEN`, and `ETSY_SHARED_SECRET`
   if Etsy shows one next to your key.
7. In `config.yml` set `platforms.etsy.enabled: true` and `taxonomy_id`.

## Things Etsy requires of you

- The shop must be open, with payment and billing set up. Etsy charges a small
  fee per listing.
- Etsy's rules on handmade, designed and AI-made goods apply, and so does their
  creativity standard. `who_made` / `when_made` in `config.yml` are your
  declaration to Etsy; the defaults are `i_did` and `made_to_order`. Set them to
  what is true. `when_made` may need a specific year range in some shops.
- A digital file must be 20 MB or less (the run stops with a clear message).
- A first activation may be held for Etsy's review.

## How the sign-in stays alive

Access tokens last an hour; the refresh token lasts 90 days. Each run trades the
refresh token for a new access token, and Etsy may return a new refresh token.
With `DATABASE_URL` set (it already is for the panel) that new token is saved in
the `autopost_credentials` table and used next time, falling back to the
`ETSY_REFRESH_TOKEN` secret. Without a database, if a run says Etsy refused the
token, run `scripts/etsy_auth.py` again and update the secret. If nothing
publishes for 90 days, the token expires and you do the same.

## Rules it follows

- Re-runs update the listing found by title instead of creating a second one.
- It never sends a price on an update (Etsy keeps price in the listing's
  inventory). Change prices on Etsy.
- Files and images are uploaded only if the listing has none yet.

## Etsy MCP

Etsy publishes a documentation-only MCP server for its API, which helps when
writing code against it. It can't see or edit your shop. Community MCP servers
can, but they would hold your Etsy credentials; this project doesn't use them.
