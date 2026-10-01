// Edit one book's listing. Stored in autopost_books.override; the publisher
// treats it exactly like a books.json entry used to be treated.
import { guard, json, readBody, bad, SLUG } from "./_lib.js";
import { db } from "./_db.js";

const FIELDS = ["name", "price", "description", "summary", "tags"];

export default guard(async (req, res) => {
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });
  const body = await readBody(req);
  const slug = String(body.slug || "").trim();
  if (!SLUG.test(slug)) throw bad("That is not a valid book slug.");

  const incoming = body.book || {};
  if (incoming.price !== undefined && incoming.price !== "" && !/^\d+(\.\d{1,2})?$/.test(String(incoming.price))) {
    throw bad("Price must be a number like 5.99.");
  }
  if (incoming.tags !== undefined && !Array.isArray(incoming.tags)) throw bad("Tags must be a list.");

  const rows = await db("select override from autopost_books where slug = $1", [slug]);
  const override = { ...((rows[0] && rows[0].override) || {}) };
  for (const field of FIELDS) {
    if (incoming[field] === undefined) continue;
    const value = typeof incoming[field] === "string" ? incoming[field].trim() : incoming[field];
    // An emptied field means "stop overriding this": the generator takes it back.
    if (value === "" || (Array.isArray(value) && !value.length)) delete override[field];
    else override[field] = value;
  }

  const saved = await db(
    `insert into autopost_books (slug, override, updated_at) values ($1, $2::jsonb, now())
     on conflict (slug) do update set override = excluded.override, updated_at = now()
     returning slug, override`,
    [slug, JSON.stringify(override)]
  );
  return json(res, 200, { ok: true, book: saved[0] });
});
