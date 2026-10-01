// The scheduler's logic, kept free of any runtime so it can be tested
// against a real Postgres. neon/scheduler.ts wires it to Neon.
//
// Every five minutes:
//   1. Give up on a post whose publish run was started three times without
//      it ever going live, and say why.
//   2. Claim every post that is due (or whose run was started over half an
//      hour ago and never reported back), marking it `dispatched`.
//   3. If anything was claimed, start ONE publish run. That run publishes
//      every due post, so a burst of posts costs one run, not one each.
//   4. If GitHub refused, put the posts back so the next tick retries.
//
// `query(text, params)` returns rows; `dispatch()` starts the publish run.

export const STALE_MINUTES = 30;
export const MAX_ATTEMPTS = 3;

export async function tick({ query, dispatch }) {
  const gaveUp = await query(
    `update autopost_schedule
        set status = 'failed', finished_at = now(),
            last_error = 'Gave up after ' || attempts || ' publish runs: ' ||
                         coalesce(last_error, 'the run never reported back — check the Actions tab on GitHub')
      where status = 'dispatched'
        and attempts >= $1
        and dispatched_at < now() - make_interval(mins => $2)
      returning id, slug`,
    [MAX_ATTEMPTS, STALE_MINUTES]
  );

  const claimed = await query(
    `update autopost_schedule
        set status = 'dispatched', dispatched_at = now(), attempts = attempts + 1
      where id in (
        select id from autopost_schedule
         where (status = 'scheduled' and publish_at <= now())
            or (status = 'dispatched' and attempts < $1
                and dispatched_at < now() - make_interval(mins => $2))
         order by publish_at
         limit 25
         for update skip locked
      )
      returning id, slug, publish_at`,
    [MAX_ATTEMPTS, STALE_MINUTES]
  );

  if (!claimed.length) return { claimed: [], gaveUp, dispatched: false };

  try {
    await dispatch();
  } catch (err) {
    const message = String((err && err.message) || err).slice(0, 500);
    await query(
      `update autopost_schedule
          set status = 'scheduled', last_error = $2
        where id = any($1::bigint[])`,
      [claimed.map((row) => row.id), `Couldn't start the publish run: ${message}`]
    );
    return { claimed, gaveUp, dispatched: false, error: message };
  }
  return { claimed, gaveUp, dispatched: true };
}

/** Start the publish workflow on GitHub. */
export async function dispatchGithub({ token, repo, branch, fetchImpl = fetch }) {
  if (!token) throw new Error("GITHUB_TOKEN is not set on the scheduler function");
  const resp = await fetchImpl(
    `https://api.github.com/repos/${repo}/actions/workflows/publish.yml/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "content-type": "application/json",
        "user-agent": "autopost-scheduler",
      },
      body: JSON.stringify({ ref: branch, inputs: { command: "run" } }),
    }
  );
  if (resp.status !== 204) {
    const text = await resp.text().catch(() => "");
    throw new Error(`GitHub ${resp.status}: ${text.slice(0, 300)}`);
  }
}
