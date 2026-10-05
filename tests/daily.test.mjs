// The daily Gumroad report: summary maths, the cron endpoint's guard and
// saving, and the /admin page. Gumroad and Claude are local mock servers, so
// nothing leaves the machine; the database is a real Postgres.
//
//   AUTOPOST_TEST_DATABASE_URL=postgresql://... npm test

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { randomBytes } from "node:crypto";
import * as daily from "../api/_daily.js";

const ADMIN = (process.env.AUTOPOST_TEST_DATABASE_URL || "").trim();
const skip = ADMIN ? false : "set AUTOPOST_TEST_DATABASE_URL to run the daily-report tests";
const SECRET = "cron-secret-0123456789";

let pg, dbName, cron, admin, session, cookie;
const servers = [];
const seen = { gumroad: [], anthropic: [] };
let gumroadMode = "ok";
let claudeMode = "ok";

const urlFor = (db) => ADMIN.replace(/\/[^/?]*(\?|$)/, `/${db}$1`);
async function adminSql(sql) {
  const c = new pg.Client({ connectionString: ADMIN });
  await c.connect();
  try { return await c.query(sql); } finally { await c.end(); }
}

function call(handler, { method = "GET", headers = {}, url = "/x" } = {}) {
  return new Promise((resolve, reject) => {
    const req = { method, url, headers };
    const out = {};
    const res = {
      statusCode: 200,
      setHeader: (k, v) => { out[k.toLowerCase()] = v; },
      end: (payload) => {
        let data = payload;
        if ((out["content-type"] || "").startsWith("application/json")) data = JSON.parse(payload);
        resolve({ status: res.statusCode, headers: out, data });
      },
    };
    Promise.resolve(handler(req, res)).catch(reject);
  });
}

const hoursAgo = (h) => new Date(Date.now() - h * 3600 * 1000).toISOString();
const PRODUCTS = [
  { id: "p1", name: "Animal Friends Coloring Book", price: 500, short_url: "https://example.gumroad.com/l/animals", published: true, sales_count: 40 },
  { id: "p2", name: "Shapes <script>alert(1)</script> Pack", price: 300, short_url: "javascript:alert(1)", published: true, sales_count: 3 },
  { id: "p3", name: "Old Draft", price: 100, published: false, sales_count: 99 },
];
const sale = (id, product, cents, hours, extra = {}) =>
  ({ id, product_id: product, product_name: product, price: cents, currency: "usd", created_at: hoursAgo(hours), refunded: false, ...extra });
