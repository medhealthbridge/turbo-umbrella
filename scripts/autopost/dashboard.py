"""The status page.

One self-contained HTML file, rebuilt after every run and committed. No
build step, no framework, no network calls — open it from the repo, from
your phone, or publish it with the "Deploy dashboard" workflow.

It answers the four questions you actually have: what is live, what is
queued, when does the next one go out, and what broke.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import log, scheduler
from .config import ROOT

TEMPLATE = """<!doctype html>
<html lang="en" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f6f7f9;       --panel: #ffffff;     --panel-2: #fbfbfd;
  --ink: #16181d;      --muted: #6b7280;     --line: #e4e6ec;
  --accent: #4f46e5;   --accent-soft: #eef2ff;
  --ok: #067647;       --ok-soft: #e7f6ee;
  --warn: #92400e;     --warn-soft: #fef3c7;
  --bad: #b42318;      --bad-soft: #fee4e2;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(16,18,24,.05), 0 8px 24px -12px rgba(16,18,24,.12);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0d0f14;     --panel: #14171f;     --panel-2: #191d27;
    --ink: #e9eaf0;    --muted: #9aa1b1;     --line: #262b37;
    --accent: #a5b4fc; --accent-soft: #1e2140;
    --ok: #6ee7b7;     --ok-soft: #0d2b22;
    --warn: #fcd34d;   --warn-soft: #2e2410;
    --bad: #fca5a5;    --bad-soft: #341a1a;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"] {
  --bg: #0d0f14;       --panel: #14171f;     --panel-2: #191d27;
  --ink: #e9eaf0;      --muted: #9aa1b1;     --line: #262b37;
  --accent: #a5b4fc;   --accent-soft: #1e2140;
  --ok: #6ee7b7;       --ok-soft: #0d2b22;
  --warn: #fcd34d;     --warn-soft: #2e2410;
  --bad: #fca5a5;      --bad-soft: #341a1a;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.7);
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Inter, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 32px 16px 72px; }

header { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 26px; }
header .grow { flex: 1 1 260px; min-width: 0; }
h1 { font-size: 26px; margin: 0 0 2px; letter-spacing: -.02em; }
.sub { color: var(--muted); font-size: 14px; margin: 0; }
button.theme {
  background: var(--panel); color: var(--muted); border: 1px solid var(--line);
  border-radius: 999px; padding: 7px 14px; font: inherit; font-size: 13px; cursor: pointer;
}
button.theme:hover { color: var(--ink); border-color: var(--muted); }

.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 26px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 18px; box-shadow: var(--shadow); }
.kpi .n { font-size: 27px; font-weight: 640; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.kpi .l { color: var(--muted); font-size: 12.5px; text-transform: uppercase; letter-spacing: .06em; margin-top: 2px; }

.next { display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  background: var(--accent-soft); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: var(--radius); padding: 15px 18px; margin-bottom: 30px; }
.next .when { font-weight: 640; }
.next .why { color: var(--muted); font-size: 13.5px; }

h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted);
  margin: 0 0 12px; font-weight: 620; }
section { margin-bottom: 34px; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 14px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  overflow: hidden; box-shadow: var(--shadow); display: flex; flex-direction: column; }
.card .art { aspect-ratio: 1; background: var(--panel-2) center/cover no-repeat;
  border-bottom: 1px solid var(--line); display: grid; place-items: center; color: var(--muted); font-size: 12px; }
.card .body { padding: 13px 14px 14px; display: flex; flex-direction: column; gap: 7px; flex: 1; }
.card .t { font-weight: 600; font-size: 14px; line-height: 1.35; }
.card .t a { color: inherit; text-decoration: none; }
.card .t a:hover { color: var(--accent); text-decoration: underline; }
.card .meta { color: var(--muted); font-size: 12.5px; display: flex; gap: 8px; flex-wrap: wrap;
  margin-top: auto; font-variant-numeric: tabular-nums; }

.chips { display: flex; gap: 5px; flex-wrap: wrap; }
.chip { font-size: 11px; padding: 2.5px 8px; border-radius: 999px; border: 1px solid var(--line);
  color: var(--muted); background: var(--panel-2); white-space: nowrap; }
.chip.ok   { color: var(--ok);   background: var(--ok-soft);   border-color: transparent; }
.chip.warn { color: var(--warn); background: var(--warn-soft); border-color: transparent; }
.chip.bad  { color: var(--bad);  background: var(--bad-soft);  border-color: transparent; }

table { width: 100%; border-collapse: collapse; background: var(--panel);
  border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); }
