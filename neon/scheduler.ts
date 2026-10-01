// Neon Function: the calendar's clock.
//
// A schedule trigger (see neon.ts) POSTs here every five minutes. Anything on
// the calendar that has come due gets one GitHub publish run started for it.
// The heavy lifting — Drive, the PDF, cover art, Gumroad — stays in GitHub
// Actions; this function only decides *when*.
//
// The URL is public, as Neon requires for triggers, so the handler only acts
// when Neon's trigger header is present. Neon strips any X-Neon-* header a
// client sets, so that header can't be forged from outside.

import { neon } from "@neondatabase/serverless";
import { tick, dispatchGithub } from "./scheduler-core.js";

export default async function scheduler(req: Request): Promise<Response> {
  if (req.method === "GET") {
    return Response.json({ ok: true, service: "autopost scheduler" });
  }
  if (req.method !== "POST" || !req.headers.get("x-neon-trigger-invocation-id")) {
    return Response.json({ error: "not a trigger call" }, { status: 403 });
  }

  const sql = neon(process.env.DATABASE_URL!);
  const result = await tick({
    query: (text: string, params: unknown[] = []) => sql.query(text, params),
    dispatch: () =>
      dispatchGithub({
        token: process.env.GITHUB_TOKEN ?? "",
        repo: process.env.GITHUB_REPO || "medhealthbridge/turbo-umbrella",
        branch: process.env.GITHUB_BRANCH || "main",
      }),
  });

  const slugs = result.claimed.map((row: { slug: string }) => row.slug).join(", ");
  if (result.gaveUp.length) {
    console.log(`gave up on: ${result.gaveUp.map((row: { slug: string }) => row.slug).join(", ")}`);
  }
  if (result.error) console.log(`dispatch failed for ${slugs}: ${result.error}`);
  else if (result.claimed.length) console.log(`publish run started for: ${slugs}`);

  return Response.json({
    claimed: result.claimed.length,
    dispatched: result.dispatched,
    gave_up: result.gaveUp.length,
    error: result.error ?? null,
  });
}
