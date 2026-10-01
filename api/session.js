// Sign in and out. The only endpoint that does not require a session.
import { json, readBody, passwordMatches, issueToken, sessionCookie, clearCookie, authed } from "./_lib.js";
import { configured as dbConfigured } from "./_db.js";

export default async function handler(req, res) {
  if (req.method === "GET") {
    const missing = [];
    if (!process.env.PANEL_PASSWORD) missing.push("PANEL_PASSWORD");
    if (!dbConfigured()) missing.push("DATABASE_URL");
    return json(res, 200, {
      authed: authed(req),
      configured: missing.length === 0,
      missing,
      github: Boolean(process.env.GITHUB_TOKEN),
    });
  }
  if (req.method === "DELETE") {
    res.setHeader("set-cookie", clearCookie());
    return json(res, 200, { authed: false });
  }
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  if (!process.env.PANEL_PASSWORD) {
    return json(res, 503, {
      error: "PANEL_PASSWORD is not set on this deployment. Add it in Vercel → Settings → Environment Variables, then redeploy.",
    });
  }

  const body = await readBody(req);
  // A deliberate pause: makes guessing the password over the network slow.
  await new Promise((r) => setTimeout(r, 400));
  if (!passwordMatches(body.password)) {
    return json(res, 401, { error: "Wrong password." });
  }
  res.setHeader("set-cookie", sessionCookie(issueToken()));
  return json(res, 200, { authed: true });
}
