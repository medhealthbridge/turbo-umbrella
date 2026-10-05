#!/usr/bin/env python3
"""The Etsy platform against a local mock that follows the v3 documentation.

No network. Checks: the OAuth refresh and token rotation, a draft digital
listing with file and images, no duplicate on a re-run, activation only when
asked, plain-text/limit clean-up, and clear errors. With
AUTOPOST_TEST_DATABASE_URL set it also checks the rotated token is kept in
Postgres and preferred next time.

    python tests/etsy_test.py
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from autopost import config, log, store  # noqa: E402
from autopost.platforms import etsy as etsy_mod  # noqa: E402

failures = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


class Mock:
    def __init__(self):
        self.listings, self.files, self.images, self.calls, self.tokens = {}, {}, {}, [], []
        self.valid_refresh = {"r-old"}
        self.next_id = 100

    def handler(mock):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _read(self):
                n = int(self.headers.get("content-length") or 0)
                return self.rfile.read(n) if n else b""

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _form(self, raw):
                from urllib.parse import parse_qs
                return {k: v[0] for k, v in parse_qs(raw.decode()).items()}

            def do_ANY(self):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                raw = self._read()
                ctype = self.headers.get("content-type", "")
                body = self._form(raw) if "x-www-form-urlencoded" in ctype else {}
                mock.calls.append((self.command, u.path, body, ctype.split(";")[0], raw if "multipart" in ctype else b"", self.headers.get("x-api-key"), self.headers.get("authorization")))
                if u.path == "/token":
                    if body.get("refresh_token") not in mock.valid_refresh:
                        return self._send(400, {"error": "invalid_grant"})
                    mock.tokens.append(body["refresh_token"])
                    return self._send(200, {"access_token": "123.access", "refresh_token": "r-new", "expires_in": 3600})
                if self.headers.get("authorization") != "Bearer 123.access":
                    return self._send(401, {"error": "bad token"})
                parts = u.path.strip("/").split("/")  # shops/9/listings[/id[/files|images]]
                if parts[:3] != ["shops", "9", "listings"]:
                    return self._send(404, {"error": "no"})
                if len(parts) == 3 and self.command == "GET":
                    state = parse_qs(u.query).get("state", ["active"])[0]
                    res = [l for l in mock.listings.values() if l["state"] == state]
                    return self._send(200, {"count": len(res), "results": res})
                if len(parts) == 3 and self.command == "POST":
                    mock.next_id += 1
                    lid = mock.next_id
                    mock.listings[lid] = {"listing_id": lid, "state": "draft", "url": f"https://etsy.test/{lid}", **body}
                    return self._send(201, mock.listings[lid])
                lid = int(parts[3])
                if len(parts) == 4 and self.command == "PATCH":
                    mock.listings[lid].update(body)
                    return self._send(200, mock.listings[lid])
                kind = parts[4]
                store_ = mock.files if kind == "files" else mock.images
                if self.command == "GET":
                    return self._send(200, {"results": store_.get(lid, [])})
                store_.setdefault(lid, []).append({"n": len(store_.get(lid, [])) + 1})
                return self._send(201, {"ok": True})

            do_GET = do_POST = do_PATCH = do_PUT = do_ANY
        return H


def make_book(tmp, title="Animal Friends Coloring Book – 24 Pages"):
    pdf = tmp / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    img = tmp / "cover.jpg"
    img.write_bytes(b"\xff\xd8\xff test")
    return SimpleNamespace(
        slug="animal-friends",
        listing={"title": title, "price": "5", "summary": "s",
                 "description_html": "<p>Big &amp; bold pages</p><ul><li>24 pages</li></ul>",
                 "tags": ["coloring book", "Coloring Book", "x" * 40, "kids!!", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]},
        images={"cover": img, "thumbnail": img},
    ), pdf


def platform(conf=None):
    cfg = config.Config({"platforms": {"etsy": {"enabled": True, "taxonomy_id": 1234, **(conf or {})}}})
    return etsy_mod.Etsy(cfg)


def main():
    mock = Mock()
    server = HTTPServer(("127.0.0.1", 0), mock.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    os.environ.update(ETSY_API_KEY="keystring", ETSY_SHOP_ID="9", ETSY_REFRESH_TOKEN="r-old",
                      ETSY_API_BASE=base, ETSY_TOKEN_URL=base + "/token")
    os.environ.pop("ETSY_SHARED_SECRET", None)
    os.environ.pop("DATABASE_URL", None)
    store.reset()

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        book, pdf = make_book(tmp)

        print("helpers")
        check(etsy_mod.plain_text("<p>Big &amp; bold</p><ul><li>one</li></ul>") == "Big & bold\n• one", "HTML becomes plain text")
        tags = etsy_mod.clean_tags(book.listing["tags"])
        check(len(tags) <= 13 and all(len(x) <= 20 for x in tags), "tags capped at 13 and 20 characters")
        check(len({x.casefold() for x in tags}) == len(tags) and "kids" in tags, "tags de-duplicated and stripped of punctuation")

        print("preflight")
        try:
            cfg = config.Config({"platforms": {"etsy": {"enabled": True}}})
            etsy_mod.Etsy(cfg).preflight()
            check(False, "a missing taxonomy_id is refused")
        except log.AutopostError as exc:
            check("taxonomy_id" in str(exc), "a missing taxonomy_id is refused with the fix")
        saved = os.environ.pop("ETSY_SHOP_ID")
        try:
            platform().preflight()
            check(False, "a missing secret is refused")
        except log.AutopostError as exc:
            check("ETSY_SHOP_ID" in str(exc), "a missing secret is named")
        os.environ["ETSY_SHOP_ID"] = saved

        print("raket.ph")
        from autopost.platforms import raketph as rk
        rk.ROOT = tmp
        cfg = config.Config({"platforms": {"raketph": {"enabled": True, "price": 249}}})
        book.source_file = SimpleNamespace(id="DRIVEID123")
        r = rk.RaketPH(cfg).publish(book, pdf)
        text = (tmp / "content/raketph/animal-friends/listing.md").read_text()
        check(r["status"] == "draft" and (tmp / "content/raketph/animal-friends/cover.jpg").exists(), "a listing and cover are written")
        check("249" in text and "drive.google.com/file/d/DRIVEID123" in text and "<" not in text, "price, Drive link, plain text")
        rk.RaketPH(cfg, dry_run=True).publish(book, pdf)

        print("dry run")
        before = len(mock.calls)
        p = platform()
        p.dry_run = True
        check(p.publish(book, pdf)["status"] == "dry-run" and len(mock.calls) == before, "a dry run sends nothing")

        print("first run: draft")
        r = platform().publish(book, pdf)
        check(r["status"] == "draft" and r["product_id"] == "101", f"a draft is created ({r['status']})")
        listing = mock.listings[101]
        check(listing["type"] == "download" and listing["taxonomy_id"] == "1234", "digital download with the configured category")
        check(listing["who_made"] == "i_did" and listing["when_made"] == "made_to_order", "who_made / when_made defaults")
        check(float(listing["price"]) == 5.0 and "<" not in listing["description"], "price set, description is plain text")
        check(len(mock.files[101]) == 1 and len(mock.images[101]) == 2, "the PDF and two images were uploaded")
        check(all(c[5] == "keystring" for c in mock.calls if c[1] != "/token"), "x-api-key sent on every API call")
        check(listing["state"] == "draft", "left as a draft unless publish is on")

        print("re-run: no duplicate")
        os.environ["ETSY_REFRESH_TOKEN"] = "r-new"  # what the secret holds after rotation
        mock.valid_refresh.add("r-new")
        r = platform().publish(book, pdf)
        check(len(mock.listings) == 1 and r["product_id"] == "101", "the same listing is found, not duplicated")
        check(len(mock.files[101]) == 1 and len(mock.images[101]) == 2, "nothing is uploaded twice")
        patch = [c for c in mock.calls if c[0] == "PATCH"][-1]
        check("price" not in patch[2], "price is not sent on an update")

        print("publish: true")
        r = platform({"publish": True}).publish(book, pdf)
        check(r["status"] == "published" and mock.listings[101]["state"] == "active", "activated when asked")

        print("errors")
        mock.valid_refresh.clear()
        try:
            platform().publish(book, pdf)
            check(False, "an expired refresh token fails")
        except log.AutopostError as exc:
            check("etsy_auth.py" in str(exc), "an expired refresh token says how to fix it")
        mock.valid_refresh.add("r-new")
        long_book, _ = make_book(tmp, title="Word " * 60)
        check(len(platform().listing_fields(long_book)["title"]) <= 140, "titles are cut to Etsy's 140 characters")

        db = os.environ.get("AUTOPOST_TEST_DATABASE_URL", "").strip()
        if db:
            print("postgres: rotated token is kept")
            import psycopg
            name = "etsy_test_" + os.urandom(4).hex()
            with psycopg.connect(db, autocommit=True) as c:
                c.execute(f'create database "{name}"')
            try:
                import re
                os.environ["DATABASE_URL"] = re.sub(r"/[^/?]*(\?|$)", f"/{name}\\1", db, count=1)
                store.reset()
                mock.valid_refresh.update({"r-old", "r-new"})
                os.environ["ETSY_REFRESH_TOKEN"] = "r-old"
                mock.tokens.clear()
                platform().authenticate()
                check(store.get().credential("etsy_refresh_token") == "r-new", "Etsy's new refresh token is saved")
                platform().authenticate()
                check(mock.tokens[-1] == "r-new", "the saved token is used before the secret")
                mock.valid_refresh.discard("r-new")
                os.environ["ETSY_REFRESH_TOKEN"] = "r-old"
                platform().authenticate()
                check(mock.tokens[-1] == "r-old", "falls back to the secret if the saved token is refused")
            finally:
                store.reset()
                os.environ.pop("DATABASE_URL", None)
                with psycopg.connect(db, autocommit=True) as c:
                    c.execute(f'drop database if exists "{name}" with (force)')
        else:
            print("(postgres checks skipped: AUTOPOST_TEST_DATABASE_URL not set)")

    server.shutdown()
    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