th, td { text-align: left; padding: 11px 14px; border-bottom: 1px solid var(--line); font-size: 14px; vertical-align: top; }
th { font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 620; background: var(--panel-2); }
tr:last-child td { border-bottom: 0; }
td.slug { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
td.why { color: var(--muted); font-size: 13px; }

.empty { background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius);
  padding: 26px; text-align: center; color: var(--muted); font-size: 14px; }

details.logs { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
details.logs summary { padding: 13px 16px; cursor: pointer; font-size: 13.5px; color: var(--muted); user-select: none; }
details.logs summary:hover { color: var(--ink); }
details.logs pre { margin: 0; padding: 0 16px 16px; font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; word-break: break-word; border-top: 1px solid var(--line); padding-top: 14px; }
.lvl-error { color: var(--bad); } .lvl-warn { color: var(--warn); } .lvl-ok { color: var(--ok); }

footer { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 44px; }
footer code { background: var(--panel); padding: 2px 6px; border-radius: 6px; border: 1px solid var(--line); }

@media (max-width: 560px) {
  .wrap { padding: 22px 14px 56px; }
  h1 { font-size: 22px; }
  .kpi .n { font-size: 23px; }
  th, td { padding: 9px 11px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="grow">
      <h1>__TITLE__</h1>
      <p class="sub">__SUBTITLE__</p>
    </div>
    <button class="theme" onclick="toggleTheme()">Theme</button>
  </header>

  <div class="kpis">__KPIS__</div>

  <div class="next">
    <div>
      <div class="when">Next window · __NEXT__</div>
      <div class="why">__SCHEDULE_NOTE__</div>
    </div>
  </div>

  <section>
    <h2>Live</h2>
    __LIVE__
  </section>

  <section>
    <h2>Queue</h2>
    __QUEUE__
  </section>

  <section>
    <h2>Needs attention</h2>
    __PROBLEMS__
  </section>

  <section>
    <h2>Last run</h2>
    <details class="logs"><summary>__LOG_COUNT__ log lines — __GENERATED__</summary><pre>__LOG__</pre></details>
  </section>

  <footer>
    Rebuilt automatically after every run · edit <code>config.yml</code> to change the schedule, price or copy
  </footer>
</div>
<script>
function toggleTheme() {
  const root = document.documentElement;
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const now = root.dataset.theme === 'auto' ? (dark ? 'light' : 'dark')
            : root.dataset.theme === 'dark' ? 'light' : 'dark';
  root.dataset.theme = now;
  try { localStorage.setItem('autopost-theme', now); } catch (e) {}
}
try {
  const saved = localStorage.getItem('autopost-theme');
  if (saved) document.documentElement.dataset.theme = saved;
} catch (e) {}
</script>
</body>
</html>
"""


def _esc(text):
    import html

    return html.escape(str(text if text is not None else ""), quote=True)


def _fmt_date(value):
    from .state import parse_time

    moment = parse_time(value)
    return moment.strftime("%d %b %Y") if moment else "—"


def _status_chip(status):
    kind = {
        "published": "ok",
        "partial": "warn",
        "draft": "warn",
        "pending": "warn",
        "failed": "bad",
    }.get(status, "")
    return f'<span class="chip {kind}">{_esc(status)}</span>'


def _kpi(number, label):
    return f'<div class="kpi"><div class="n">{_esc(number)}</div><div class="l">{_esc(label)}</div></div>'


def _live_cards(entries, cfg, covers_dir):
    if not entries:
        return '<div class="empty">Nothing is live yet. Drop a PDF in the Drive folder and it will appear here.</div>'
    currency = cfg.get("dashboard.currency", "$")
    cards = []
    for entry in reversed(entries):
        slug = entry.get("slug", "")
        title = entry.get("title") or entry.get("name") or slug
        cover = covers_dir / f"{slug}.jpg"
        art = (
            f'<div class="art" style="background-image:url(covers/{_esc(slug)}.jpg)"></div>'
            if cover.exists()
            else '<div class="art">no cover</div>'
        )

        url = None
        chips = []
        for name, result in sorted((entry.get("platforms") or {}).items()):
            status = (result or {}).get("status", "?")
            kind = {"published": "ok", "draft": "warn", "failed": "bad"}.get(status, "")
            chips.append(f'<span class="chip {kind}">{_esc(name)}</span>')
            url = url or (result or {}).get("url")

        heading = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(title)}</a>' if url else _esc(title)
        meta = [f"{currency}{_esc(entry.get('price', '—'))}"]
        if entry.get("pages"):
            meta.append(f"{_esc(entry['pages'])} pages")
        meta.append(_fmt_date(entry.get("published_at")))
        if entry.get("ai_polished"):
            chips.append('<span class="chip">AI copy</span>')

        cards.append(
            f'<div class="card">{art}<div class="body">'
            f'<div class="t">{heading}</div>'
            f'<div class="chips">{"".join(chips)}</div>'
            f'<div class="meta">{" · ".join(meta)}</div>'
            "</div></div>"
        )
    return f'<div class="grid">{"".join(cards)}</div>'


def _queue_table(queue):
    if not queue:
        return '<div class="empty">The queue is empty — every PDF in the folder is already live.</div>'
    rows = "".join(
        f'<tr><td class="slug">{_esc(item["slug"])}</td>'
        f'<td>{_esc(item["name"])}</td>'
        f'<td class="why">{_esc(item["note"])}</td></tr>'
        for item in queue
    )
    return f"<table><tr><th>Slug</th><th>File</th><th>Status</th></tr>{rows}</table>"


def _problems(entries):
    bad = [e for e in entries if e.get("status") in ("failed", "partial", "draft", "pending")]
    if not bad:
        return '<div class="empty">Nothing needs attention.</div>'
    rows = []
    for entry in bad:
        why = entry.get("error") or ""
        if not why:
            why = "; ".join(
                f"{name}: {(result or {}).get('error') or (result or {}).get('status')}"
                for name, result in sorted((entry.get("platforms") or {}).items())
                if (result or {}).get("status") != "published"
            )
        rows.append(
            f'<tr><td class="slug">{_esc(entry.get("slug"))}</td>'
            f"<td>{_status_chip(entry.get('status'))}</td>"
            f'<td class="why">{_esc(why or "—")}</td></tr>'
        )
    return f'<table><tr><th>Slug</th><th>State</th><th>What happened</th></tr>{"".join(rows)}</table>'


def build(cfg, st, queue=None, output=None):
    """Write the dashboard. `queue` is [{slug, name, note}, ...]."""
    output = Path(output or ROOT / cfg.get("dashboard.output", "docs/index.html"))
    output.parent.mkdir(parents=True, exist_ok=True)
    covers_dir = output.parent / "covers"

    entries = st.entries
    live = [e for e in entries if e.get("status") == "published"]

    queue = queue or []
    if not queue:
        live_slugs = {e["slug"] for e in live}
        queue = [
            {"slug": meta.get("slug", ""), "name": meta.get("name", ""), "note": "waiting for its turn"}
            for meta in st.seen.values()
            if meta.get("slug") not in live_slugs
        ]

    currency = cfg.get("dashboard.currency", "$")
    catalog_value = sum(float(e.get("price") or 0) for e in live)
    pages = sum(int(e.get("pages") or 0) for e in live)

    kpis = "".join(
        [
            _kpi(len(live), "live"),
            _kpi(len(queue), "queued"),
            _kpi(f"{currency}{catalog_value:,.2f}", "catalog value"),
            _kpi(f"{pages:,}", "pages published"),
        ]
    )

    upcoming = scheduler.next_run(cfg)
    note = (
        f"{cfg.get('schedule.max_per_run', 1)} per run · "
        f"{', '.join(cfg.publish_days)} at {cfg.publish_time:%H:%M} "
        f"{cfg.get('schedule.timezone')} · "
        f"platforms: {', '.join(cfg.enabled_platforms()) or 'none'}"
    )

    log_lines = log.lines()
    rendered_log = "\n".join(
        f'<span class="lvl-{line["level"]}">{_esc(line["text"].strip())}</span>'
        for line in log_lines
        if line["text"].strip()
    )

    html_out = TEMPLATE
    for token, value in {
        "__TITLE__": _esc(cfg.get("dashboard.title", "Autopost")),
        "__SUBTITLE__": _esc(cfg.get("dashboard.subtitle", "")),
        "__KPIS__": kpis,
        "__NEXT__": _esc(upcoming.strftime("%a %d %b, %H:%M %Z")),
        "__SCHEDULE_NOTE__": _esc(note),
        "__LIVE__": _live_cards(live, cfg, covers_dir),
        "__QUEUE__": _queue_table(queue),
        "__PROBLEMS__": _problems(entries),
        "__LOG__": rendered_log or "(no log for this run)",
        "__LOG_COUNT__": str(len(log_lines)),
        "__GENERATED__": _esc(datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")),
    }.items():
        html_out = html_out.replace(token, value)

    output.write_text(html_out, encoding="utf-8")
    try:
        shown = output.relative_to(ROOT)
    except ValueError:  # an output path outside the repo, e.g. a preview
        shown = output
    log.ok(f"dashboard: {shown}", indent=1)

    # The same numbers as data, for anything else that wants them.
    (output.parent / "status.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "live": len(live),
                "queued": len(queue),
                "catalog_value": round(catalog_value, 2),
                "next_window": upcoming.isoformat(),
                "books": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return output
