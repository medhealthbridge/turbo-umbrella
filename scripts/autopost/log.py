"""Run log.

Everything the run prints also accumulates here, so the dashboard and the
GitHub Actions job summary can replay it without re-running anything.
"""

import os
import sys
from datetime import datetime, timezone

_LINES = []
_STEP = None


class AutopostError(Exception):
    """Something went wrong that should stop this book (not the whole run)."""


def _stamp():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _emit(level, msg, indent=0):
    text = f"{'  ' * indent}{msg}"
    _LINES.append({"at": _stamp(), "level": level, "step": _STEP, "text": text})
    prefix = {"info": "", "ok": "✓ ", "warn": "! ", "error": "✗ ", "step": ""}[level]
    stream = sys.stderr if level == "error" else sys.stdout
    print(f"{prefix}{text}", file=stream, flush=True)


def step(msg):
    """Start a named phase of the run."""
    global _STEP
    _STEP = msg
    _emit("step", f"\n── {msg} " + "─" * max(0, 56 - len(msg)))


def info(msg, indent=1):
    _emit("info", msg, indent)


def ok(msg, indent=1):
    _emit("ok", msg, indent)


def warn(msg, indent=1):
    _emit("warn", msg, indent)


def error(msg, indent=1):
    _emit("error", msg, indent)


def lines():
    return list(_LINES)


def fail(msg):
    """Abort the entire run."""
    error(msg, indent=0)
    sys.exit(1)


def summary(markdown):
    """Append to the GitHub Actions run summary, if we are in Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown.rstrip() + "\n")
    except OSError as exc:  # a broken summary must never fail a publish
        warn(f"could not write the run summary: {exc}")
