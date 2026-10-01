// Serve a stored image to the signed-in panel: a cover you uploaded, or a
// cover / thumbnail / pin / social image the publisher generated.
import { guard, json, bad, SLUG } from "./_lib.js";
import { db } from "./_db.js";

export default guard(async (req, res) => {
  if (req.method !== "GET") return json(res, 405, { error: "Method not allowed." });
  const url = new URL(req.url, "http://x");
  const slug = url.searchParams.get("slug") || "";
  const kind = url.searchParams.get("kind") || "cover";
  const source = url.searchParams.get("source") || "";
  if (!SLUG.test(slug)) throw bad("That is not a valid book slug.");
  if (!["cover", "thumbnail", "pin", "social"].includes(kind)) throw bad("Unknown image kind.");

  // Without an explicit source, prefer what you uploaded over what was rendered.
  const rows = await db(
    `select mime, encode(bytes, 'base64') as b64 from autopost_artwork
      where slug = $1 and kind = $2 and ($3 = '' or source = $3)
      order by case source when 'upload' then 0 else 1 end limit 1`,
    [slug, kind, source]
  );
  if (!rows.length) return json(res, 404, { error: "No such image." });

  const bytes = Buffer.from(rows[0].b64, "base64");
  res.statusCode = 200;
  res.setHeader("content-type", rows[0].mime);
  res.setHeader("cache-control", "private, max-age=60");
  if (url.searchParams.get("download")) {
    res.setHeader("content-disposition", `attachment; filename="${slug}-${kind}.${rows[0].mime.split("/")[1].replace("jpeg", "jpg")}"`);
  }
  res.end(bytes);
});
