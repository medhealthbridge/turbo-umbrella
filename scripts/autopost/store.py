"""Where everything the publisher remembers is kept.

Two backends behind one interface:

  PgStore    DATABASE_URL is set. Settings, per-book listings, uploaded
             covers, publish history, the queue, the calendar and every run's
             log live in Postgres (Neon). The control panel reads and writes
             the same tables, so nothing about running the shop ever touches
             the repository.

  FileStore  No database configured. The JSON files under state/, books.json
             and assets/artwork/ — the behaviour from before the database,
             kept so the test suite and a quick local run need no Postgres.

`get()` picks one. Everything above this module talks to the interface only.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import log
from .config import ROOT

SCHEMA_FILE = ROOT / "db" / "schema.sql"
ART_KINDS = ("cover", "thumbnail", "pin", "social")
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
EXT = {v: k for k, v in MIME.items() if k != ".jpeg"}

_STORE = None


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default
    except json.JSONDecodeError as exc:
        log.warn(f"{Path(path).name} is not valid JSON ({exc}) — ignoring it")
        return default


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def schema_statements():
    """db/schema.sql split on its `-- ;;` separators."""
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    chunks = text.split("\n-- ;;\n")
    statements = []
    for chunk in chunks:
        body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            statements.append(body)
    return statements


# ===================================================================== files


class FileStore:
    """JSON files in the repository. Used when there is no database."""

    kind = "files"

    def __init__(self):
        self.state_dir = ROOT / "state"

    # settings / overrides --------------------------------------------------

    def settings(self):
        data = _read_json(self.state_dir / "settings.json", {})
        return data if isinstance(data, dict) else {}

    def overrides(self):
        data = _read_json(ROOT / "books.json", [])
        if not isinstance(data, list):
            log.warn("books.json should be a list of books — ignoring it")
            return {}
        return {b["slug"]: b for b in data if isinstance(b, dict) and b.get("slug")}

    # publish state ---------------------------------------------------------

    def load_state(self):
        published = _read_json(self.state_dir / "published.json", {"published": []})
        seen = _read_json(self.state_dir / "seen.json", {"files": {}})
        return list(published.get("published", [])), dict(seen.get("files", {}))

    def save_state(self, entries, seen):
        _write_json(self.state_dir / "published.json", {"published": entries})
        _write_json(self.state_dir / "seen.json", {"files": seen})

    # artwork ---------------------------------------------------------------

    def uploaded_artwork(self, slug, workdir):
        """{kind: Path} for images uploaded through the panel (assets/artwork/)."""
        folder = ROOT / "assets" / "artwork"
        found = {}
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                stem = path.stem.lower()
                for kind in ART_KINDS:
                    if stem == f"{slug}-{kind}" and path.suffix.lower() in MIME:
                        found[kind] = path
        return found

    def save_generated(self, slug, images):
        return None  # the social pack folder already carries copies

    # social ----------------------------------------------------------------

    def save_social(self, book, cfg):
        from . import social

        return social.write_pack(book, cfg)

    # calendar --------------------------------------------------------------

    def _schedule(self):
        data = _read_json(self.state_dir / "schedule.json", {"posts": []})
        return data.get("posts", []) if isinstance(data, dict) else []

    def due_posts(self):
        current = now()
        out = []
        for post in self._schedule():
            when = _parse(post.get("publish_at"))
            if post.get("status") in ("scheduled", "dispatched") and when and when <= current:
                out.append(post)
        return sorted(out, key=lambda p: p["publish_at"])

    def future_slugs(self):
        current = now()
        return {
            p["slug"]
            for p in self._schedule()
            if p.get("status") in ("scheduled", "dispatched") and (_parse(p.get("publish_at")) or current) > current
        }

    def finish_post(self, post_id, status, error=None):
        data = _read_json(self.state_dir / "schedule.json", {"posts": []})
        for post in data.get("posts", []):
            if str(post.get("id")) == str(post_id):
                post["status"] = status
                post["last_error"] = error
                post["finished_at"] = iso(now())
                if status != "published":
                    post["attempts"] = int(post.get("attempts") or 0) + 1
        _write_json(self.state_dir / "schedule.json", data)

    def complete_slug(self, slug):
        """A book went live: any calendar post still waiting for it is done."""
        path = self.state_dir / "schedule.json"
        if not path.exists():
            return
        data = _read_json(path, {"posts": []})
        changed = False
        for post in data.get("posts", []):
            if post.get("slug") == slug and post.get("status") in ("scheduled", "dispatched"):
                post.update(status="published", finished_at=iso(now()), last_error=None)
                changed = True
        if changed:
            _write_json(path, data)

    def note_post_error(self, post_id, error):
        data = _read_json(self.state_dir / "schedule.json", {"posts": []})
        for post in data.get("posts", []):
            if str(post.get("id")) == str(post_id):
                post["last_error"] = error
        _write_json(self.state_dir / "schedule.json", data)

    # runs ------------------------------------------------------------------

    def save_run(self, snapshot):
        target = ROOT / "docs" / "status.json"
        _write_json(target, snapshot)
        return target

    def close(self):
        pass


def _parse(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ================================================================== postgres


class PgStore:
    """Neon Postgres. Used whenever DATABASE_URL is set."""

    kind = "postgres"

    def __init__(self, url):
        import psycopg

        try:
            self.conn = psycopg.connect(url, autocommit=True, connect_timeout=20)
        except Exception as exc:
            log.fail(
                f"could not connect to the database: {exc}\n"
                "  Check the DATABASE_URL secret — it should be the full postgresql:// "
                "connection string from the Neon console."
            )
        self.migrate()
        self.seed()

    # plumbing --------------------------------------------------------------

    def q(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or ())
            if cur.description is None:
                return []
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    @staticmethod
    def jb(value):
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    def migrate(self):
        for statement in schema_statements():
            self.q(statement)

    def seed(self):
        """One-time import of anything still living in files. Each table is only
        seeded while it is empty, so this is safe to run on every start."""
        imported = []

        if not self.q("select 1 from autopost_settings limit 1"):
            data = _read_json(ROOT / "state" / "settings.json", {})
            data.pop("updated_at", None)
            data.pop("updated_by", None)
            self.q(
                "insert into autopost_settings (id, data) values (1, %s) on conflict (id) do nothing",
                (self.jb(data if isinstance(data, dict) else {}),),
            )
            if data:
                imported.append("settings")

        if not self.q("select 1 from autopost_books limit 1"):
            books = _read_json(ROOT / "books.json", [])
            published = _read_json(ROOT / "state" / "published.json", {"published": []})
            records = {e["slug"]: e for e in published.get("published", []) if e.get("slug")}
            overrides = {b["slug"]: b for b in books if isinstance(b, dict) and b.get("slug")}
            for slug in sorted(set(records) | set(overrides)):
                override = {k: v for k, v in overrides.get(slug, {}).items() if k != "slug"}
                record = records.get(slug, {})
                if record:
                    from .state import _migrate

                    record = _migrate(dict(record))
                self._upsert_book(slug, override=override, record=record)
            if overrides or records:
                imported.append(f"{len(overrides)} listing(s), {len(records)} publish record(s)")

        if not self.q("select 1 from autopost_seen limit 1"):
            seen = _read_json(ROOT / "state" / "seen.json", {"files": {}}).get("files", {})
            for file_id, meta in seen.items():
                self._upsert_seen(file_id, meta)
            if seen:
                imported.append(f"{len(seen)} known file(s)")

        if imported:
            log.ok(f"database seeded from the repository: {'; '.join(imported)}", indent=1)

    def _upsert_book(self, slug, override=None, record=None):
        status = (record or {}).get("status")
        published_at = _parse((record or {}).get("published_at"))
        self.q(
            """
            insert into autopost_books (slug, override, record, status, published_at, updated_at)
            values (%s, %s, %s, %s, %s, now())
            on conflict (slug) do update set
              override     = case when %s then excluded.override else autopost_books.override end,
              record       = case when %s then excluded.record   else autopost_books.record   end,
              status       = case when %s then excluded.status   else autopost_books.status   end,
              published_at = case when %s then excluded.published_at else autopost_books.published_at end,
              updated_at   = now()
            """,
            (
                slug,
                self.jb(override or {}),
                self.jb(record or {}),
                status,
                published_at,
                override is not None,
                record is not None,
                record is not None,
                record is not None,
            ),
        )

    def _upsert_seen(self, file_id, meta):
        self.q(
            """
            insert into autopost_seen (file_id, name, slug, size, first_seen)
            values (%s, %s, %s, %s, coalesce(%s, now()))
            on conflict (file_id) do update set
              name = excluded.name, slug = excluded.slug, size = excluded.size
            """,
            (file_id, meta.get("name", ""), meta.get("slug", ""), meta.get("size"), _parse(meta.get("first_seen"))),
        )

    # settings / overrides --------------------------------------------------

    def settings(self):
        rows = self.q("select data from autopost_settings where id = 1")
        data = rows[0]["data"] if rows else {}
        return data if isinstance(data, dict) else {}

    def overrides(self):
        rows = self.q("select slug, override from autopost_books where override <> '{}'::jsonb")
        return {r["slug"]: {**(r["override"] or {}), "slug": r["slug"]} for r in rows}

    # publish state ---------------------------------------------------------

    def load_state(self):
        entries = []
        for row in self.q("select slug, record from autopost_books where record <> '{}'::jsonb order by slug"):
            entries.append({**(row["record"] or {}), "slug": row["slug"]})
        seen = {}
        for row in self.q("select file_id, name, slug, size, first_seen from autopost_seen"):
            seen[row["file_id"]] = {
                "name": row["name"],
                "slug": row["slug"],
                "size": row["size"],
                "first_seen": iso(row["first_seen"]),
            }
        return entries, seen

    def save_state(self, entries, seen):
        for entry in entries:
            self._upsert_book(entry["slug"], record=entry)
        for file_id, meta in seen.items():
            self._upsert_seen(file_id, meta)

    # artwork ---------------------------------------------------------------

    def uploaded_artwork(self, slug, workdir):
        found = {}
        rows = self.q(
            "select kind, mime, bytes from autopost_artwork where slug = %s and source = 'upload'", (slug,)
        )
        for row in rows:
            path = Path(workdir) / f"upload-{slug}-{row['kind']}{EXT.get(row['mime'], '.jpg')}"
            path.write_bytes(bytes(row["bytes"]))
            found[row["kind"]] = path
        return found

    def save_generated(self, slug, images):
        for kind, path in (images or {}).items():
            path = Path(path)
            if not path.exists():
                continue
            self.q(
                """
                insert into autopost_artwork (slug, kind, source, mime, bytes, updated_at)
                values (%s, %s, 'generated', %s, %s, now())
                on conflict (slug, kind, source) do update
                  set mime = excluded.mime, bytes = excluded.bytes, updated_at = now()
                """,
                (slug, kind, MIME.get(path.suffix.lower(), "image/jpeg"), path.read_bytes()),
            )

    # social ----------------------------------------------------------------

    def save_social(self, book, cfg):
        # The captions travel inside the book's record (see runner), and the
        # images were just saved as generated artwork, so there is no folder
        # to write — the panel shows both.
        return None

    # calendar --------------------------------------------------------------

    def due_posts(self):
        rows = self.q(
            """
            select id, slug, publish_at, status, attempts
              from autopost_schedule
             where status in ('scheduled', 'dispatched') and publish_at <= now()
             order by publish_at
             limit 25
            """
        )
        return [{**r, "publish_at": iso(r["publish_at"])} for r in rows]

    def future_slugs(self):
        rows = self.q(
            "select distinct slug from autopost_schedule where status in ('scheduled', 'dispatched') and publish_at > now()"
        )
        return {r["slug"] for r in rows}

    def finish_post(self, post_id, status, error=None):
        self.q(
            """
            update autopost_schedule
               set status = %s, last_error = %s, finished_at = now(),
                   attempts = attempts + case when %s = 'published' then 0 else 1 end
             where id = %s
            """,
            (status, error, status, post_id),
        )

    def complete_slug(self, slug):
        self.q(
            """
            update autopost_schedule
               set status = 'published', finished_at = now(), last_error = null
             where slug = %s and status in ('scheduled', 'dispatched')
            """,
            (slug,),
        )

    def note_post_error(self, post_id, error):
        self.q("update autopost_schedule set last_error = %s where id = %s", (error, post_id))

    # runs ------------------------------------------------------------------

    def save_run(self, snapshot):
        log_lines = snapshot.pop("log", [])
        self.q(
            "insert into autopost_runs (finished_at, summary, log) values (now(), %s, %s)",
            (self.jb(snapshot), self.jb(log_lines)),
        )
        # Keep the table from growing forever: the last 200 runs are plenty.
        self.q(
            "delete from autopost_runs where id < (select coalesce(min(id), 0) from "
            "(select id from autopost_runs order by id desc limit 200) recent)"
        )
        return None

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ===================================================================== pick


def get():
    global _STORE
    if _STORE is None:
        url = os.environ.get("DATABASE_URL", "").strip()
        _STORE = PgStore(url) if url else FileStore()
    return _STORE


def reset():
    """Forget the cached store (tests)."""
    global _STORE
    if _STORE is not None:
        _STORE.close()
    _STORE = None
