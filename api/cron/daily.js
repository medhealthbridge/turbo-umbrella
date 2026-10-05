// Vercel Cron target: build the daily Gumroad report and save it.
//
// Scheduled in vercel.json. Vercel calls it with
//   Authorization: Bearer $CRON_SECRET
// so anyone else hitting the URL gets a 401. With CRON_SECRET unset it refuses
// to run at all rather than running open.

import crypto from "node:crypto";
import { json } from "../_lib.js";
import { db, configured } from "../_db.js";
import { buildReport } from "../_daily.js";

function secretOk(req) {
  const secret = (process.env.CRON_SECRET || "").trim();
  const sent = String(req.headers.authorization || "");
  const hash = (v) => crypto.createHash("sha256").update(v).digest();
  return crypto.timingSafeEqual(hash(sent), hash(`Bearer ${secret}`));
}

export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "POST") return json(res, 405, { error: "Method not allowed." });
  const secret = (process.env.CRON_SECRET || "").trim();
  if (secret.length < 16) {
    return json(res, 503, { error: "CRON_SECRET is not set (use at least 16 random characters). The job will not run without it." });
  }
  if (!secretOk(req)) return json(res, 401, { error: "Unauthorized." });
  if (!configured()) {
    return json(res, 503, { error: "DATABASE_URL is not set, so the report cannot be saved." });
  }

  let report;
  try {
    report = await buildReport();
  } catch (err) {
    return json(res, 502, { error: String(err.message || err) });
  }
  try {
    // One row per day in the shop's time zone; a repeat run replaces it.
    await db(
      `insert into autopost_daily_reports (day, report) values ($1, $2::jsonb)
       on conflict (day) do update set report = excluded.report, created_at = now()`,
      [report.day, JSON.stringify(report)]
    );
  } catch (err) {
    return json(res, 500, { error: `Report built but not saved: ${String(err.message || err)}`, report });
  }
  return json(res, 200, { ok: true, saved: true, day: report.day, drafts_source: report.drafts_source, report });
}
