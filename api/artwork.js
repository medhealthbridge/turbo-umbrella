// Upload a cover for one book. Committed to assets/artwork/, which the
// publisher checks before rendering one from the PDF.
import { guard, json, readBody, putFile } from "./_lib.js";

const KINDS = ["cover", "thumbnail", "pin", "social"];
const TYPES = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" };
const MAX_BYTES = 6 * 1024 * 1024;

export default guard(async (req, res) => {
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const body = await readBody(req);
  const slug = String(body.slug || "").trim();
  const kind = String(body.kind || "cover");

  if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) {
    return json(res, 400, { error: "That is not a valid book slug." });
  }
  if (!KINDS.includes(kind)) {
    return json(res, 400, { error: `Kind must be one of ${KINDS.join(", ")}.` });
  }

  // A data URL from the browser's FileReader.
  const match = String(body.data || "").match(/^data:([^;,]+);base64,(.+)$/);
  if (!match) return json(res, 400, { error: "No image was received." });

  const extension = TYPES[match[1]];
  if (!extension) {
    return json(res, 400, { error: "Images must be JPEG, PNG or WebP." });
  }
  const bytes = Buffer.from(match[2], "base64");
  if (!bytes.length) return json(res, 400, { error: "That image was empty." });
  if (bytes.length > MAX_BYTES) {
    return json(res, 400, { error: `That image is ${(bytes.length / 1e6).toFixed(1)} MB; the limit is 6 MB.` });
  }

  const path = `assets/artwork/${slug}-${kind}.${extension}`;
  await putFile(path, bytes, `chore: upload ${kind} for ${slug} from the control panel [skip ci]`);
  return json(res, 200, { ok: true, path, bytes: bytes.length });
});
