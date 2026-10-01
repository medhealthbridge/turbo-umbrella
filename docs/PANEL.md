# The control panel

A web page that runs your shop: change the schedule, edit a listing, upload a
cover, publish on demand. You never open the code.

It lives at your Vercel URL. Changes it makes are committed to this
repository, and the next run picks them up.

---

## Turning it on (once, about five minutes)

### 1. A GitHub token

The panel edits your repository on your behalf, so it needs a token.

1. <https://github.com/settings/personal-access-tokens/new> — a **fine-grained**
   token.
2. **Repository access → Only select repositories →** this repository.
3. **Permissions → Repository permissions**, set these three:

   | Permission | Access |
   |---|---|
   | Contents | Read and write |
   | Actions | Read and write |
   | Metadata | Read-only (added for you) |

4. Give it an expiry you are happy with, create it, and copy it. GitHub shows
   it once.

Nothing wider than that is needed. The token cannot touch your other
repositories.

### 2. Two environment variables in Vercel

Vercel → your project → **Settings → Environment Variables**. Add both for
**Production**:

| Name | Value |
|---|---|
| `PANEL_PASSWORD` | a password you choose — this is what signs you in |
| `GITHUB_TOKEN` | the token from step 1 |

Optional:

| Name | Default | |
|---|---|---|
| `GITHUB_REPO` | `medhealthbridge/turbo-umbrella` | if you fork or rename |
| `GITHUB_BRANCH` | `main` | the branch the panel reads and writes |
| `PANEL_SECRET` | your password | rotating it signs every device out |

### 3. Redeploy

Vercel → **Deployments → … → Redeploy**. Environment variables only reach the
functions on a fresh deploy.

Open the URL, enter your password, and you are in. The session lasts two
weeks per device.

---

## What each tab does

**Overview** — what is live, what is queued, what needs attention, and the
last few runs. The *Run it now* box replaces going to the Actions tab:

- *Publish the next book* — the normal cycle
- *Look for new files only* — notices new PDFs, publishes nothing
- *Show the plan* — prints the schedule and queue, changes nothing
- *Rebuild the dashboard data* — refreshes `docs/status.json`

**Dry run is ticked by default.** Untick it to publish for real. *Force*
ignores the posting window.

**Schedule** — posting time in your own time zone, which days, how many per
run, the minimum gap between publishes, how long a late run still counts, and
how long a new file must settle before it is safe. Also the default price and
the Drive folder.

**Marketing** — who you are writing for, your shop's promise, the reassurance
points, the tags added to every book, the refund period and the receipt email.
These shape every listing the generator writes.

**Books** — pick any book, queued or live, and override its title, price,
description or tags. Leave a field blank and the generator takes it back over.
You can also upload a cover here, which replaces the one rendered from page 1.

---

## Where your changes go

| In the panel | Written to | Effect |
|---|---|---|
| Schedule, Marketing | `state/settings.json` | merged over `config.yml` |
| A book's listing | `books.json` | per-book override |
| A cover upload | `assets/artwork/<slug>-cover.jpg` | beats the rendered cover |
| Run it now | a workflow run | immediate |

`config.yml` is never machine-rewritten, so its comments survive and you can
still edit it by hand. The panel's settings win where both set the same thing.

---

## Notes

**The URL is public; the panel is not.** Every endpoint except sign-in
requires the session cookie, and the cookie is signed with your password. If
you ever think the password leaked, change `PANEL_PASSWORD` in Vercel and
redeploy — that invalidates every signed-in device.

**Settings apply on the next run**, not retroactively. A book already
published is not rewritten by a marketing change; re-publish it from the
Books tab if you want it updated.

**`docs/status.json` is world-readable** at `your-url/status.json`. It carries
titles, prices, product links and any error text. Nothing secret, but if you
would rather it were private, turn on Vercel's Deployment Protection.
