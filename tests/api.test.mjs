// The control panel's API, against a real Postgres.
//
//   AUTOPOST_TEST_DATABASE_URL=postgresql://... npm test*.test.mjs
//
// Each run creates a throwaway database, points the handlers at it through
// node-postgres (the production code path uses Neon's HTTP driver; every
// statement is identical), and drops it afterwards. Without a database URL
// the suite skips rather than failing, so `npm test` is safe anywhere.

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";

const ADMIN = (process.env.AUTOPOST_TEST_DATABASE_URL || "").trim();
const skip = ADMIN ? false : "set AUTOPOST_TEST_DATABASE_URL to run the API tests";

let pg, dbName, api = {};

// Swap the database name in a connection string. Not `new URL()`: a socket
// connection string (postgresql://user@/db?host=/run/postgresql) has no host,
// which WHATWG URL parsing rejects.
function urlFor(db) {
  return ADMIN.replace(/\/[^/?]*(\?|$)/, `/${db}$1`);
}

async function admin(sql) {
  const client = new pg.Client({ connectionString: ADMIN });
  await client.connect();
  try { return await client.query(sql); } finally { await client.end(); }
}

// ------------------------------------------------------------ fake http

function call(handler, { method = "GET", body, cookie, url = "/api/x" } = {}) {
  return new Promise((resolve, reject) => {
    const req = { method, url, headers: cookie ? { cookie } : {}, body };
    const headers = {};
    const res = {
      statusCode: 200,
      setHeader: (k, v) => { headers[k.toLowerCase()] = v; },
      end: (payload) => {
        const type = headers["content-type"] || "";
        let data = payload;
        if (type.startsWith("application/json")) data = JSON.parse(payload);
        resolve({ status: res.statusCode, headers, data });
      },
    };
    Promise.resolve(handler(req, res)).catch(reject);
  });
}

let cookie;
const PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  randomBytes(64),
]);

before(async () => {
  if (skip) return;
  pg = (await import("pg")).default;
  dbName = "autopost_api_" + randomBytes(5).toString("hex");
  await admin(`create database "${dbName}"`);
  process.env.DATABASE_URL = urlFor(dbName);
  process.env.AUTOPOST_PG_DRIVER = "pg";
  process.env.PANEL_PASSWORD = "correct horse";
  delete process.env.GITHUB_TOKEN;
  for (const name of ["session", "state", "settings", "book", "artwork", "media", "schedule", "run"]) {
    api[name] = (await import(`../api/${name}.js`)).default;
  }
});

after(async () => {
  if (skip) return;
  await (await import("../api/_db.js"))._reset();
  await admin(`drop database if exists "${dbName}"`).catch(() => {}); // plain drop: every connection is closed by now
});

// ------------------------------------------------------------------ auth

test("everything but sign-in needs a session", { skip }, async () => {
  for (const name of ["state", "settings", "book", "artwork", "media", "schedule", "run"]) {
    const r = await call(api[name], { method: name === "state" || name === "media" ? "GET" : "POST", body: {} });
    assert.equal(r.status, 401, `${name} answered ${r.status} without a session`);
  }
});

test("a wrong password is refused, the right one signs in", { skip }, async () => {
  const wrong = await call(api.session, { method: "POST", body: { password: "nope" } });
  assert.equal(wrong.status, 401);

  const ok = await call(api.session, { method: "POST", body: { password: "correct horse" } });
  assert.equal(ok.status, 200);
  const set = ok.headers["set-cookie"];
  assert.match(set, /HttpOnly/);
  assert.match(set, /SameSite=Strict/);
  cookie = set.split(";")[0];

  const who = await call(api.session, { cookie });
  assert.equal(who.data.authed, true);
  assert.equal(who.data.configured, true);
});

test("a tampered session cookie is refused", { skip }, async () => {
  const [name, value] = cookie.split("=");
  const [expires, sig] = value.split(".");
  const forged = `${name}=${Number(expires) + 999999}.${sig}`;
  const r = await call(api.state, { cookie: forged });
  assert.equal(r.status, 401);
});

// -------------------------------------------------------------- settings

test("the first request creates the tables on an empty database", { skip }, async () => {
  const r = await call(api.state, { cookie });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.settings, {});
  assert.deepEqual(r.data.books, []);
});

test("settings are validated, allow-listed and saved", { skip }, async () => {
  const badTime = await call(api.settings, { method: "POST", cookie,
    body: { settings: { schedule: { publish_at: "25:99" } } } });
  assert.equal(badTime.status, 400);
  assert.match(badTime.data.error, /real time/);

  const badZone = await call(api.settings, { method: "POST", cookie,
    body: { settings: { schedule: { timezone: "Mars/Olympus" } } } });
  assert.equal(badZone.status, 400);

  const ok = await call(api.settings, { method: "POST", cookie, body: { settings: {
    schedule: { auto: false, publish_at: "07:30", timezone: "Asia/Manila", days: ["mon", "fri"] },
    pricing: { default: "6.49" },
    secrets: { leak: "should never be stored" },
  } } });
  assert.equal(ok.status, 200);
  assert.equal(ok.data.settings.schedule.publish_at, "07:30");
  assert.equal(ok.data.settings.schedule.auto, false);
  assert.equal(ok.data.settings.secrets, undefined, "a non-allow-listed key was stored");

  const state = await call(api.state, { cookie });
  assert.equal(state.data.settings.pricing.default, "6.49");
});

