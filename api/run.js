// Trigger the publish workflow.
import { guard, json, readBody, dispatchWorkflow, listRuns } from "./_lib.js";

const COMMANDS = ["run", "scan", "plan", "dashboard"];

export default guard(async (req, res) => {
  if (req.method !== "POST") return json(res, 405, { error: "Method not allowed." });

  const body = await readBody(req);
  const command = String(body.command || "run");
  if (!COMMANDS.includes(command)) {
    return json(res, 400, { error: `Command must be one of ${COMMANDS.join(", ")}.` });
  }
  const slug = String(body.slug || "").trim();
  if (slug && !/^[a-z0-9][a-z0-9-]*$/.test(slug)) {
    return json(res, 400, { error: "That is not a valid book slug." });
  }

  await dispatchWorkflow("publish.yml", {
    command,
    slug,
    force: String(Boolean(body.force)),
    dry_run: String(Boolean(body.dry_run)),
  });

  // GitHub takes a moment to register the run, so the panel polls for it.
  return json(res, 202, { ok: true, command, runs: await listRuns(5).catch(() => []) });
});
