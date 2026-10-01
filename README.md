# autopost

Drop a PDF in a Google Drive folder. It becomes a live, fully-written product
listing — cover art, marketing copy, tags, receipt, refund policy — plus a
ready-to-post social pack, on the schedule you set.

Runs entirely in GitHub Actions. No PC, no desktop app, no manual step per book.

```
Google Drive                  GitHub Actions (hourly)              Gumroad
  ocean-buddies.pdf   ──►   notice it's new                  ──►   listing, published
                            wait for the posting window            cover + thumbnail
                            read the real page count               tags, summary, receipt
                            write the listing copy
                            render cover / thumbnail / pin   ──►   content/social/
                            record it, rebuild the dashboard       captions + images
```

---

## What changed from v1

The first version created a product with a name, a price and a description,
and nothing else. It stalled in draft, and a re-run would have made a
duplicate. Everything below is new:

| | v1 | now |
|---|---|---|
| **New files** | had to be listed in `books.json` first | detected automatically; `books.json` is optional |
| **Schedule** | a cron line in UTC | `publish_at` + `timezone` in `config.yml`, checked hourly |
| **Cover art** | none — the usual reason a listing won't leave draft | cover, thumbnail, Pinterest pin and social card, rendered from page 1 |
| **Copy** | whatever was typed in `books.json` | full listing: hook, what-you-get, guarantees, how-to-print, SEO title, summary, tags, receipt |
| **Page count** | hard-coded and already wrong | read from the PDF, and stale counts in your own copy are corrected |
| **Re-runs** | would create a duplicate | finds the existing listing by name and repairs it |
| **Other platforms** | — | adapter layer; Payhip / Lemon Squeezy / Etsy wired in |
| **Social** | — | caption + sized image per network, written to the repo |
| **Visibility** | read the logs | a dashboard page, rebuilt every run |
| **Tests** | — | `tests/smoke.py`, run in CI on every push |

---

## Setup

### 1. Gumroad token

Gumroad → **Settings → Advanced → Applications** → create an application →
generate an access token (it needs product-edit permission).

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `GUMROAD_ACCESS_TOKEN` | the token |

### 2. Google Drive access

1. <https://console.cloud.google.com/> → create a project (any name).
2. **APIs & Services → Library** → "Google Drive API" → **Enable**.
3. **Credentials → Create credentials → Service account** → name it, skip the
   optional steps, Done.
4. Click it → **Keys → Add key → Create new key → JSON**. A file downloads.
5. Add the whole file as the secret `GOOGLE_SERVICE_ACCOUNT_JSON` (paste
   everything, including the outer `{ }`).
6. In that JSON find `"client_email"` — something like
   `name@project.iam.gserviceaccount.com`. **Share your Drive books folder
   with that address** (Viewer is enough). This step is the one people miss.
7. The folder is already set in `config.yml` as
   `161VeirIHlTqbRb3__oURDOMVVRv4KD3N`. To watch a different one, change
   `source.folder_id` there.

### 3. Optional — Claude writes the copy

Add `ANTHROPIC_API_KEY` as a secret and the hook, summary, tags and social
captions get a rewrite pass. Without it you still get a complete listing;
the generator is deterministic and needs no key.

### 4. Turn the dashboard on

Pick one host. Either serves the same `docs/` folder, and both refresh on
their own: each run commits the rebuilt dashboard, and that push triggers a
redeploy.

**Vercel** — import the repo. `vercel.json` already tells it there is nothing
to build and to serve `docs/`, and `.vercelignore` keeps the Python out of the
upload so Vercel does not mistake this for a web app and ask for an entrypoint.
Make sure Vercel's production branch is the branch that actually has `docs/`
on it.

If Vercel still tries to build this as a Python app, set **Project → Settings
→ Build and Deployment → Root Directory** to `docs`. With the root set there,
the only thing Vercel can see is a folder of static files — there is no
`requirements.txt` in scope to detect, so the question cannot come up. In that
mode the root `vercel.json` is not read, and `outputDirectory` no longer
applies.

**GitHub Pages** — **Settings → Pages → Source: GitHub Actions**, then run the
**Deploy dashboard** workflow once.

Both are public URLs. The dashboard shows titles, prices and product links —
all of it already public on your storefront — but `status.json` also carries
product ids and any error text. If you would rather it were not world-readable,
Vercel's Deployment Protection (Project → Settings → Deployment Protection)
puts it behind a login.

---

## First run

**Actions → Publish → Run workflow**, with `command: run`, `dry_run: ✓`,
`force: ✓`.