// ----------------------------------------------------------------- books

test("a listing can be overridden and handed back to the generator", { skip }, async () => {
  const bad = await call(api.book, { method: "POST", cookie,
    body: { slug: "Not A Slug!", book: { name: "x" } } });
  assert.equal(bad.status, 400);

  const set = await call(api.book, { method: "POST", cookie, body: {
    slug: "ocean-buddies", book: { name: "Ocean Buddies", price: "4.99", tags: ["ocean", "kids"] } } });
  assert.equal(set.status, 200);
  assert.equal(set.data.book.override.price, "4.99");

  const cleared = await call(api.book, { method: "POST", cookie,
    body: { slug: "ocean-buddies", book: { price: "" } } });
  assert.equal(cleared.data.book.override.price, undefined, "an emptied field should stop overriding");
  assert.equal(cleared.data.book.override.name, "Ocean Buddies");

  const state = await call(api.state, { cookie });
  const book = state.data.books.find((b) => b.slug === "ocean-buddies");
  assert.equal(book.state, "prepared", "a listing with no PDF yet is 'prepared'");
});

// --------------------------------------------------------------- artwork

test("covers are type-checked by their bytes, stored, and served back", { skip }, async () => {
  const lying = await call(api.artwork, { method: "POST", cookie, body: {
    slug: "ocean-buddies", kind: "cover",
    data: "data:image/png;base64," + Buffer.from("<script>alert(1)</script>").toString("base64") } });
  assert.equal(lying.status, 400);
  assert.match(lying.data.error, /not a JPEG, PNG or WebP/);

  const up = await call(api.artwork, { method: "POST", cookie, body: {
    slug: "ocean-buddies", kind: "cover", data: "data:image/png;base64," + PNG.toString("base64") } });
  assert.equal(up.status, 200);

  const got = await call(api.media, { cookie, url: "/api/media?slug=ocean-buddies&kind=cover" });
  assert.equal(got.status, 200);
  assert.equal(got.headers["content-type"], "image/png");
  assert.ok(Buffer.from(got.data).equals(PNG), "the image came back different from what was uploaded");

  const missing = await call(api.media, { cookie, url: "/api/media?slug=ocean-buddies&kind=pin" });
  assert.equal(missing.status, 404);

  const removed = await call(api.artwork, { method: "DELETE", cookie, body: { slug: "ocean-buddies", kind: "cover" } });
  assert.equal(removed.status, 200);
  const gone = await call(api.media, { cookie, url: "/api/media?slug=ocean-buddies&kind=cover" });
  assert.equal(gone.status, 404);
});

// -------------------------------------------------------------- calendar

test("the calendar schedules, reschedules and cancels", { skip }, async () => {
  const past = await call(api.schedule, { method: "POST", cookie, body: {
    slug: "ocean-buddies", publish_at: new Date(Date.now() - 3600e3).toISOString() } });
  assert.equal(past.status, 400);
  assert.match(past.data.error, /already passed/);

  const when = new Date(Date.now() + 2 * 3600e3);
  const made = await call(api.schedule, { method: "POST", cookie,
    body: { slug: "ocean-buddies", publish_at: when.toISOString() } });
  assert.equal(made.status, 201);
  const id = made.data.post.id;

  const later = new Date(Date.now() + 5 * 3600e3);
  const moved = await call(api.schedule, { method: "POST", cookie,
    body: { slug: "ocean-buddies", publish_at: later.toISOString() } });
  assert.equal(moved.data.rescheduled, true, "a second post for the same book should move the first");
  assert.equal(String(moved.data.post.id), String(id));

  const list = await call(api.schedule, { cookie });
  const pending = list.data.posts.filter((p) => p.status === "scheduled");
  assert.equal(pending.length, 1, "rescheduling must not leave two posts");
  assert.equal(new Date(pending[0].publish_at).getTime(), later.getTime());

  const cancel = await call(api.schedule, { method: "DELETE", cookie, body: { id } });
  assert.equal(cancel.status, 200);
  const again = await call(api.schedule, { method: "DELETE", cookie, body: { id } });
  assert.equal(again.status, 409, "cancelling twice should say it's already gone");
});

test("a book that is already live can't be scheduled", { skip }, async () => {
  const pgc = new pg.Client({ connectionString: process.env.DATABASE_URL });
  await pgc.connect();
  await pgc.query(`insert into autopost_books (slug, record, status) values
    ('mermaid', '{"status":"published","title":"Mermaid"}', 'published')`);
  await pgc.end();

  const r = await call(api.schedule, { method: "POST", cookie, body: {
    slug: "mermaid", publish_at: new Date(Date.now() + 3600e3).toISOString() } });
  assert.equal(r.status, 409);
  assert.match(r.data.error, /already live/);

  const state = await call(api.state, { cookie });
  assert.equal(state.data.books.find((b) => b.slug === "mermaid").state, "live");
});

// ------------------------------------------------------------------ runs

test("starting a run without a GitHub token explains the fallback", { skip }, async () => {
  const r = await call(api.run, { method: "POST", cookie, body: { command: "run" } });
  assert.equal(r.status, 503);
  assert.match(r.data.error, /GITHUB_TOKEN/);
  assert.match(r.data.error, /Scheduling still works/);
});
