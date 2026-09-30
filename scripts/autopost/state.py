"""Durable state — what we have seen, and what we have published.

Two files, both committed back to the repo after every run:

  state/published.json   one record per book, per platform
  state/seen.json        every Drive file we have ever noticed

`seen.json` is what makes "detect a new upload" work: a file is new the
first time it shows up there, and its `first_seen` timestamp is what the
settle delay is measured from.

The publish record is written *before* the risky work and updated after,
so a run that dies half way leaves a `draft` behind rather than a hole —
the next run repairs that draft instead of creating a duplicate.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import log
from .config import ROOT

STATE_DIR = ROOT / "state"
PUBLISHED_FILE = STATE_DIR / "published.json"
SEEN_FILE = STATE_DIR / "seen.json"


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8")) or default
    except json.JSONDecodeError as exc:
        log.fail(f"{path.relative_to(ROOT)} is corrupt: {exc}")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _migrate(entry):
    """Records written by v1 kept the Gumroad id at the top level."""
    entry.setdefault("platforms", {})
    if "product_id" in entry and "gumroad" not in entry["platforms"]:
        entry["platforms"]["gumroad"] = {
            "status": "published",
            "product_id": entry.pop("product_id"),
            "url": entry.pop("url", None),
        }
    entry.setdefault("status", "published")
    entry.setdefault("updated_at", entry.get("published_at"))
    return entry


class State:
    def __init__(self):
        published = _read(PUBLISHED_FILE, {"published": []})
        self.entries = [_migrate(e) for e in published.get("published", [])]
        self.seen = _read(SEEN_FILE, {"files": {}}).get("files", {})
        self.dirty = False

    # ----------------------------------------------------------- published

    def entry(self, slug):
        for e in self.entries:
            if e.get("slug") == slug:
                return e
        return None

    def is_live(self, slug):
        """True once the book is published everywhere that was asked of it.
        A `draft` or `failed` record is deliberately *not* live, so the next
        run picks it back up."""
        entry = self.entry(slug)
        return bool(entry) and entry.get("status") == "published"

    def record(self, slug, **fields):
        entry = self.entry(slug)
        if entry is None:
            entry = {"slug": slug, "first_seen": now()}
            self.entries.append(entry)
        entry.update(fields)
        entry["updated_at"] = now()
        if fields.get("status") == "published":
            entry.setdefault("published_at", entry["updated_at"])
        self.dirty = True
        return entry

    def record_platform(self, slug, platform, **fields):
        entry = self.entry(slug) or self.record(slug)
        entry.setdefault("platforms", {}).setdefault(platform, {}).update(fields)
        entry["updated_at"] = now()
        self.dirty = True
        return entry

    def last_published_at(self):
        stamps = [
            parse_time(e.get("published_at"))
            for e in self.entries
            if e.get("status") == "published"
        ]
        stamps = [s for s in stamps if s]
        return max(stamps) if stamps else None

    def hours_since_last_publish(self):
        last = self.last_published_at()
        if not last:
            return None
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600

    # ---------------------------------------------------------------- seen

    def note_file(self, file_id, name, slug, size=None):
        """Register a source file. Returns True the first time we see it."""
        existing = self.seen.get(file_id)
        if existing:
            # A re-upload keeps its original first_seen, so the settle delay
            # is not restarted by Drive touching the file's modified time.
            if existing.get("name") != name or existing.get("size") != size:
                existing.update({"name": name, "size": size, "slug": slug})
                self.dirty = True
            return False
        self.seen[file_id] = {
            "name": name,
            "slug": slug,
            "size": size,
            "first_seen": now(),
        }
        self.dirty = True
        return True

    def first_seen(self, file_id):
        return parse_time((self.seen.get(file_id) or {}).get("first_seen"))

    # --------------------------------------------------------------- save

    def save(self):
        if not self.dirty:
            return False
        self.entries.sort(key=lambda e: (e.get("published_at") or e.get("updated_at") or "", e.get("slug", "")))
        _write(PUBLISHED_FILE, {"published": self.entries})
        _write(SEEN_FILE, {"files": self.seen})
        return True
