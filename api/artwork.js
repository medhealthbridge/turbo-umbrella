// Upload or remove a cover (or thumbnail / pin / social image) for one book.
// Uploaded artwork beats both a Drive image and the render from page 1.
import { guard, json, readBody, bad, SLUG } from "./_lib.js";
import { db } from "./_db.js";

const KINDS = ["cover", "thumbnail", "pin", "social"];
const TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_BYTES = 6 * 1024 * 1024;

export default guard(async (req, res) => {
  const body = await readBody(req);
  const slug = String(body.slug || "").trim();
  const kind = String(body.kind || "cover");
  if (!SLUG.test(slug)) throw bad("That is not a valid book slug.");
  if (!KINDS.includes(kind)) throw bad(`Kind must be one of ${KINDS.join(", ")}.`);

  if (req.method === "DELETE") {
    await db("delete from autopost_artwork where slug = $1 and kind = $2 and source = 'upload'", [slug, kind]);
    return json(res, 200, { ok: true, removed: true });
  }
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const match = String(body.data || "").match(/^data:([^;,]+);base64,(.+)$/);
  if (!match) throw bad("No image was received.");
  if (!TYPES.includes(match[1])) throw bad("Images must be JPEG, PNG or WebP.");
  const bytes = Buffer.from(match[2], "base64");
  if (!bytes.length) throw bad("That image was empty.");
  if (bytes.length > MAX_BYTES) {
    throw bad(`That image is ${(bytes.length / 1e6).toFixed(1)} MB; the limit is 6 MB.`);
  }
  // Check the bytes really are the image type claimed, not just the label.
  const magic = bytes.subarray(0, 12);
  const real =
    (magic[0] === 0xff && magic[1] === 0xd8 && "image/jpeg") ||
    (magic.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) && "image/png") ||
    (magic.subarray(0, 4).toString("latin1") === "RIFF" && magic.subarray(8, 12).toString("latin1") === "WEBP" && "image/webp");
  if (!real) throw bad("That file is not a JPEG, PNG or WebP image.");

  await db(
    `insert into autopost_artwork (slug, kind, source, mime, bytes, updated_at)
     values ($1, $2, 'upload', $3, decode($4, 'base64'), now())
     on conflict (slug, kind, source) do update
       set mime = excluded.mime, bytes = excluded.bytes, updated_at = now()`,
    [slug, kind, real, match[2]]
  );
  return json(res, 200, { ok: true, slug, kind, bytes: bytes.length });
});
