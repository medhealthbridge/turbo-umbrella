// Shared helpers for the control panel's API: the session, JSON responses,
// and the GitHub API (used only to start and list publish runs — everything
// the panel saves goes to the database, see _db.js).
//
// No dependencies on purpose: Node 20's built-in fetch and crypto are enough,
// so Vercel installs nothing and a cold start is a few milliseconds.

import crypto from "node:crypto";

export const REPO = process.env.GITHUB_REPO || "medhealthbridge/turbo-umbrella";
export const BRANCH = process.env.GITHUB_BRANCH || "main";
const TOKEN = process.env.GITHUB_TOKEN || "";
const PASSWORD = process.env.PANEL_PASSWORD || "";
// Rotating this invalidates every signed-in device.
const SECRET = process.env.PANEL_SECRET || PASSWORD;

const COOKIE = "autopost_session";
const SESSION_HOURS = 24 * 14;

// ------------------------------------------------------------------ session

function sign(value) {
  return crypto.createHmac("sha256", SECRET).update(value).digest("base64url");
}

export function issueToken() {
  const expires = Date.now() + SESSION_HOURS * 3600 * 1000;
  return `${expires}.${sign(String(expires))}`;
}

function tokenValid(token) {
  if (!token || !SECRET) return false;
  const [expires, signature] = String(token).split(".");
  if (!expires || !signature) return false;
  if (Number(expires) < Date.now()) return false;
  const expected = sign(expires);
  // Constant-time compare, so a wrong token leaks nothing through timing.
  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export function passwordMatches(candidate) {
  if (!PASSWORD) return false;
  const a = Buffer.from(String(candidate || ""));
  const b = Buffer.from(PASSWORD);
  // Compare hashes so differing lengths don't short-circuit.
  return crypto.timingSafeEqual(
    crypto.createHash("sha256").update(a).digest(),
    crypto.createHash("sha256").update(b).digest()
  );
}

export function sessionCookie(token) {
  const age = SESSION_HOURS * 3600;
  return `${COOKIE}=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=${age}`;
}

export function clearCookie() {
  return `${COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0`;
}

export function authed(req) {
  const header = req.headers.cookie || "";
  const match = header.match(new RegExp(`${COOKIE}=([^;]+)`));
  return tokenValid(match && match[1]);
}

/** Wrap a handler so it only runs for a signed-in browser. */
export function guard(handler) {
  return async (req, res) => {
    if (!PASSWORD) {
      return json(res, 503, {
        error:
          "PANEL_PASSWORD is not set on this deployment. Add it in Vercel → " +
          "Settings → Environment Variables, then redeploy.",
      });
    }
    if (!(process.env.DATABASE_URL || "").trim()) {
      return json(res, 503, {
        error:
          "DATABASE_URL is not set on this deployment. Add your Neon connection " +
          "string in Vercel → Settings → Environment Variables, then redeploy.",
      });
    }
    if (!authed(req)) return json(res, 401, { error: "Not signed in." });
    try {
      return await handler(req, res);
    } catch (err) {
      const status = Number.isInteger(err.status) && err.status >= 400 && err.status < 600 ? err.status : 500;
      return json(res, status, { error: String(err && err.message ? err.message : err) });
    }
  };
}

/** An error that becomes a 4xx with a readable message. */
export function bad(message, status = 400) {
  const err = new Error(message);
  err.status = status;
  return err;
}

export const SLUG = /^[a-z0-9][a-z0-9-]{0,119}$/;

export function githubConfigured() {
  return Boolean(TOKEN);
}

// ------------------------------------------------------------------- output

export function json(res, status, body) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(body));
}

export async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new Error("The request body was not valid JSON.");
  }
}

// --------------------------------------------------------------- GitHub API

async function gh(path, options = {}) {
  const resp = await fetch(`https://api.github.com${path}`, {
    ...options,
    headers: {
      authorization: `Bearer ${TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "content-type": "application/json",
      "user-agent": "autopost-panel",
      ...(options.headers || {}),
    },
  });
  if (resp.status === 204) return null;
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!resp.ok) {
    const detail = (data && data.message) || resp.statusText;
    const hint =
      resp.status === 401 ? " — the GITHUB_TOKEN in Vercel is invalid or has expired." :
      resp.status === 403 || resp.status === 404 ? " — check the token can access this repository with Actions: read and write." :
      "";
    // Always a 502 to the browser. Passing GitHub's own status through would
    // turn an expired token's 401 into "you are signed out" in the panel.
    const err = new Error(`GitHub ${resp.status}: ${detail}${hint}`);
    err.status = 502;
    throw err;
  }
  return data;
}

export async function dispatchWorkflow(workflow, inputs) {
  if (!TOKEN) {
    throw bad(
      "GITHUB_TOKEN is not set on this deployment, so the panel can't start a run. " +
        "Scheduling still works: the Neon scheduler and the hourly run will pick it up.",
      503
    );
  }
  return gh(`/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: BRANCH, inputs }),
  });
}

export async function listRuns(limit = 8) {
  if (!TOKEN) return [];
  const data = await gh(
    `/repos/${REPO}/actions/runs?per_page=${limit}&branch=${encodeURIComponent(BRANCH)}`
  );
  return (data.workflow_runs || []).map((run) => ({
    id: run.id,
    name: run.name,
    status: run.status,
    conclusion: run.conclusion,
    event: run.event,
    created_at: run.created_at,
    url: run.html_url,
  }));
}