// Page 1 / page 2 of the sales list, to exercise the cursor.
const PAGE1 = [sale("s1", "p1", 500, 2), sale("s2", "p1", 500, 30), sale("s3", "p2", 300, 100, { product_name: "Shapes" })];
const PAGE2 = [sale("s4", "p1", 500, 5, { refunded: true }), sale("s5", "p1", 500, 400)];

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    servers.push(server);
    server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`));
  });
}

before(async () => {
  if (skip) return;
  pg = (await import("pg")).default;
  dbName = "autopost_daily_" + randomBytes(5).toString("hex");
  await adminSql(`create database "${dbName}"`);
  process.env.DATABASE_URL = urlFor(dbName);
  process.env.AUTOPOST_PG_DRIVER = "pg";
  process.env.PANEL_PASSWORD = "correct horse";
  process.env.CRON_SECRET = SECRET;
  process.env.GUMROAD_ACCESS_TOKEN = "gum-test-token";
  process.env.REPORT_TZ = "Asia/Manila";

  process.env.GUMROAD_API_BASE = await listen((req, res) => {
    const url = new URL(req.url, "http://x");
    seen.gumroad.push({ path: url.pathname, auth: req.headers.authorization, query: Object.fromEntries(url.searchParams) });
    res.setHeader("content-type", "application/json");
    if (gumroadMode === "denied") { res.statusCode = 401; return res.end(JSON.stringify({ success: false, message: "Invalid token" })); }
    if (url.pathname === "/products") return res.end(JSON.stringify({ success: true, products: PRODUCTS }));
    if (url.pathname === "/sales") {
      const second = url.searchParams.get("page_key") === "k2";
      return res.end(JSON.stringify({ success: true, sales: second ? PAGE2 : PAGE1, ...(second ? {} : { next_page_key: "k2" }) }));
    }
    res.statusCode = 404; res.end("{}");
  });

  process.env.ANTHROPIC_API_KEY = "sk-ant-test";
  process.env.ANTHROPIC_BASE_URL = await listen((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.anthropic.push({ url: req.url, key: req.headers["x-api-key"], body: JSON.parse(body || "{}") });
      res.setHeader("content-type", "application/json");
      if (claudeMode === "down") { res.statusCode = 500; return res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: "boom" } })); }
      const text = JSON.stringify({
        drafts: ["pinterest", "facebook", "tiktok"].map((platform) => ({
          platform, caption: `Claude caption for ${platform}`, hashtags: ["Coloring Book", "#kids"], image_idea: `Idea for ${platform}`,
        })),
        template_idea: { title: "Alphabet Tracing Pack", why: "Animals sell; letters are next.", outline: "26 pages." },
      });
      res.end(JSON.stringify({
        id: "msg_1", type: "message", role: "assistant", model: "claude-opus-5-5", stop_reason: "end_turn", stop_sequence: null,
        content: [{ type: "text", text }], usage: { input_tokens: 10, output_tokens: 10 },
      }));
    });
  });

  cron = (await import("../api/cron/daily.js")).default;
  admin = (await import("../api/admin.js")).default;
  session = (await import("../api/session.js")).default;
});

after(async () => {
  if (skip) return;
  for (const s of servers) s.close();
  await (await import("../api/_db.js"))._reset();
  await adminSql(`drop database if exists "${dbName}"`).catch(() => {});
});

const authHeader = { authorization: `Bearer ${SECRET}` };

// ---------------------------------------------------------------- summary

test("summary: 24h and 7d totals, refunds excluded, best and worst", () => {
  const sales = [...PAGE1, ...PAGE2];
  const s = daily.summarize(PRODUCTS, sales, new Date());
  assert.deepEqual(s.last_24h, { sales: 1, revenue_cents: 500 });
  // s1, s2, s3 count; s4 is refunded; s5 is 400h old.
  assert.deepEqual(s.last_7d, { sales: 3, revenue_cents: 1300 });
  assert.equal(s.best.id, "p1");
  assert.equal(s.best_basis, "last 7 days");
  assert.equal(s.worst.id, "p2");
  assert.ok(!s.products.some((p) => p.id === "p3"), "unpublished products are not ranked");
});

test("summary: nothing sold this week falls back to all-time for the best", () => {
  const s = daily.summarize(PRODUCTS, [], new Date());
  assert.equal(s.best.id, "p1");
  assert.match(s.best_basis, /all-time/);
  assert.deepEqual(s.last_7d, { sales: 0, revenue_cents: 0 });
});

test("summary: one product has no 'worst'; no products has no 'best'", () => {
  assert.equal(daily.summarize([PRODUCTS[0]], [], new Date()).worst, null);
  assert.equal(daily.summarize([], [], new Date()).best, null);
});

test("reportDay uses the shop's time zone, and links must be http(s)", () => {
  // 17:00 UTC on the 4th is 01:00 on the 5th in Manila.
  assert.equal(daily.reportDay(new Date("2026-10-04T17:00:00Z"), "Asia/Manila"), "2026-10-05");
  assert.equal(daily.reportDay(new Date("2026-10-04T17:00:00Z"), "UTC"), "2026-10-04");
  assert.equal(daily.safeUrl("javascript:alert(1)"), "");
  assert.equal(daily.safeUrl("https://a.example/x"), "https://a.example/x");
});

// ------------------------------------------------------------- the cron

test("cron refuses to run without a secret, or with the wrong one", { skip }, async () => {
  const wrong = await call(cron, { headers: { authorization: "Bearer nope" } });
  assert.equal(wrong.status, 401);
  const none = await call(cron);
  assert.equal(none.status, 401);
  assert.equal(seen.gumroad.length, 0, "an unauthorised call must not reach Gumroad");

  process.env.CRON_SECRET = "";
  try {
    const open = await call(cron, { headers: { authorization: "Bearer " } });
    assert.equal(open.status, 503, "an unset secret must fail closed, not run open");
  } finally {
    process.env.CRON_SECRET = SECRET;
  }
  assert.equal((await call(cron, { method: "DELETE", headers: authHeader })).status, 405);
});

test("cron builds the report with Claude's drafts and saves it", { skip }, async () => {
  const r = await call(cron, { headers: authHeader });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(r.data.drafts_source, "claude");

  // Gumroad: bearer token, both endpoints, cursor followed, date window sent.
  assert.ok(seen.gumroad.every((g) => g.auth === "Bearer gum-test-token"));
  const sales = seen.gumroad.filter((g) => g.path === "/sales");
  assert.equal(sales.length, 2);
  assert.match(sales[0].query.after, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(sales[1].query.page_key, "k2");

  // Claude: the key was sent, structured output was requested, nothing exotic.
  const ai = seen.anthropic.at(-1);
  assert.equal(ai.key, "sk-ant-test");
  assert.equal(ai.body.output_config.format.type, "json_schema");
  assert.ok(!("temperature" in ai.body) && !("thinking" in ai.body));

  const report = r.data.report;
  assert.deepEqual(report.drafts.map((d) => d.platform), ["pinterest", "facebook", "tiktok"]);
  assert.deepEqual(report.drafts[0].hashtags, ["#coloringbook", "#kids"]);
  assert.equal(report.template_idea.title, "Alphabet Tracing Pack");
  assert.equal(report.summary.last_7d.sales, 3);
  assert.ok(report.notes.some((n) => /Nothing has been posted/.test(n)));
  assert.ok(!JSON.stringify(report).includes("@"), "no buyer emails in the saved report");

  const { db } = await import("../api/_db.js");
  const saved = await db("select day::text as day, report from autopost_daily_reports");
  assert.equal(saved.length, 1);
  assert.equal(saved[0].day, report.day);
});

test("a second run on the same day replaces the report instead of adding one", { skip }, async () => {
  const { db } = await import("../api/_db.js");
  await call(cron, { headers: authHeader });
  assert.equal((await db("select count(*)::int as n from autopost_daily_reports"))[0].n, 1);
});

test("if Claude fails the report still completes with template drafts", { skip }, async () => {
  claudeMode = "down";
  try {
    const r = await call(cron, { headers: authHeader });
    assert.equal(r.status, 200);
    assert.equal(r.data.drafts_source, "template");
    assert.equal(r.data.report.drafts.length, 3);
    assert.match(r.data.report.notes.join(" "), /Claude was unavailable/);
  } finally {
    claudeMode = "ok";
  }
});

test("without ANTHROPIC_API_KEY it uses templates and never calls Claude", { skip }, async () => {
  const key = process.env.ANTHROPIC_API_KEY;
  delete process.env.ANTHROPIC_API_KEY;
  const before = seen.anthropic.length;
  try {
    const r = await call(cron, { headers: authHeader });
    assert.equal(r.data.drafts_source, "template");
    assert.equal(seen.anthropic.length, before);
  } finally {
    process.env.ANTHROPIC_API_KEY = key;
  }
});

test("a Gumroad failure is reported and nothing is saved", { skip }, async () => {
  const { db } = await import("../api/_db.js");
  await db("delete from autopost_daily_reports");
  gumroadMode = "denied";
  try {
    const r = await call(cron, { headers: authHeader });
    assert.equal(r.status, 502);
    assert.match(r.data.error, /view_sales/);
    assert.equal((await db("select count(*)::int as n from autopost_daily_reports"))[0].n, 0);
  } finally {
    gumroadMode = "ok";
  }
});

// ----------------------------------------------------------------- /admin

test("/admin asks for the password and shows nothing without a session", { skip }, async () => {
  const r = await call(admin, { url: "/admin" });
  assert.equal(r.status, 401);
  assert.match(r.data, /Sign in/);
  assert.doesNotMatch(r.data, /Animal Friends/);
});

test("/admin shows the saved report to a signed-in browser, escaped", { skip }, async () => {
  const { db } = await import("../api/_db.js");
  const { issueToken } = await import("../api/_lib.js");
  cookie = `autopost_session=${issueToken()}`;

  const empty = await call(admin, { url: "/admin", headers: { cookie } });
  assert.equal(empty.status, 200);
  assert.match(empty.data, /No report yet/);

  await call(cron, { headers: authHeader });
  const page = await call(admin, { url: "/admin", headers: { cookie } });
  assert.equal(page.status, 200);
  assert.equal(page.headers["cache-control"], "no-store");
  assert.match(page.data, /Animal Friends Coloring Book/);
  assert.match(page.data, /Claude caption for tiktok/);
  assert.match(page.data, /Alphabet Tracing Pack/);
  assert.match(page.data, /Nothing was posted/);
  assert.match(page.data, /13\.00 USD/);
  assert.doesNotMatch(page.data, /<script>alert/, "product names must be escaped");

  // A poisoned product name stored in a report cannot inject markup either.
  const row = (await db("select day::text as day, report from autopost_daily_reports"))[0];
  row.report.summary.worst.name = `<img src=x onerror=alert(1)>`;
  await db("update autopost_daily_reports set report = $1::jsonb", [JSON.stringify(row.report)]);
  const poisoned = await call(admin, { url: `/admin?day=${row.day}`, headers: { cookie } });
  assert.doesNotMatch(poisoned.data, /<img src=x/);

  assert.equal((await call(admin, { url: "/admin?day=1999-01-01", headers: { cookie } })).status, 404);
  assert.equal((await call(admin, { url: "/admin?day=x'; drop table", headers: { cookie } })).status, 200);
});

// ------------------------------------------------------------- deployment

test("vercel.json schedules the endpoint at 08:00 Manila (00:00 UTC)", () => {
  const cfg = JSON.parse(readFileSync(new URL("../vercel.json", import.meta.url), "utf8"));
  assert.deepEqual(cfg.crons, [{ path: "/api/cron/daily", schedule: "0 0 * * *" }]);
  assert.ok(existsSync(new URL("../api/cron/daily.js", import.meta.url)));
  assert.ok(cfg.functions["api/cron/daily.js"], "nested api files need their own functions entry");
  assert.ok(cfg.rewrites.some((r) => r.source === "/admin" && r.destination === "/api/admin"));
});
