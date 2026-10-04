"""When to publish.

The workflow wakes up every hour; this module decides whether a given wake-up
is the real one. Doing it here rather than in cron means the posting time
lives in config.yml in your own time zone — no UTC arithmetic, no editing
YAML cron, and a schedule change takes effect on the next hour.

It also absorbs GitHub's scheduled-run behaviour, which is best-effort: runs
are routinely 5-20 minutes late and are sometimes skipped entirely. The
`window_minutes` setting is what turns that from a missed day into a late
post.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import log
from .config import DAYS


def zone(cfg):
    name = cfg.get("schedule.timezone", "UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warn(f"unknown time zone {name!r} — falling back to UTC")
        return timezone.utc


def local_now(cfg, now=None):
    return (now or datetime.now(timezone.utc)).astimezone(zone(cfg))


def next_run(cfg, after=None):
    """The next moment the poster is allowed to publish."""
    tz = zone(cfg)
    moment = (after or datetime.now(timezone.utc)).astimezone(tz)
    target = cfg.publish_time
    allowed = set(cfg.publish_days)

    candidate = moment.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    for _ in range(8):
        if DAYS[candidate.weekday()] in allowed:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def decide(cfg, state, force=False, now=None):
    """(may_publish, reason). `reason` is shown in the log and dashboard."""
    if force:
        return True, "forced by hand — the schedule was not checked"

    tz_now = local_now(cfg, now)
    target = cfg.publish_time
    window = int(cfg.get("schedule.window_minutes", 120))

    if DAYS[tz_now.weekday()] not in cfg.publish_days:
        return False, f"{DAYS[tz_now.weekday()]} is not a posting day"

    scheduled = tz_now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    minutes_since = (tz_now - scheduled).total_seconds() / 60
    if minutes_since < 0:
        return False, (
            f"too early — the window opens at {target.strftime('%H:%M')} "
            f"{tz_now.tzname()} (in {abs(int(minutes_since))} min)"
        )
    if minutes_since > window:
        return False, (
            f"too late — the {target.strftime('%H:%M')} window closed "
            f"{int(minutes_since - window)} min ago"
        )

    gap = int(cfg.get("schedule.min_hours_between", 0))
    since = state.hours_since_last_publish()
    if gap and since is not None and since < gap:
        return False, (
            f"only {since:.1f}h since the last publish, minimum gap is {gap}h"
        )

    return True, f"inside the {target.strftime('%H:%M')} {tz_now.tzname()} window"


def settled(cfg, state, source_file, now=None):
    """(ready, reason) — has the file sat in Drive long enough to be safe?

    Guards against publishing a PDF that is still uploading, or one that is
    about to be replaced two minutes later by the corrected version.
    """
    minutes = int(cfg.get("schedule.settle_minutes", 0))
    if minutes <= 0:
        return True, ""
    first_seen = state.first_seen(source_file.id)
    if not first_seen:
        return False, f"first seen just now — waiting {minutes} min for it to settle"
    age = ((now or datetime.now(timezone.utc)) - first_seen).total_seconds() / 60
    if age < minutes:
        return False, f"only {int(age)} min old — waiting {minutes} min for it to settle"
    return True, ""
