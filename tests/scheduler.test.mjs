// The Neon scheduler's tick, against a real Postgres.
//
//   AUTOPOST_TEST_DATABASE_URL=postgresql://... npm test

import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { tick, dispatchGithub, MAX_ATTEMPTS } from "../neon/scheduler-core.js";

const ADMIN = (process.env.AUTOPOST_TEST_DATABASE_URL || "").trim();
const skip = ADMIN ? false : "set AUTOPOST_TEST_DATABASE_URL to run the scheduler tests";

let pg, pool, dbName;
const urlFor = (db) => ADMIN.replace(/\/[^/?]*(\?|$)/, `/${db}$1`);
const query = async (text, params = []) => (await pool.query(text, params)).rows;

async function admin(sql) {
  const client = new pg.Client({ connectionString: ADMIN });
  await client.connect();
  try { return await client.query(sql); } finally { await client.end(); }
}

before(async () => {
  if (skip) return;
  pg = (await import("pg")).default;
  dbName = "autopost_sched_" + randomBytes(5).toString("hex");
  await admin(`create database "${dbName}"`);
  pool = new pg.Pool({ connectionString: urlFor(dbName), max: 3 });
  pool.on("error", () => {});
  const { schemaStatements } = await import("../api/_db.js");
  for (const statement of schemaStatements()) await query(statement);
});

after(async () => {
  if (skip) return;
  await pool.end();
  await admin(`drop database if exists "${dbName}"`).catch(() => {});
});

beforeEach(async () => {
  if (skip) return;
  await query("delete from autopost_schedule");
});

const add = (slug, minutes, extra = "") =>
  query(`insert into autopost_schedule (slug, publish_at) values ($1, now() + make_interval(mins => $2)) returning id`,
        [slug, minutes]).then((r) => r[0].id);
const status = (id) => query("select status, attempts, last_error from autopost_schedule where id = $1", [id]).then((r) => r[0]);

test("an idle tick does nothing and starts no run", { skip }, async () => {
  await add("later", 60);
  let runs = 0;
  const r = await tick({ query, dispatch: async () => { runs++; } });
  assert.equal(r.claimed.length, 0);
  assert.equal(runs, 0);
});

test("due posts are claimed together and cost one run", { skip }, async () => {
  const a = await add("book-a", -10);
  const b = await add("book-b", -1);
  const c = await add("book-c", 30);
  let runs = 0;
  const r = await tick({ query, dispatch: async () => { runs++; } });
  assert.equal(runs, 1, "three due posts should share one publish run");
  assert.deepEqual(r.claimed.map((x) => x.slug).sort(), ["book-a", "book-b"]);
  assert.equal((await status(a)).status, "dispatched");
  assert.equal((await status(b)).attempts, 1);
  assert.equal((await status(c)).status, "scheduled", "a future post must not be touched");

  const again = await tick({ query, dispatch: async () => { runs++; } });
  assert.equal(again.claimed.length, 0, "a fresh dispatch must not be re-sent on the next tick");
  assert.equal(runs, 1);
});

test("a run that never reports back is retried, then given up on", { skip }, async () => {
  const id = await add("stuck", -60);
  await tick({ query, dispatch: async () => {} });

  for (let attempt = 2; attempt <= MAX_ATTEMPTS; attempt++) {
    await query("update autopost_schedule set dispatched_at = now() - interval '31 minutes' where id = $1", [id]);
    const r = await tick({ query, dispatch: async () => {} });
    assert.equal(r.claimed.length, 1, `attempt ${attempt} should re-dispatch`);
    assert.equal((await status(id)).attempts, attempt);
  }

  await query("update autopost_schedule set dispatched_at = now() - interval '31 minutes', last_error = 'waiting for the PDF' where id = $1", [id]);
  const last = await tick({ query, dispatch: async () => { throw new Error("must not dispatch"); } });
  assert.equal(last.gaveUp.length, 1);
  const final = await status(id);
  assert.equal(final.status, "failed");
  assert.match(final.last_error, /Gave up after 3 publish runs: waiting for the PDF/);
});

test("if GitHub refuses, the posts go back on the calendar with the reason", { skip }, async () => {
  const id = await add("refused", -2);
  const r = await tick({ query, dispatch: async () => { throw new Error("GitHub 401: Bad credentials"); } });
  assert.equal(r.dispatched, false);
  const row = await status(id);
  assert.equal(row.status, "scheduled", "a refused dispatch must be retried next tick");
  assert.match(row.last_error, /Couldn't start the publish run: GitHub 401/);

  let runs = 0;
  await tick({ query, dispatch: async () => { runs++; } });
  assert.equal(runs, 1, "the next tick should try again");
});

test("cancelled and published posts are never dispatched", { skip }, async () => {
  const a = await add("gone", -5);
  const b = await add("done", -5);
  await query("update autopost_schedule set status = 'cancelled' where id = $1", [a]);
  await query("update autopost_schedule set status = 'published' where id = $1", [b]);
  const r = await tick({ query, dispatch: async () => { throw new Error("must not dispatch"); } });
  assert.equal(r.claimed.length, 0);
});

test("the GitHub call is shaped right and errors are readable", async () => {
  let seen;
  await dispatchGithub({
    token: "t0k", repo: "me/shop", branch: "main",
    fetchImpl: async (url, init) => { seen = { url, init }; return { status: 204 }; },
  });
  assert.equal(seen.url, "https://api.github.com/repos/me/shop/actions/workflows/publish.yml/dispatches");
  assert.equal(seen.init.headers.authorization, "Bearer t0k");
  assert.deepEqual(JSON.parse(seen.init.body), { ref: "main", inputs: { command: "run" } });

  await assert.rejects(
    dispatchGithub({ token: "x", repo: "me/shop", branch: "main",
      fetchImpl: async () => ({ status: 404, text: async () => "Not Found" }) }),
    /GitHub 404: Not Found/
  );
  await assert.rejects(dispatchGithub({ token: "", repo: "a/b", branch: "main" }), /GITHUB_TOKEN is not set/);
});
