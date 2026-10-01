// Edit one book's listing: title, price, description, tags, summary.
// Writes into books.json, which the publisher treats as per-book overrides.
import { guard, json, readBody, getJson, putFile } from "./_lib.js";

const FIELDS = ["name", "price", "description", "summary", "tags", "link"];

export default guard(async (req, res) => {
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const body = await readBody(req);
  const slug = String(body.slug || "").trim();
  if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) {
    return json(res, 400, { error: "That is not a valid book slug." });
  }

  const incoming = body.book || {};
  if (incoming.price !== undefined && !/^\d+(\.\d{1,2})?$/.test(String(incoming.price))) {
    return json(res, 400, { error: "Price must be a number like 5.99." });
  }
  if (incoming.tags !== undefined && !Array.isArray(incoming.tags)) {
    return json(res, 400, { error: "Tags must be a list." });
  }

  const books = await getJson("books.json", []);
  if (!Array.isArray(books)) return json(res, 500, { error: "books.json is not a list." });

  const entry = books.find((b) => b && b.slug === slug) || { slug };
  let changed = false;
  for (const field of FIELDS) {
    if (incoming[field] === undefined) continue;
    const value = typeof incoming[field] === "string" ? incoming[field].trim() : incoming[field];
    // An emptied field means "stop overriding this" — drop it so the
    // generator takes over again, rather than publishing a blank.
    if (value === "" || (Array.isArray(value) && !value.length)) {
      if (entry[field] !== undefined) { delete entry[field]; changed = true; }
    } else if (JSON.stringify(entry[field]) !== JSON.stringify(value)) {
      entry[field] = value;
      changed = true;
    }
  }

  if (Object.keys(entry).length === 1 && entry.slug) {
    // Nothing left to override: remove the entry entirely.
    const index = books.findIndex((b) => b && b.slug === slug);
    if (index >= 0) { books.splice(index, 1); changed = true; }
  } else if (!books.includes(entry)) {
    books.push(entry);
    changed = true;
  }

  if (!changed) return json(res, 200, { ok: true, unchanged: true, book: entry });

  await putFile(
    "books.json",
    JSON.stringify(books, null, 2) + "\n",
    `chore: update ${slug} listing from the control panel [skip ci]`
  );
  return json(res, 200, { ok: true, book: entry });
});
