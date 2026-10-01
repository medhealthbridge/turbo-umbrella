"""Load config.yml, apply the built-in defaults, apply env overrides.

Config is read through dotted paths so a missing or half-filled config.yml
never crashes a run:

    cfg.get("schedule.publish_at", "09:15")
    cfg.platform("gumroad")["enabled"]
"""

import copy
import os
from datetime import time
from pathlib import Path

import yaml

from . import log

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.yml"

# Written by the web control panel, merged over config.yml. Keeping the
# panel's output in its own JSON file means config.yml never has to be
# machine-rewritten, so all of its comments survive.
SETTINGS_FILE = ROOT / "state" / "settings.json"

# Every key the code reads has a default here, so an empty config.yml still
# produces a working run.
DEFAULTS = {
    "schedule": {
        "timezone": "UTC",
        "publish_at": "09:15",
        "window_minutes": 120,
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "max_per_run": 1,
        "min_hours_between": 20,
        "settle_minutes": 30,
    },
    "source": {
        "kind": "gdrive",
        "folder_id": "",
        "recursive": True,
        "ignore_patterns": [],
        "min_size_mb": 0.05,
    },
    "pricing": {"default": "5.99", "rules": []},
    "marketing": {
        "audience": "parents and teachers",
        "brand_promise": "",
        "guarantees": [],
        "base_tags": [],
        "max_tags": 10,
        "title_suffix": "",
        "max_title_length": 120,
        "max_summary_length": 190,
        "receipt": "Thank you for your purchase! Your download is ready.",
        "refund_period": "30",
    },
    "images": {
        "enabled": True,
        "cover": {"width": 1280, "height": 720},
        "thumbnail": {"width": 800, "height": 800},
        "pin": {"width": 1000, "height": 1500},
        "social": {"width": 1080, "height": 1080},
        "quality": 90,
    },
    "platforms": {"gumroad": {"enabled": True, "publish": True}},
    "social": {
        "enabled": True,
        "auto_post": False,
        "output_dir": "content/social",
        "networks": {},
    },
    "dashboard": {
        "enabled": True,
        "title": "Autopost",
        "subtitle": "",
        "output": "docs/index.html",
        "currency": "$",
    },
}

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _merge(base, override):
    """Deep-merge `override` onto a copy of `base`."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data, path=None):
        self.data = data
        self.path = path

    # ------------------------------------------------------------ access

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    def section(self, name):
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    def platform(self, name):
        return self.section(f"platforms.{name}")

    def enabled_platforms(self):
        return [
            name
            for name, conf in sorted(self.section("platforms").items())
            if isinstance(conf, dict) and conf.get("enabled")
        ]

    def enabled_networks(self):
        if not self.get("social.enabled", True):
            return []
        return [
            name
            for name, conf in sorted(self.section("social.networks").items())
            if isinstance(conf, dict) and conf.get("enabled")
        ]

    # ------------------------------------------------- derived / validated

    @property
    def folder_id(self):
        """Repository variable wins over config.yml, so you can point a fork
        at a different folder without editing the file."""
        return (os.environ.get("GDRIVE_FOLDER_ID") or self.get("source.folder_id") or "").strip()

    @property
    def publish_time(self):
        raw = str(self.get("schedule.publish_at", "09:15")).strip()
        try:
            hour, minute = (int(p) for p in raw.split(":", 1))
            return time(hour=hour % 24, minute=minute % 60)
        except (ValueError, TypeError):
            log.warn(f"schedule.publish_at is not HH:MM ({raw!r}) — falling back to 09:15")
            return time(hour=9, minute=15)

    @property
    def publish_days(self):
        raw = self.get("schedule.days", DAYS) or DAYS
        chosen = {str(d).strip().lower()[:3] for d in raw}
        valid = [d for d in DAYS if d in chosen]
        if not valid:
            log.warn("schedule.days matched no weekday — treating every day as allowed")
            return list(DAYS)
        return valid

    def image_size(self, kind):
        spec = self.section(f"images.{kind}")
        return int(spec.get("width", 1000)), int(spec.get("height", 1000))

    def price_for(self, *texts):
        """First pricing rule whose keywords appear in any of `texts`."""
        haystack = " ".join(t for t in texts if t).lower()
        for rule in self.get("pricing.rules", []) or []:
            if not isinstance(rule, dict):
                continue
            for keyword in rule.get("match", []) or []:
                if str(keyword).lower() in haystack:
                    return str(rule.get("price", self.get("pricing.default")))
        return str(self.get("pricing.default", "5.99"))


def _overlay():
    """The control panel's settings, if it has written any."""
    if not SETTINGS_FILE.exists():
        return {}
    import json

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warn(f"state/settings.json is not valid JSON ({exc}) — ignoring the panel's settings")
        return {}
    if not isinstance(data, dict):
        log.warn("state/settings.json should be an object — ignoring it")
        return {}
    data.pop("updated_at", None)
    data.pop("updated_by", None)
    return data


def load(path=None):
    path = Path(path) if path else CONFIG_FILE
    raw = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.fail(f"{path.name} is not valid YAML: {exc}")
        if not isinstance(raw, dict):
            log.fail(f"{path.name} must be a mapping at the top level")
    else:
        log.warn(f"{path.name} not found — using built-in defaults")

    merged = _merge(DEFAULTS, raw)
    overlay = _overlay()
    if overlay:
        merged = _merge(merged, overlay)
        log.info(f"applied {len(overlay)} setting group(s) from the control panel")
    return Config(merged, path)
