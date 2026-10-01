"""The contract every selling platform implements.

Adding a marketplace means writing one subclass and registering it — the
scheduler, copy engine, cover art and state tracking are already done and
are shared by every platform. See docs/PLATFORMS.md.
"""

import os
import subprocess

from .. import log


class Result(dict):
    """What a publish attempt produced, per platform."""

    def __init__(self, status, product_id=None, url=None, error=None, **extra):
        super().__init__(status=status, product_id=product_id, url=url, error=error, **extra)

    @property
    def ok(self):
        return self["status"] in ("published", "draft", "skipped")


class Platform:
    #: Short key used in config.yml and in the state file.
    name = "base"
    #: Human-readable, for logs and the dashboard.
    label = "Base"
    #: Env vars that must be present for this platform to run.
    required_secrets = ()

    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.conf = cfg.platform(self.name)
        self.dry_run = dry_run

    # -------------------------------------------------------------- checks

    def missing_secrets(self):
        return [name for name in self.required_secrets if not os.environ.get(name, "").strip()]

    def preflight(self):
        """Raise if this platform cannot possibly succeed. Called before any
        file is downloaded, so a misconfigured platform fails fast and cheap."""
        missing = self.missing_secrets()
        if missing and not self.dry_run:
            raise log.AutopostError(
                f"{self.label} is enabled but {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not set. "
                "Add it under Settings → Secrets and variables → Actions, "
                f"or set platforms.{self.name}.enabled: false in config.yml."
            )

    # ------------------------------------------------------------- publish

    def publish(self, book, pdf_path):
        """Create or repair the listing and return a Result."""
        raise NotImplementedError

    # ------------------------------------------------------------- helpers

    def run(self, cmd, allow_fail=False, secret_args=()):
        """Run a CLI, logging a redacted version of the command."""
        shown = []
        for arg in cmd[1:]:
            if arg in secret_args:
                shown.append("***")
            elif len(arg) > 80:
                shown.append(arg[:77] + "…")
            else:
                shown.append(arg)
        log.info(f"$ {cmd[0]} {' '.join(shown)}", indent=2)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except FileNotFoundError:
            raise log.AutopostError(f"{cmd[0]} is not installed on this runner")
        except subprocess.TimeoutExpired:
            raise log.AutopostError(f"{cmd[0]} timed out after 15 minutes")

        if proc.stdout.strip():
            log.info(proc.stdout.strip()[:1500], indent=3)
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip())[:600]
            if allow_fail:
                log.warn(f"{cmd[0]} exited {proc.returncode}: {detail}", indent=3)
                return None
            raise log.AutopostError(f"{cmd[0]} failed (exit {proc.returncode}): {detail}")
        return proc.stdout


class NotYetConnected(Platform):
    """A platform that is wired in but has no implementation yet.

    Enabling one of these in config.yml gives a clear, actionable message
    rather than a crash, and keeps the run going for the platforms that do
    work.
    """

    how_to = ""

    def publish(self, book, pdf_path):
        raise log.AutopostError(
            f"{self.label} support is not implemented yet. {self.how_to} "
            f"Set platforms.{self.name}.enabled: false to silence this."
        )
