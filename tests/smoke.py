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

Run it with:  python tests/smoke.py
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


def main():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print("This test needs reportlab: pip install -r requirements-dev.txt")
        return 2

    work = Path(tempfile.mkdtemp(prefix="autopost-smoke-"))
    try:
        repo = work / "repo"
        shutil.copytree(
            ROOT, repo,
            ignore=shutil.ignore_patterns(".git", "out", "__pycache__", "docs", "content"),
        )
        # Start from an empty history so the test is deterministic.
        (repo / "state").mkdir(exist_ok=True)
        (repo / "state" / "published.json").write_text('{"published": []}\n')
        (repo / "state" / "seen.json").write_text('{"files": {}}\n')
        (repo / "books.json").write_text("[]\n")

        cfg = (repo / "config.yml").read_text()
        cfg = cfg.replace("min_size_mb: 0.05", "min_size_mb: 0.0")
        cfg = cfg.replace("settle_minutes: 30", "settle_minutes: 0")
        (repo / "config.yml").write_text(cfg)

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
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)

        def run(*args):
            return subprocess.run(
                [sys.executable, str(repo / "scripts" / "publish.py"), *args],
                cwd=repo, env=env, capture_output=True, text=True,
            )

        def products():
            return json.loads(store.read_text())["products"] if store.exists() else []

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
        check("the dashboard was written", (repo / "docs" / "index.html").exists())
        check("a social pack was written",
              (repo / "content" / "social" / "ocean-buddies-coloring" / "pinterest.txt").exists())

        print("\n2. re-run is a no-op (no duplicates)")
        second = run("--force")
        check("run succeeded", second.returncode == 0)
        check("nothing new was published", "nothing new to publish" in second.stdout)
        check("still exactly one product", len(products()) == 1, f"{len(products())} products")

        print("\n3. a crashed run is repaired, not duplicated")
        state = json.loads((repo / "state" / "published.json").read_text())
        for entry in state["published"]:
            entry["status"] = "failed"
        (repo / "state" / "published.json").write_text(json.dumps(state, indent=2))
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
        cfg = (repo / "config.yml").read_text().replace('window_minutes: 120', "window_minutes: 1")
        (repo / "config.yml").write_text(cfg)
        make_pdf(drive / "dino-diggers.pdf", "Dino Diggers", 20)
        fourth = run()  # no --force, so the window applies
        check("run succeeded", fourth.returncode == 0)
        check("it held instead of publishing",
              "Holding" in fourth.stdout or "holding" in fourth.stdout or "nothing new" in fourth.stdout)
        check("no extra product was created", len(products()) == 1, f"{len(products())} products")

        print("\n5. dry run sends nothing")
        fifth = run("--force", "--dry-run")
        check("run succeeded", fifth.returncode == 0)
        check("previews were written", (repo / "out").exists())
        check("still exactly one product", len(products()) == 1, f"{len(products())} products")

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        if FAILED:
            print("failed: " + ", ".join(FAILED))
        return 1 if FAILED else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
