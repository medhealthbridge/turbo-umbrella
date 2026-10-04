"""Command line.

    python scripts/publish.py            # a normal scheduled run
    python scripts/publish.py --force    # publish now, ignore the schedule
    python scripts/publish.py --dry-run --slug my-book
    python scripts/publish.py scan       # detect new files, publish nothing
    python scripts/publish.py plan       # what would happen, and when
    python scripts/publish.py dashboard  # rebuild the status page only

Every flag also has an environment variable, because that is how the
GitHub Actions workflow passes the run inputs through.
"""

import argparse
import os
import sys

from . import config, dashboard, log, metadata, runner, scheduler, state as state_mod


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="autopost",
        description="Watch a Drive folder and publish new books on a schedule.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "scan", "plan", "dashboard"],
        help="run: the normal publish cycle (default). "
        "scan: notice new files without publishing. "
        "plan: show the schedule and the queue. "
        "dashboard: rebuild the status page from existing state.",
    )
    parser.add_argument("--slug", default=os.environ.get("ONLY_SLUG", "").strip(),
                        help="publish one specific book instead of the next in the queue")
    parser.add_argument("--force", action="store_true", default=_env_flag("FORCE"),
                        help="ignore the schedule window and the settle delay")
    parser.add_argument("--dry-run", action="store_true", default=_env_flag("DRY_RUN"),
                        help="do everything except call the platforms; previews land in out/")
    parser.add_argument("--no-ai", action="store_true", default=_env_flag("NO_AI"),
                        help="skip the optional Claude copy polish")
    parser.add_argument("--config", default=os.environ.get("AUTOPOST_CONFIG", ""),
                        help="path to config.yml")
    return parser.parse_args(argv)


def cmd_plan(cfg):
    st = state_mod.State()
    log.step("Schedule")
    may, reason = scheduler.decide(cfg, st)
    log.info(f"time zone     {cfg.get('schedule.timezone')}")
    log.info(f"posts at      {cfg.publish_time:%H:%M} on {', '.join(cfg.publish_days)}")
    log.info(f"window        {cfg.get('schedule.window_minutes')} min · "
             f"max {cfg.get('schedule.max_per_run')} per run · "
             f"min gap {cfg.get('schedule.min_hours_between')}h")
    log.info(f"local now     {scheduler.local_now(cfg):%a %d %b %H:%M %Z}")
    log.info(f"next window   {scheduler.next_run(cfg):%a %d %b %H:%M %Z}")
    log.info(f"right now     {'would publish' if may else 'holding'} — {reason}")

    log.step("Configuration")
    log.info(f"drive folder  {cfg.folder_id or '(not set)'}")
    log.info(f"platforms     {', '.join(cfg.enabled_platforms()) or 'none'}")
    log.info(f"social        {', '.join(cfg.enabled_networks()) or 'off'}")
    log.info(f"default price {cfg.get('pricing.default')}")

    log.step("Catalog")
    live = [e for e in st.entries if e.get("status") == "published"]
    log.info(f"{len(live)} live, {len(st.seen)} file(s) known in Drive")
    for entry in st.entries:
        if entry.get("status") != "published":
            log.warn(f"{entry['slug']}: {entry.get('status')} — {entry.get('error', 'needs another run')}")
    return 0


def cmd_dashboard(cfg):
    st = state_mod.State()
    dashboard.build(cfg, st)
    return 0


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = config.load(args.config or None)

    if args.command == "plan":
        return cmd_plan(cfg)
    if args.command == "dashboard":
        return cmd_dashboard(cfg)

    return runner.run(
        cfg,
        force=args.force,
        dry_run=args.dry_run,
        only_slug=metadata.slugify(args.slug) if args.slug else "",
        use_ai=not args.no_ai,
        scan_only=args.command == "scan",
    )
