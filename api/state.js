// Everything the panel needs to draw itself, in one request.
import { guard, json, listRuns, githubConfigured, REPO, BRANCH } from "./_lib.js";
import { db } from "./_db.js";

export default guard(async (req, res) => {
  const [settingsRows, books, seen, schedule, artwork, runs, ghRuns] = await Promise.all([
    db("select data, updated_at from autopost_settings where id = 1"),
    db("select slug, override, record, status, published_at, updated_at from autopost_books order by slug"),
    db("select file_id, name, slug, size, first_seen from autopost_seen order by first_seen"),
    db(
      `select id, slug, publish_at, status, attempts, last_error, dispatched_at, finished_at, created_at
         from autopost_schedule
        where status in ('scheduled', 'dispatched') or finished_at > now() - interval '14 days'
        order by publish_at`
    ),
    db("select slug, kind, source, updated_at from autopost_artwork order by slug, kind"),
    db("select id, started_at, finished_at, summary from autopost_runs order by id desc limit 10"),
    listRuns(8).catch(() => []),
  ]);

  // One list of books, whatever state each is in:
  //   live      published
  //   queued    its PDF is in Drive, waiting for a slot
  //   prepared  you wrote its listing, the PDF isn't in Drive yet
  //   failed / partial / pending — needs a look
  const inDrive = new Map();
  for (const file of seen) inDrive.set(file.slug, file);
  const bySlug = new Map();
  for (const row of books) {
    const record = row.record || {};
    const hasRecord = Object.keys(record).length > 0;
    bySlug.set(row.slug, {
      slug: row.slug,
      override: row.override || {},
      record,
      state: hasRecord && record.status ? (record.status === "published" ? "live" : record.status)
           : inDrive.has(row.slug) ? "queued" : "prepared",
      published_at: row.published_at,
    });
  }
  for (const [slug, file] of inDrive) {
    if (!bySlug.has(slug)) bySlug.set(slug, { slug, override: {}, record: {}, state: "queued" });
    bySlug.get(slug).file = { name: file.name, size: file.size, first_seen: file.first_seen };
  }
  const art = {};
  for (const a of artwork) {
    (art[a.slug] ||= {})[`${a.kind}:${a.source}`] = a.updated_at;
  }
  for (const book of bySlug.values()) book.artwork = art[book.slug] || {};

  return json(res, 200, {
    repo: REPO,
    branch: BRANCH,
    github: githubConfigured(),
    settings: (settingsRows[0] && settingsRows[0].data) || {},
    books: [...bySlug.values()],
    schedule,
    runs: ghRuns,
    history: runs,
  });
});
