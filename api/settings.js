// Save the panel's settings. Written to state/settings.json, which the
// publisher merges over config.yml — so config.yml keeps its comments.
import { guard, json, readBody, putFile } from "./_lib.js";

// Only these may be set from the panel. Anything else is dropped rather than
// written, so a bug or a crafted request cannot reach arbitrary config.
const ALLOWED = {
  schedule: ["timezone", "publish_at", "window_minutes", "days", "max_per_run", "min_hours_between", "settle_minutes"],
  pricing: ["default", "rules"],
  marketing: ["audience", "brand_promise", "guarantees", "base_tags", "max_tags", "receipt", "refund_period", "title_suffix"],
  source: ["folder_id", "recursive", "ignore_patterns", "min_size_mb"],
  images: ["enabled", "quality"],
  dashboard: ["title", "subtitle", "currency"],
};

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function clean(incoming) {
  const out = {};
  for (const [section, fields] of Object.entries(ALLOWED)) {
    const given = incoming[section];
    if (!given || typeof given !== "object") continue;
    const kept = {};
    for (const field of fields) {
      if (given[field] !== undefined && given[field] !== null) kept[field] = given[field];
    }
    if (Object.keys(kept).length) out[section] = kept;
  }

  // Sections the panel toggles wholesale.
  for (const key of ["platforms", "social"]) {
    if (incoming[key] && typeof incoming[key] === "object") out[key] = incoming[key];
  }
  return out;
}

function validate(settings) {
  const problems = [];
  const s = settings.schedule || {};

  if (s.publish_at !== undefined && !/^\d{1,2}:\d{2}$/.test(String(s.publish_at))) {
    problems.push("Posting time must look like 09:15.");
  } else if (s.publish_at !== undefined) {
    const [h, m] = String(s.publish_at).split(":").map(Number);
    if (h > 23 || m > 59) problems.push("Posting time must be a real time of day.");
  }
  if (s.timezone !== undefined) {
    try {
      new Intl.DateTimeFormat("en", { timeZone: String(s.timezone) });
    } catch {
      problems.push(`"${s.timezone}" is not a time zone name. Try Asia/Manila.`);
    }
  }
  if (s.days !== undefined) {
    if (!Array.isArray(s.days) || !s.days.length) problems.push("Pick at least one posting day.");
    else if (s.days.some((d) => !DAYS.includes(String(d)))) problems.push("Posting days must be mon…sun.");
  }
  for (const [field, label, min, max] of [
    ["window_minutes", "Window", 5, 1440],
    ["max_per_run", "Books per run", 1, 20],
    ["min_hours_between", "Minimum gap", 0, 720],
    ["settle_minutes", "Settle delay", 0, 1440],
  ]) {
    if (s[field] === undefined) continue;
    const value = Number(s[field]);
    if (!Number.isFinite(value) || value < min || value > max) {
      problems.push(`${label} must be a number between ${min} and ${max}.`);
    }
  }
  const price = settings.pricing && settings.pricing.default;
  if (price !== undefined && !/^\d+(\.\d{1,2})?$/.test(String(price))) {
    problems.push("Default price must be a number like 5.99.");
  }
  return problems;
}

export default guard(async (req, res) => {
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const body = await readBody(req);
  const settings = clean(body.settings || {});
  const problems = validate(settings);
  if (problems.length) return json(res, 400, { error: problems.join(" ") });

  settings.updated_at = new Date().toISOString();
  settings.updated_by = "control panel";

  await putFile(
    "state/settings.json",
    JSON.stringify(settings, null, 2) + "\n",
    "chore: update settings from the control panel [skip ci]"
  );
  return json(res, 200, { ok: true, settings });
});
