// Shared helpers for the control panel's API.
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
    if (!TOKEN) {
      return json(res, 503, {
        error:
          "GITHUB_TOKEN is not set on this deployment. The panel can't read or " +
          "write the repository without it.",
      });
    }
    if (!authed(req)) return json(res, 401, { error: "Not signed in." });
    try {
      return await handler(req, res);
    } catch (err) {
      return json(res, 500, { error: String(err && err.message ? err.message : err) });
    }
  };
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
    const err = new Error(`GitHub ${resp.status}: ${detail}`);
    err.status = resp.status;
    throw err;
  }
  return data;
}

/** A file's decoded text and blob sha, or null when it does not exist. */
export async function getFile(path) {
  try {
    const data = await gh(
      `/repos/${REPO}/contents/${encodeURI(path)}?ref=${encodeURIComponent(BRANCH)}`
    );
    return {
      sha: data.sha,
      text: Buffer.from(data.content || "", "base64").toString("utf8"),
    };
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
}

export async function getJson(path, fallback) {
  const file = await getFile(path);
  if (!file) return fallback;
  try {
    return JSON.parse(file.text);
  } catch {
    return fallback;
  }
}

/** Create or replace a file. `content` is a string or a Buffer. */
export async function putFile(path, content, message) {
  const existing = await getFile(path);
  const body = {
    message,
    branch: BRANCH,
    content: Buffer.from(content).toString("base64"),
  };
  if (existing) body.sha = existing.sha;
  return gh(`/repos/${REPO}/contents/${encodeURI(path)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function dispatchWorkflow(workflow, inputs) {
  return gh(`/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: BRANCH, inputs }),
  });
}

export async function listRuns(limit = 8) {
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
