# Setting up the control panel and the scheduler

You run the shop from one web page: the calendar, every listing, covers,
the schedule, the copy. Everything you set is saved in your **Neon
database**. The code never changes when you use the panel, and you never
have to open it.

```
   you ──► control panel (Vercel) ──► Neon Postgres ◄── publisher (GitHub Actions)
                                          ▲                      ▲
                                          │   every 5 minutes    │
                                   scheduler (Neon Function) ────┘ "something is due"
```

- **The panel** writes your settings, listings, covers and calendar to Neon.
- **The scheduler**, a Neon Function, wakes every five minutes. When a
  calendar post is due, it starts a publish run.
- **The publisher** reads everything from Neon: Drive, the PDF, the cover art,
  Gumroad. It writes the results back to Neon, so the panel shows them.
- **A backstop:** the publisher also runs on the hour. It publishes anything
  due that the scheduler missed, and handles the automatic daily slot.

---

## One-time setup (about fifteen minutes)

Do these once, in order.

### 1. Merge the pull request

Everything below runs from `main`.

### 2. A GitHub token (for starting runs)

<https://github.com/settings/personal-access-tokens/new>, a **fine-grained**
token:

- **Repository access:** only this repository
- **Permissions → Actions:** Read and write

That's all it needs. The panel no longer writes to the repository, so it
does not need Contents access. Copy the token; GitHub shows it only once.

### 3. Secrets in GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `DATABASE_URL` | your Neon connection string (`postgresql://…`) |
| `GUMROAD_ACCESS_TOKEN` | you already have this |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the service-account key, and **share the Drive folder with its `client_email`** |
| `ANTHROPIC_API_KEY` | optional: Claude polishes the copy |

### 4. Environment variables in Vercel

Vercel → project → **Settings → Environment Variables**, for **Production**:

| Name | Value |
|---|---|
| `DATABASE_URL` | the same Neon connection string |
| `PANEL_PASSWORD` | a password you choose; this is how you sign in |
| `GITHUB_TOKEN` | the token from step 2 |

Then **Deployments → … → Redeploy**. Variables only reach the functions on a
fresh deploy.

### 5. Deploy the scheduler to Neon

From a terminal in this repository:

```bash
npm i -g neon@latest
neon login
neon link --project-id round-sea-27549086 --branch production -y
GITHUB_TOKEN=github_pat_... neon deploy
```

`neon deploy` reads `neon.ts`, which declares the `scheduler` function and
its five-minute trigger. The token is read from your shell at deploy time,
so it never ends up in a file. Check it worked:

```bash
neon logs query --source function
```

The scheduler is optional. Without it, calendar posts still go out, but at
the next hourly run rather than within five minutes.

### 6. Rotate the database password

Your connection string was pasted into a chat, so treat it as seen. In the
Neon console, go to **Roles → neondb_owner → Reset password**, then put the
new connection string into `DATABASE_URL` in GitHub (step 3) and Vercel
(step 4), and redeploy Vercel. The scheduler picks up the new credentials on
its own, because Neon injects them.

---

## The first time you open the panel

There's nothing to set up inside it. On first use, the database creates its
own tables and imports what's already in the repository: your 33 listings
from `books.json` and the Mermaid book you've already published. That import
happens once.

---

## What each tab does

**Overview.** What's live, what has its PDF ready, what is still waiting for
a PDF, and what's scheduled. It also shows anything that needs attention and
the last few runs. *Run it now* starts a run by hand. Dry run is ticked by
default, so untick it to publish for real.

**Calendar.** Pick a book, a date and a time, all in your shop's time zone.
The book goes live within five minutes of that time. If its PDF isn't in
Drive yet, it waits for it. After three tries it gives up and tells you why.
Scheduling a book that's already on the calendar moves it rather than adding
a second post.

**Books.** Every book in one list: live, ready, waiting for a PDF, or failed.
Search and filter it. Pick one to:
- edit its title, price, description or tags (clear a field to let the
  generator write it)
- upload your own cover, or remove it and go back to the one rendered from
  page 1
- see the images made for it and download them
- copy its social posts, which already include the live link
- schedule it

**Schedule.** Automatic publishing on or off. When it's on, new PDFs go out
one slot at a time. When it's off, only what's on the calendar goes out. This
tab also has the daily slot, days, books per slot, minimum gap, grace window,
settle delay, default price and the Drive folder.

**Marketing.** Who you're writing for, your shop's promise, reassurance
points, the tags every book gets, the refund period and the receipt email.

---

## Where things live

| What | Where |
|---|---|
| Your settings | `autopost_settings` in Neon, merged over `config.yml` |
| Listings and publish results | `autopost_books` |
| The calendar | `autopost_schedule` |
| Covers you upload and images made for you | `autopost_artwork` |
| Files seen in Drive | `autopost_seen` |
| Every run's log | `autopost_runs` (the last 200) |

`config.yml` stays in the repository as the documented defaults. Anything you
set in the panel overrides it. The schema is in `db/schema.sql`. The
publisher and the panel both apply it on start-up, so a new or empty
database just works.

---

## Security, briefly

- The panel URL is public, but the panel isn't. Every endpoint except sign-in
  needs your session cookie, which is HttpOnly, Secure, SameSite=Strict and
  signed with your password. To sign every device out, change
  `PANEL_PASSWORD` and redeploy.
- The settings endpoint only stores a fixed list of known settings, and
  ignores anything else in a request.
- Uploaded images are checked by their actual bytes, not by what the browser
  says they are.
- The scheduler's URL is public too, because Neon needs it to be. It only acts
  when Neon's trigger header is present, and Neon strips that header from
  anyone else's request.
