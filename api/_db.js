// Database access for the panel's API.
//
// Production talks to Neon over HTTPS with @neondatabase/serverless — no
// connection pool to manage, which suits short-lived serverless functions.
// The test suite swaps in node-postgres against a local Postgres
// (AUTOPOST_PG_DRIVER=pg), so every query here is exercised for real.
//
// The schema lives in db/schema.sql, shared with the Python publisher. It is
// applied on the first query of each cold start; every statement is
// idempotent, so a brand-new database works with no setup step.

import { readFileSync } from "node:fs";

let query = null;
let migrated = null;
let pool = null;

export function configured() {
  return Boolean((process.env.DATABASE_URL || "").trim());
}

async function connect() {
  if (query) return query;
  const url = (process.env.DATABASE_URL || "").trim();
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set on this deployment. Add your Neon connection string in " +
        "Vercel → Settings → Environment Variables, then redeploy."
    );
  }
  if (process.env.AUTOPOST_PG_DRIVER === "pg") {
    const pg = (await import("pg")).default;
    pool = new pg.Pool({ connectionString: url, max: 3 });
    // An idle client dropped by the server must not crash the process.
    pool.on("error", () => {});
    query = async (text, params = []) => (await pool.query(text, params)).rows;
  } else {
    const { neon } = await import("@neondatabase/serverless");
    const sql = neon(url);
    query = (text, params = []) => sql.query(text, params);
  }
  return query;
}

export function schemaStatements() {
  const text = readFileSync(new URL("../db/schema.sql", import.meta.url), "utf8");
  return text
    .split("\n-- ;;\n")
    .map((chunk) =>
      chunk
        .split("\n")
        .filter((line) => !line.trim().startsWith("--"))
        .join("\n")
        .trim()
    )
    .filter(Boolean);
}

async function migrate(q) {
  if (!migrated) {
    migrated = (async () => {
      for (const statement of schemaStatements()) await q(statement);
    })().catch((err) => {
      migrated = null; // let the next request try again
      throw err;
    });
  }
  return migrated;
}

/** Run one parameterised statement and return its rows. */
export async function db(text, params = []) {
  const q = await connect();
  await migrate(q);
  return q(text, params);
}

/** Test hook: close the connection so the next call reconnects. */
export async function _reset() {
  if (pool) await pool.end().catch(() => {});
  pool = null;
  query = null;
  migrated = null;
}
