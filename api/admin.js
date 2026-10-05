// /admin — the daily report, server-rendered. Same password and session as the
// control panel (sign in once, both work). Read-only: it shows the drafts for
// review and posts nothing.

import { authed } from "./_lib.js";
import { db, configured } from "./_db.js";
import { money } from "./_daily.js";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DAY = /^\d{4}-\d{2}-\d{2}$/;

const CSS = `
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6a64;--line:#e4e2da;--accent:#2f5d50;--warn:#9a5b00}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#f0eee8;--mute:#a09e95;--line:#33322d;--accent:#7fc3ad;--warn:#e0a24a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 10px}
.sub{color:var(--mute);margin:0 0 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.big{font-size:1.6rem;font-weight:650}.mute{color:var(--mute);font-size:.9rem}.warn{color:var(--warn)}
.badge{display:inline-block;font-size:.75rem;border:1px solid var(--line);border-radius:99px;padding:1px 8px;color:var(--mute);text-transform:capitalize}
.cap{white-space:pre-wrap;margin:8px 0}.tags{color:var(--accent);word-break:break-word}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 20px}nav a{padding:3px 10px;border:1px solid var(--line);border-radius:99px;color:var(--ink);text-decoration:none;font-size:.85rem}
nav a[aria-current]{background:var(--accent);color:var(--bg);border-color:var(--accent)}a{color:var(--accent)}
form{display:flex;gap:8px;flex-wrap:wrap}input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line)}input{flex:1;min-width:200px;background:var(--card);color:var(--ink)}
button{background:var(--accent);color:var(--bg);border-color:var(--accent);cursor:pointer}
`;

function page(res, status, body, title = "Daily report") {
  res.statusCode = status;
  res.setHeader("content-type", "text/html; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.setHeader("x-robots-tag", "noindex");
  res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>${esc(title)}</title><style>${CSS}</style></head><body><main>${body}</main></body></html>`);
}

function login(res, message) {
  page(res, 401, `<h1>Daily report</h1><p class="sub">Sign in with your control-panel password.</p>
${message ? `<p class="warn">${esc(message)}</p>` : ""}
<form id="f"><input type="password" name="password" placeholder="Password" autocomplete="current-password" required autofocus><button>Sign in</button></form>
<p id="e" class="warn" role="alert"></p>
<script>
document.getElementById("f").addEventListener("submit",async(ev)=>{ev.preventDefault();
const r=await fetch("/api/session",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({password:ev.target.password.value})});
if(r.ok){location.reload()}else{const d=await r.json().catch(()=>({}));document.getElementById("e").textContent=d.error||"Sign-in failed."}});
</script>`, "Sign in");
}

const stat = (label, w, cur) =>
  `<div class="card"><div class="mute">${esc(label)}</div><div class="big">${esc(money(w.revenue_cents, cur))}</div><div class="mute">${w.sales} sale${w.sales === 1 ? "" : "s"}</div></div>`;

const productCard = (label, p, cur, extra = "") =>
  `<div class="card"><div class="mute">${esc(label)}</div><div><strong>${p ? esc(p.name) : "—"}</strong></div>
${p ? `<div class="mute">${p.sales_7d} sale${p.sales_7d === 1 ? "" : "s"} · ${esc(money(p.revenue_7d_cents, cur))} in 7 days</div>` : ""}${extra}</div>`;

function render(r) {
  const s = r.summary;
  const cur = s.currency;
  const best = s.best;
  const drafts = (r.drafts || [])
    .map((d) => `<div class="card"><span class="badge">${esc(d.platform)}</span>
<div class="cap">${esc(d.caption)}</div><div class="tags">${(d.hashtags || []).map(esc).join(" ")}</div>
<div class="mute">Image idea: ${esc(d.image_idea)}</div></div>`)
    .join("");
  const idea = r.template_idea || {};
  const notes = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
  return `<h2>Last 24 hours and 7 days</h2><div class="grid">
${stat("Last 24 hours", s.last_24h, cur)}${stat("Last 7 days", s.last_7d, cur)}</div>
<h2>Products</h2><div class="grid">
${productCard("Best", best, cur, s.best_basis ? `<div class="mute">Based on ${esc(s.best_basis)}</div>` : "")}
${productCard("Worst", s.worst, cur)}</div>
<h2>Promo drafts for ${best ? esc(best.name) : "your top product"}</h2>
<p class="mute">For review. Nothing was posted.${r.drafts_source === "claude" ? "" : " <span class=\"warn\">Template drafts, not written by Claude.</span>"}</p>
<div class="grid">${drafts}</div>
<h2>Template idea</h2><div class="card"><strong>${esc(idea.title)}</strong><div class="cap">${esc(idea.why)}</div><div class="mute">${esc(idea.outline)}</div></div>
${notes ? `<h2>Notes</h2><ul class="mute">${notes}</ul>` : ""}`;
}

export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.statusCode = 405;
    return res.end("Method not allowed.");
  }
  if (!(process.env.PANEL_PASSWORD || "").trim()) {
    return page(res, 503, `<h1>Daily report</h1><p class="warn">PANEL_PASSWORD is not set on this deployment. Add it in Vercel → Settings → Environment Variables, then redeploy.</p>`);
  }
  if (!authed(req)) return login(res);
  if (!configured()) {
    return page(res, 503, `<h1>Daily report</h1><p class="warn">DATABASE_URL is not set, so there are no saved reports to show.</p>`);
  }

  try {
    const days = (await db("select day::text as day from autopost_daily_reports order by day desc limit 30")).map((r) => r.day);
    const url = new URL(req.url || "/admin", "http://x");
    const asked = url.searchParams.get("day");
    const day = asked && DAY.test(asked) ? asked : days[0];
    if (!day) {
      return page(res, 200, `<h1>Daily report</h1><p class="sub">No report yet.</p><p>The first one is written by the 08:00 Manila cron. To make one now, see “How to test” in docs/DAILY.md.</p>`);
    }
    const rows = await db("select report, created_at from autopost_daily_reports where day = $1", [day]);
    if (!rows.length) {
      return page(res, 404, `<h1>Daily report</h1><p class="warn">No report for ${esc(day)}.</p><p><a href="/admin">Latest report</a></p>`);
    }
    const report = typeof rows[0].report === "string" ? JSON.parse(rows[0].report) : rows[0].report;
    const nav = `<nav aria-label="Days">${days.map((d) => `<a href="/admin?day=${d}"${d === day ? " aria-current=\"page\"" : ""}>${d}</a>`).join("")}</nav>`;
    const when = new Date(report.generated_at).toUTCString();
    return page(res, 200, `<h1>Daily report · ${esc(day)}</h1><p class="sub">Generated ${esc(when)} · ${esc(report.timezone)}</p>${nav}${render(report)}`);
  } catch (err) {
    return page(res, 500, `<h1>Daily report</h1><p class="warn">Could not load reports: ${esc(err.message || err)}</p>`);
  }
}
