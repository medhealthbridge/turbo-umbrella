// Everything the panel needs to draw itself, in one request.
import { guard, json, getJson, getFile, listRuns, REPO, BRANCH } from "./_lib.js";

export default guard(async (req, res) => {
  const [settings, books, published, seen, runs, configFile] = await Promise.all([
    getJson("state/settings.json", {}),
    getJson("books.json", []),
    getJson("state/published.json", { published: [] }),
    getJson("state/seen.json", { files: {} }),
    listRuns(8).catch(() => []),
    getFile("config.yml"),
  ]);

  const live = (published.published || []).filter((e) => e.status === "published");
  const liveSlugs = new Set(live.map((e) => e.slug));
  const queue = Object.values(seen.files || {})
    .filter((f) => f.slug && !liveSlugs.has(f.slug))
    .map((f) => ({ slug: f.slug, name: f.name, first_seen: f.first_seen }));

  return json(res, 200, {
    repo: REPO,
    branch: BRANCH,
    settings,
    books,
    published: published.published || [],
    live: live.length,
    queue,
    runs,
    // The raw YAML, so the panel can show the defaults a setting falls back to.
    config_yaml: configFile ? configFile.text : "",
  });
});
