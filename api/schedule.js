// The calendar: list, add and cancel scheduled posts.
import { guard, json, readBody, bad, SLUG } from "./_lib.js";
import { db } from "./_db.js";

export default guard(async (req, res) => {
  if (req.method === "GET") {
    const rows = await db(
      `select id, slug, publish_at, status, attempts, last_error, finished_at
         from autopost_schedule
        where status in ('scheduled', 'dispatched') or finished_at > now() - interval '14 days'
        order by publish_at`
    );
    return json(res, 200, { posts: rows });
  }

  const body = await readBody(req);

  if (req.method === "DELETE") {
    const id = Number(body.id);
    if (!Number.isInteger(id) || id < 1) throw bad("Which post?");
    const rows = await db(
      `update autopost_schedule set status = 'cancelled', finished_at = now()
        where id = $1 and status in ('scheduled', 'dispatched') returning id`,
      [id]
    );
    if (!rows.length) throw bad("That post has already gone out or was cancelled.", 409);
    return json(res, 200, { ok: true, cancelled: id });
  }

  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const slug = String(body.slug || "").trim();
  if (!SLUG.test(slug)) throw bad("Pick a book.");
  const when = new Date(body.publish_at);
  if (Number.isNaN(when.getTime())) throw bad("Pick a date and time.");
  if (when.getTime() < Date.now() - 5 * 60 * 1000) throw bad("That time has already passed.");
  if (when.getTime() > Date.now() + 366 * 24 * 3600 * 1000) throw bad("Pick a time within the next year.");

  const live = await db("select 1 from autopost_books where slug = $1 and status = 'published'", [slug]);
  if (live.length) throw bad("That book is already live.", 409);

  // One pending post per book: rescheduling moves it rather than doubling up.
  const moved = await db(
    `update autopost_schedule set publish_at = $2, last_error = null, attempts = 0, status = 'scheduled'
      where slug = $1 and status in ('scheduled', 'dispatched') returning id, slug, publish_at, status`,
    [slug, when.toISOString()]
  );
  if (moved.length) return json(res, 200, { ok: true, post: moved[0], rescheduled: true });

  const rows = await db(
    "insert into autopost_schedule (slug, publish_at) values ($1, $2) returning id, slug, publish_at, status",
    [slug, when.toISOString()]
  );
  return json(res, 201, { ok: true, post: rows[0] });
});