Nothing is sent. Download the **previews** artifact from the run page and
check the cover, the thumbnail and the Pinterest pin. The log shows the exact
listing that would be created.

Then run it again with `dry_run` off. It will find any existing draft of the
same name and repair it rather than creating a second one.

---

## Day to day

You drop PDFs in the Drive folder. That is the whole workflow.

The filename becomes the slug and the title:
`ocean-buddies-coloring.pdf` → `Ocean Buddies Coloring`. Anything with
`draft`, `wip`, `test` or `copy of` in the name is skipped, so
work-in-progress can live in the same folder.

To change anything else, edit **`config.yml`** — the posting time, the days,
how many per run, prices by keyword, the guarantees, the tags, which
platforms and which social networks. It is commented throughout.

### Run it by hand

**Actions → Publish → Run workflow**:

| Command | What it does |
|---|---|
| `run` | the normal cycle (this is what the hourly schedule uses) |
| `scan` | notice new files, publish nothing |
| `plan` | print the schedule, the config and the queue |
| `dashboard` | rebuild the status page from existing state |

`slug` publishes one specific book. `force` ignores the schedule window.
`dry_run` builds everything and sends nothing.

Locally, the same thing:

```bash
pip install -r requirements.txt
python scripts/publish.py plan
python scripts/publish.py --dry-run --force --slug ocean-buddies-coloring
```

---

## Overriding one book

Auto-detection covers everything. When you want to hand-write a particular
listing, add an entry to `books.json` matched by `slug` — every field is
optional, and anything you leave out is generated:

```json
{
  "slug": "ocean-buddies-coloring",
  "name": "Ocean Buddies Coloring Book for Kids Ages 3-5",
  "price": "4.99",
  "description": "Dive into 25 big, bold coloring pages…",
  "tags": ["coloring book", "ocean", "preschool"]
}
```

A `"link"` field still works for a book that is not in the Drive folder.

---

## Social

Every publish writes `content/social/<slug>/`:

```
pinterest.txt   caption + hashtags + the product URL, ready to paste
instagram.txt
facebook.txt
x.txt
pin.jpg         1000×1500, the ratio Pinterest wants
social.jpg      1080×1080
pack.json       the same thing as data
```

These are committed, so they are on your phone in the GitHub app. Posting is
deliberately manual for now: Instagram, TikTok and Pinterest all require a
reviewed app and a business account before an API will accept a post, which
is weeks of setup for something that takes ninety seconds by hand.

When you are ready to automate one, the copy and images are already there —
see `docs/PLATFORMS.md`.

---

## Adding a selling platform

`platforms:` in `config.yml` lists Gumroad (live), plus Payhip, Lemon Squeezy
and Etsy, which are wired in and waiting on an implementation. Turning one on
without one gives a clear message rather than a crash. `docs/PLATFORMS.md`
walks through writing one — it is one class with one method, because the
scheduling, copy, cover art and state tracking are already shared.

---

## When something goes wrong

The dashboard's **Needs attention** section, and the run's summary on the
Actions page, both say what happened. Common ones:

| Symptom | Cause |
|---|---|
| "could not read Drive folder" | the folder is not shared with the service account's `client_email` |
| "only 0.01 MB, looks like a partial upload" | the upload had not finished; it will be picked up next hour |
| "too early / too late" | working as intended — outside the window in `config.yml`. Use `force` to publish now |
| "only Nh since the last publish" | `min_hours_between` is doing its job |
| a product stuck in draft | almost always a missing cover — check the previews artifact from a dry run |

Nothing is ever published twice: every attempt is recorded in
`state/published.json`, and a listing is looked up by name on the platform
before anything is created.

---

## Layout

```
config.yml              everything you would want to change
books.json              optional per-book overrides
scripts/publish.py      entry point
scripts/autopost/
  config.py             config.yml + defaults + env overrides
  scheduler.py          is this the moment to publish?
  sources.py            Drive / local / link
  metadata.py           filename → book, PDF → page count
  marketing.py          the listing copy, SEO and captions
  images.py             cover, thumbnail, pin, social card
  platforms/            gumroad.py + the adapter layer
  social.py             the ready-to-post packs
  dashboard.py          the status page
  state.py              what we have seen and published
  runner.py             one run, start to finish
state/                  committed after every run
docs/index.html         the dashboard
content/social/         the social packs
tests/smoke.py          end-to-end test, no network
```

Still manual on Gumroad, because its CLI has no flag for them: the
"Additional details" rows and the receipt *button* text.
