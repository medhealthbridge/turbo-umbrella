"""Registry of selling platforms.

Turn one on in config.yml under `platforms:` and every future book goes
there too — the scheduler, copy and cover art are already shared.
"""

from .base import NotYetConnected, Platform, Result
from .etsy import Etsy
from .gumroad import Gumroad
from .lemonsqueezy import LemonSqueezy
from .payhip import Payhip
from .raketph import RaketPH


REGISTRY = {p.name: p for p in (Gumroad, Payhip, LemonSqueezy, Etsy, RaketPH)}


def enabled(cfg, dry_run=False):
    """Instantiate every platform switched on in config.yml."""
    chosen = []
    for name in cfg.enabled_platforms():
        cls = REGISTRY.get(name)
        if cls is None:
            from .. import log

            log.warn(f"config.yml enables an unknown platform {name!r} — ignoring it")
            continue
        chosen.append(cls(cfg, dry_run=dry_run))
    return chosen


__all__ = ["Platform", "Result", "NotYetConnected", "REGISTRY", "enabled"]
