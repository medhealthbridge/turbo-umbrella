#!/usr/bin/env python3
"""End-to-end smoke test — no network, no real Gumroad, no real Drive.

Builds throwaway PDFs, puts a stand-in `gumroad` CLI on PATH, and runs the
real publisher against them in a temporary copy of the repo. Checks the
things that actually broke in production:

  * a brand-new file is detected and published
  * a second run publishes nothing (no duplicates)
  * a run that died after `create` is repaired, not duplicated
  * a cover is always uploaded (the stand-in refuses to publish without one)
  * the page count in the title is corrected from the real PDF
  * a calendar post goes out when it is due, and the automatic drip leaves
    a book alone while it is on the calendar for later

Every scenario runs against whichever storage backend is configured:

  python tests/smoke.py                       JSON files (no database)
  AUTOPOST_TEST_DATABASE_URL=postgresql://... python tests/smoke.py
                                              a real Postgres; each run gets
                                              its own throwaway database
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys, hashlib
STORE = os.environ["FAKE_GUMROAD_STORE"]

def load():
    try: return json.load(open(STORE))
    except Exception: return {"products": []}

args = sys.argv[1:]
if not args or args[0] != "products":
    print("fake-gumroad 0.1"); sys.exit(0)
sub, rest, db = args[1], args[2:], load()

def flags(argv):
    out, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out.setdefault(key, []).append(argv[i + 1]); i += 2
            else:
                out.setdefault(key, []).append(True); i += 1
        else:
            i += 1
    return out

def save(d): json.dump(d, open(STORE, "w"), indent=2)

if sub == "list":
    print(json.dumps({"products": db["products"]})); sys.exit(0)

if sub == "create":
    f = flags(rest)
    name = f.get("name", ["untitled"])[0]
    if not f.get("file"):
        print("error: --file is required", file=sys.stderr); sys.exit(2)
    # Gumroad will not publish a product with no cover — this is what made
    # the very first production run stall in draft.
    if not f.get("cover-image"):
        print("error: a product with no cover cannot be published", file=sys.stderr); sys.exit(3)
    pid = hashlib.md5(name.encode()).hexdigest()[:12] + "=="
    prod = {"id": pid, "name": name, "published": False,
            "price": f.get("price", [""])[0], "tags": f.get("tag", []),
            "summary": f.get("custom-summary", [""])[0],
            "description": f.get("description", [""])[0],
            "permalink": f.get("custom-permalink", [""])[0],
            "cover": f.get("cover-image", [None])[0]}
    db["products"] = [p for p in db["products"] if p["name"] != name] + [prod]
    save(db); print(json.dumps({"id": pid, "name": name})); sys.exit(0)

pid = rest[0] if rest else ""
prod = next((p for p in db["products"] if p["id"] == pid), None)
if not prod:
    print(f"error: no product {pid}", file=sys.stderr); sys.exit(4)
if sub == "update":
    for k, v in flags(rest[1:]).items(): prod[k] = v if k == "tag" else v[0]
    save(db); print(json.dumps({"id": pid, "updated": True}))
elif sub == "publish":
    prod["published"] = True; save(db); print(json.dumps({"id": pid, "published": True}))
elif sub == "view":
    print(json.dumps({**prod, "short_url": "https://gum.co/" + (prod["permalink"] or pid[:6])}))
else:
    print("https://gum.co/" + (prod["permalink"] or pid[:6]))
'''

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{('  — ' + detail) if detail and not condition else ''}")


def make_pdf(path, title, pages):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        if i == 1:
            c.setFont("Helvetica-Bold", 28)
            c.drawString(60, 660, title)
            c.rect(60, 180, 480, 430)
        else:
            c.setFont("Helvetica", 15)
            c.drawString(60, 700, f"Page {i}")
        c.showPage()
    c.save()


# --------------------------------------------------------------- backends
#
# The scenarios below poke at stored state (mark a book failed, add a
# calendar post, change a setting). Each backend knows how to do that in its
# own storage, so the scenarios themselves stay identical.


class FilesBackend:
    name = "files"

    def __init__(self, repo):
        self.repo = repo

    def env(self):
        return {}

    def set_settings(self, data):
        (self.repo / "state" / "settings.json").write_text(json.dumps(data, indent=2))

    def mark_all_failed(self):
        path = self.repo / "state" / "published.json"
        state = json.loads(path.read_text())
        for entry in state["published"]:
            entry["status"] = "failed"
        path.write_text(json.dumps(state, indent=2))

    def add_post(self, slug, minutes_from_now):
        from datetime import datetime, timedelta, timezone

        path = self.repo / "state" / "schedule.json"
        data = json.loads(path.read_text()) if path.exists() else {"posts": []}
        post_id = len(data["posts"]) + 1
        when = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
        data["posts"].append({"id": post_id, "slug": slug, "status": "scheduled",
                              "publish_at": when.isoformat().replace("+00:00", "Z")})
        path.write_text(json.dumps(data, indent=2))
        return post_id

    def post_status(self, post_id):
        data = json.loads((self.repo / "state" / "schedule.json").read_text())
        return next(p for p in data["posts"] if p["id"] == post_id)["status"]

    def run_recorded(self):
        return (self.repo / "docs" / "status.json").exists()

    def social_recorded(self, slug):
        return (self.repo / "content" / "social" / slug / "pinterest.txt").exists()

    def generated_art(self, slug):
        return True  # file mode keeps them in the social pack folder

    def close(self):
        pass


class PostgresBackend:
    name = "postgres"

    def __init__(self, repo, admin_url):
        import uuid

        import psycopg

        self.psycopg = psycopg
        self.db = "autopost_smoke_" + uuid.uuid4().hex[:10]
        self.admin_url = admin_url
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'create database "{self.db}"')
        self.url = self._url_for(self.db)

    def _url_for(self, db):
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(self.admin_url)
        return urlunsplit((parts.scheme, parts.netloc, "/" + db, parts.query, parts.fragment))

    def q(self, sql, params=()):
        with self.psycopg.connect(self.url, autocommit=True) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def env(self):
        return {"DATABASE_URL": self.url}

    def set_settings(self, data):
        from psycopg.types.json import Jsonb

        self.q("insert into autopost_settings (id, data) values (1, %s) "
               "on conflict (id) do update set data = excluded.data", (Jsonb(data),))

    def mark_all_failed(self):
        self.q("update autopost_books set status = 'failed', "
               "record = jsonb_set(record, '{status}', '\"failed\"') where record <> '{}'::jsonb")

    def add_post(self, slug, minutes_from_now):
        rows = self.q("insert into autopost_schedule (slug, publish_at) values "
                      "(%s, now() + make_interval(mins => %s)) returning id", (slug, minutes_from_now))
        return rows[0][0]

    def post_status(self, post_id):
        return self.q("select status from autopost_schedule where id = %s", (post_id,))[0][0]

    def run_recorded(self):
        return bool(self.q("select 1 from autopost_runs limit 1"))

    def social_recorded(self, slug):
        rows = self.q("select record -> 'social' from autopost_books where slug = %s", (slug,))
        social = rows[0][0] if rows else None
        return bool(social) and "pinterest" in social and "gum.co" in social["pinterest"]["text"]

    def generated_art(self, slug):
        rows = self.q("select kind from autopost_artwork where slug = %s and source = 'generated'", (slug,))
        return {r[0] for r in rows} >= {"cover", "thumbnail", "pin", "social"}

    def close(self):
        with self.psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'drop database if exists "{self.db}"')  # every connection is closed by now


# ------------------------------------------------------------------ main


def main():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print("This test needs reportlab: pip install -r requirements-dev.txt")
        return 2

    work = Path(tempfile.mkdtemp(prefix="autopost-smoke-"))
    backend = None
    try:
        repo = work / "repo"
        shutil.copytree(
            ROOT, repo,
            ignore=shutil.ignore_patterns(".git", "out", "__pycache__", "content", "node_modules"),
        )
        # Start from an empty history so the test is deterministic.
        (repo / "state").mkdir(exist_ok=True)
        (repo / "state" / "published.json").write_text('{"published": []}\n')
        (repo / "state" / "seen.json").write_text('{"files": {}}\n')
        (repo / "state" / "settings.json").unlink(missing_ok=True)
        (repo / "state" / "schedule.json").unlink(missing_ok=True)
        (repo / "books.json").write_text("[]\n")
        for stale in (repo / "docs").glob("status.json"):
            stale.unlink()

        cfg = (repo / "config.yml").read_text()
        cfg = cfg.replace("min_size_mb: 0.05", "min_size_mb: 0.0")
        cfg = cfg.replace("settle_minutes: 30", "settle_minutes: 0")
        (repo / "config.yml").write_text(cfg)

        admin = os.environ.get("AUTOPOST_TEST_DATABASE_URL", "").strip()
        backend = PostgresBackend(repo, admin) if admin else FilesBackend(repo)
        print(f"storage backend: {backend.name}")

        drive = work / "drive"
        drive.mkdir()
        make_pdf(drive / "ocean-buddies-coloring.pdf", "Ocean Buddies Coloring Book", 26)
        make_pdf(drive / "wip-not-ready.pdf", "Work in progress", 3)

        bindir = work / "bin"
        bindir.mkdir()
        (bindir / "gumroad").write_text(FAKE_CLI)
        (bindir / "gumroad").chmod(0o755)

        store = work / "store.json"
        env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GUMROAD_STORE": str(store),
            "AUTOPOST_LOCAL_DIR": str(drive),
            "GUMROAD_ACCESS_TOKEN": "smoke-test",
            "NO_AI": "true",
        }
        for key in ("ANTHROPIC_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "DATABASE_URL"):
            env.pop(key, None)
        env.update(backend.env())

        def run(*args):
            return subprocess.run(
                [sys.executable, str(repo / "scripts" / "publish.py"), *args],
                cwd=repo, env=env, capture_output=True, text=True,
            )

        def products():
            return json.loads(store.read_text())["products"] if store.exists() else []

        def named(fragment):
            return [p for p in products() if fragment.lower() in p["name"].lower()]

        print("\n1. detection + first publish")
        first = run("--force")
        if first.returncode != 0:
            print(first.stdout[-3000:]); print(first.stderr[-2000:])
        check("run succeeded", first.returncode == 0, f"exit {first.returncode}")
        check("the new file was detected", "new file detected: ocean-buddies-coloring.pdf" in first.stdout)
        check("the work-in-progress file was ignored", "wip-not-ready" not in first.stdout)
        check("exactly one product exists", len(products()) == 1, f"{len(products())} products")
        check("it was published", bool(products() and products()[0]["published"]))
        check("a cover was uploaded", bool(products() and products()[0].get("cover")))
        check("the real page count is in the title",
              bool(products()) and "26 Pages" in products()[0]["name"],
              products()[0]["name"] if products() else "")
        check("tags were set", bool(products()) and len(products()[0]["tags"]) >= 4)
        check("a summary was set", bool(products()) and len(products()[0]["summary"]) > 30)
        check("the run was recorded", backend.run_recorded())
        check("social captions were kept", backend.social_recorded("ocean-buddies-coloring"))
        check("the generated images were kept", backend.generated_art("ocean-buddies-coloring"))
        if backend.name == "files":
            check("the control panel was not overwritten by the run",
                  (repo / "docs" / "index.html").read_text().lstrip().startswith("<!doctype html>"))

        print("\n2. re-run is a no-op (no duplicates)")
        second = run("--force")
        check("run succeeded", second.returncode == 0)
        check("nothing new was published", "nothing new to publish" in second.stdout)
        check("still exactly one product", len(products()) == 1, f"{len(products())} products")
        if backend.name == "files":
            status = repo / "docs" / "status.json"
            before = status.read_bytes()
            run("--force")
            check("an idle run leaves status.json untouched (no hourly commit)", status.read_bytes() == before)

        print("\n3. a crashed run is repaired, not duplicated")
        backend.mark_all_failed()
        store_data = json.loads(store.read_text())
        for product in store_data["products"]:
            product["published"] = False
        store.write_text(json.dumps(store_data, indent=2))

        third = run("--force")
        check("run succeeded", third.returncode == 0)
        check("the existing draft was reused", "not creating a duplicate" in third.stdout)
        check("still exactly one product", len(products()) == 1, f"{len(products())} products")
        check("it is published again", bool(products() and products()[0]["published"]))

        print("\n4. the schedule holds outside its window")
        cfg = (repo / "config.yml").read_text().replace("window_minutes: 120", "window_minutes: 1")
        (repo / "config.yml").write_text(cfg)
        make_pdf(drive / "dino-diggers.pdf", "Dino Diggers", 20)
        fourth = run()  # no --force, so the window applies
        check("run succeeded", fourth.returncode == 0)
        check("it held instead of publishing", "holding" in fourth.stdout.lower())
        check("no extra product was created", len(products()) == 1, f"{len(products())} products")

        print("\n5. dry run sends nothing")
        fifth = run("--force", "--dry-run")
        check("run succeeded", fifth.returncode == 0)
        check("previews were written", (repo / "out").exists())
        check("still exactly one product", len(products()) == 1, f"{len(products())} products")

        print("\n6. the control panel's settings override config.yml")
        backend.set_settings({
            "schedule": {"publish_at": "16:45", "timezone": "Europe/London", "days": ["tue"]},
            "pricing": {"default": "7.50"},
        })
        plan = run("plan")
        check("run succeeded", plan.returncode == 0)
        check("the overlay was reported", "setting group(s) from the control panel" in plan.stdout)
        check("the panel's time and days win", "posts at      16:45 on tue" in plan.stdout, plan.stdout[-500:])
        check("the panel's price wins", "default price 7.50" in plan.stdout)
        check("unset fields fall back to config.yml", "max 1 per run" in plan.stdout)

        if backend.name == "files":
            (repo / "state" / "settings.json").write_text("{ not json")
            broken = run("plan")
            check("a corrupt settings file is survivable", broken.returncode == 0)
            check("and is reported", "not valid JSON" in broken.stdout + broken.stderr)

        print("\n7. the calendar")
        # Automatic publishing off: from here on only the calendar may publish.
        backend.set_settings({"schedule": {"auto": False}})
        make_pdf(drive / "sea-turtle-story.pdf", "Sea Turtle Story", 22)
        later = backend.add_post("sea-turtle-story", 180)
        due = backend.add_post("dino-diggers", -5)

        cal = run()  # not forced: the calendar must not need --force
        if cal.returncode != 0:
            print(cal.stdout[-2500:]); print(cal.stderr[-1500:])
        check("run succeeded", cal.returncode == 0)
        check("the due post was published", bool(named("Dino Diggers")) and named("Dino Diggers")[0]["published"])
        check("and marked published on the calendar", backend.post_status(due) == "published",
              backend.post_status(due))
        check("the future post was left alone", not named("Sea Turtle"))
        check("and is still scheduled", backend.post_status(later) == "scheduled")
        check("automatic publishing stayed off", "automatic publishing is off" in cal.stdout)

        # Auto back on and forced: the drip must still skip a book that is on
        # the calendar for later.
        backend.set_settings({"schedule": {"auto": True}})
        drip = run("--force")
        check("run succeeded", drip.returncode == 0)
        check("the drip skipped the book reserved for later", not named("Sea Turtle"))
        check("and said why", "on the calendar for later" in drip.stdout)

        missing = backend.add_post("not-in-drive-yet", -1)
        wait = run()
        check("a due post with no PDF yet does not fail the run", wait.returncode == 0)
        check("it stays scheduled, waiting for the PDF", backend.post_status(missing) == "scheduled")
        check("and says so", "waiting for the PDF" in wait.stdout)

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed  [{backend.name}]")
        if FAILED:
            print("failed: " + ", ".join(FAILED))
        return 1 if FAILED else 0
    finally:
        if backend is not None:
            backend.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
