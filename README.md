# gumroad-autopost

Drops a book PDF into Google Drive → it becomes a live Gumroad product the next morning.
Runs entirely in GitHub Actions. No PC, no desktop app, no manual step per book.

**One book per day, oldest first.** Drop five PDFs, they go live over five days.
Drop nothing, nothing happens (no errors, no empty runs).

---

## How it works

```
Google Drive folder          GitHub Actions (daily cron)        Gumroad
  ligaya-bamboo-poles.pdf  →  match slug in books.json       →  products create
                              download the PDF                  products publish
                              record it in state/published.json
```

Matching is by **filename**: a PDF named `ligaya-bamboo-poles.pdf` is matched to the
book whose `slug` is `ligaya-bamboo-poles` in `books.json`. Nothing is ever published
twice, because every success is written into `state/published.json` and committed back.

---

## Setup

### 1. Gumroad token (required)

Gumroad → **Settings → Advanced → Applications** → create an application → generate an
access token. It needs product-edit permission.

Then in this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `GUMROAD_ACCESS_TOKEN` | the token you just generated |

### 2. Google Drive access (pick ONE)

**Option A — service account (recommended, fully hands-off)**

1. Go to <https://console.cloud.google.com/> → create a project (any name).
2. **APIs & Services → Library** → search "Google Drive API" → **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Name it
   anything, skip the optional role steps, click Done.
4. Click the new service account → **Keys → Add key → Create new key → JSON**. A file
   downloads.
5. Open that JSON file, copy **all** of it.
6. In this repo: **Settings → Secrets → Actions → New repository secret**

   | Name | Value |
   |---|---|
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | paste the entire JSON file contents |

7. In the same JSON, find `"client_email"` — it looks like
   `something@your-project.iam.gserviceaccount.com`. In Google Drive, **share your
   books folder with that email address** (Viewer is enough).
8. Open that Drive folder and copy the folder ID from the URL:
   `drive.google.com/drive/folders/`**`THIS_PART`**

   Then in this repo: **Settings → Secrets and variables → Actions → Variables tab →
   New repository variable**

   | Name | Value |
   |---|---|
   | `GDRIVE_FOLDER_ID` | the folder ID |

Done. From now on you only ever drop PDFs into that Drive folder.

**Option B — share links (no setup, good for a first test)**

Skip the service account. Instead, set a PDF to "Anyone with the link can view" in Drive,
then add a `"link"` field to that book in `books.json`:

```json
{
  "slug": "ligaya-bamboo-poles",
  "name": "...",
  "price": "5.99",
  "link": "https://drive.google.com/file/d/FILE_ID_HERE/view"
}
```

Downside: you have to paste a link per book, and the file is publicly downloadable while
it's shared. Fine for testing, not for the long run.

### 3. Name your PDFs to match

Valid filenames are the `slug` values in `books.json` plus `.pdf`. For example:

```
theo-bike-yet.pdf
ligaya-bamboo-poles.pdf
mei-painted-blessing.pdf
sora-paper-crane.pdf
```

A PDF whose name doesn't match any slug is simply ignored — nothing breaks.

---

## Test it before trusting it

**Actions** tab → *Publish next book to Gumroad* → **Run workflow**:

1. First run with **dry run = true**. This downloads the PDF and prints the exact
   Gumroad command it *would* run, without creating anything. Confirms Drive access works.
2. Then run with dry run off, optionally typing one `slug` to force a specific book.
3. Check the log. If it published, you'll see the product ID and URL at the end.

---

## Prices

Every book is set to **$5.99** (storybooks) or **$4.99** (coloring books) in `books.json`.
Review these before the first real run — edit the `price` field and commit.

Before settling on a price it's worth running `gumroad products comps` locally to see
what comparable printables sell for.

---

## Changing the schedule

In `.github/workflows/publish.yml`:

```yaml
- cron: "13 1 * * *"   # 01:13 UTC = 09:13 Singapore/Manila
```

Cron is always UTC. For a different local time, subtract 8 hours.
To pause the automation entirely: **Actions** tab → select the workflow → **Disable**.

---

## Known unknown

Gumroad's *public* REST API does not support creating products (it returns 404 —
creation is dashboard-only). The **CLI** does have a `products create` command, which is
what this automation calls. What hasn't been verified is whether that CLI command works
with only an API token in a headless CI environment, or whether it needs a
browser-authenticated session.

**The dry run and first real run will tell you.** If `products create` rejects the token,
the fallback is a [self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners)
on your own PC — the workflow stays exactly the same, it just executes on your machine
whenever it's switched on, still with no input from you.

---

## Files

| Path | What it is |
|---|---|
| `books.json` | Every book's title, price, description, tags. Edit freely. |
| `scripts/publish.py` | Finds the next book, downloads it, creates + publishes it. |
| `.github/workflows/publish.yml` | The daily cron and the manual "Run workflow" button. |
| `state/published.json` | Written by the job. The record of what's already live. Don't edit by hand. |

**Keep this repository private.** The workflow itself is harmless, but the token secret
and your publishing pipeline shouldn't be public.
