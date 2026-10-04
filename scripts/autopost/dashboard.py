"""Status data for the control panel.

The panel itself is a hand-written app at docs/index.html that reads live
data through its API, so this module no longer generates any HTML — if it
did, every run would overwrite the panel.

What it still writes is docs/status.json: a plain snapshot of the same
numbers, committed with each run. That keeps the deployment useful without a
login, gives anything else a stable file to read, and leaves a record in git
of what the shop looked like after every run.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import log, scheduler
from .config import ROOT


def build(cfg, st, queue=None, output=None):
    """Write docs/status.json. Returns its path."""
    output = Path(output or ROOT / cfg.get("dashboard.output", "docs/index.html"))
    target = output.parent / "status.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    entries = st.entries
    live = [e for e in entries if e.get("status") == "published"]
    live_slugs = {e["slug"] for e in live}

    if not queue:
        queue = [
            {"slug": meta.get("slug", ""), "name": meta.get("name", ""),
             "first_seen": meta.get("first_seen")}
            for meta in st.seen.values()
            if meta.get("slug") and meta.get("slug") not in live_slugs
        ]

    snapshot = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "live": len(live),
        "queued": len(queue),
        "needs_attention": [
            {"slug": e.get("slug"), "status": e.get("status"), "error": e.get("error")}
            for e in entries
            if e.get("status") and e.get("status") != "published"
        ],
        "catalog_value": round(sum(float(e.get("price") or 0) for e in live), 2),
        "pages_published": sum(int(e.get("pages") or 0) for e in live),
        "next_window": scheduler.next_run(cfg).isoformat(),
        "schedule": {
            "timezone": cfg.get("schedule.timezone"),
            "publish_at": str(cfg.get("schedule.publish_at")),
            "days": cfg.publish_days,
            "max_per_run": cfg.get("schedule.max_per_run"),
        },
        "platforms": cfg.enabled_platforms(),
        "networks": cfg.enabled_networks(),
        "queue": queue,
        "books": entries,
        "log": log.lines(),
    }

    from . import store

    where = store.get()
    if where.kind == "files":
        # The run log, the timestamp and the next window change every hour even
        # when nothing happened. Rewriting the file for those alone would mean
        # a commit — and a redeploy — every hour, so only real changes count.
        volatile = ("generated_at", "next_window", "log")
        if target.exists():
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                previous = {}
            if {k: v for k, v in previous.items() if k not in volatile} == \
               {k: v for k, v in snapshot.items() if k not in volatile}:
                log.info("status unchanged — nothing to write", indent=1)
                return target
        target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            shown = target.relative_to(ROOT)
        except ValueError:
            shown = target
        log.ok(f"status: {shown}", indent=1)
        return target
    where.save_run(snapshot)
    log.ok("run recorded in the database", indent=1)
    return None
