"""Etsy, through the Open API v3 (https://developer.etsy.com).

What it does for each book: finds the listing by title (so a re-run repairs
instead of duplicating), creates a *draft* digital-download listing, uploads
the PDF and the cover images, and — only if `publish: true` — activates it.

Etsy needs three things you set up once (docs/ETSY.md walks through them):
an app key, your numeric shop id, and a refresh token from the one-time
sign-in in scripts/etsy_auth.py. Access tokens last an hour, so each run
trades the refresh token for a fresh one. Etsy hands back a new refresh token
when you do; with a database it is saved so the next run can use it.

None of this has been run against Etsy itself — the tests use a local mock
that follows the v3 documentation. Read docs/ETSY.md before turning it on.
"""

import html
import os
import re
import time

from .. import log, store
from .base import Platform, Result

API = "https://api.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
CRED = "etsy_refresh_token"

# Etsy's limits: 140-character titles, 13 tags of at most 20 characters each,
# 20 MB per digital file, 10 images.
TITLE_MAX, TAGS_MAX, TAG_LEN, FILE_MAX = 140, 13, 20, 20 * 1024 * 1024


def plain_text(markup):
    """Etsy descriptions are plain text, and ours are HTML."""
    text = re.sub(r"(?i)<\s*br\s*/?>|</\s*(p|div|h\d|ul|ol)\s*>", "\n", markup or "")
    text = re.sub(r"(?i)<\s*li[^>]*>", "• ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def clean_tags(tags):
    seen, out = set(), []
    for tag in tags or []:
        tag = re.sub(r"[^\w\s-]", "", str(tag), flags=re.UNICODE).strip()[:TAG_LEN].strip()
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return out[:TAGS_MAX]


class Etsy(Platform):
    name = "etsy"
    label = "Etsy"
    required_secrets = ("ETSY_API_KEY", "ETSY_SHOP_ID", "ETSY_REFRESH_TOKEN")

    def __init__(self, cfg, dry_run=False):
        super().__init__(cfg, dry_run)
        self.api = os.environ.get("ETSY_API_BASE", API).rstrip("/")
        self.token_url = os.environ.get("ETSY_TOKEN_URL", TOKEN_URL)
        self._access = None

    def preflight(self):
        super().preflight()
        if not self.dry_run and not self.conf.get("taxonomy_id"):
            raise log.AutopostError(
                "Etsy needs platforms.etsy.taxonomy_id in config.yml — the category "
                "number for your listings. docs/ETSY.md shows how to look it up."
            )

    # ---------------------------------------------------------------- http

    def _key(self):
        key = os.environ["ETSY_API_KEY"].strip()
        secret = os.environ.get("ETSY_SHARED_SECRET", "").strip()
        # Etsy has been moving to "keystring:shared_secret" in x-api-key.
        return f"{key}:{secret}" if secret else key

    def _call(self, method, path, *, retries=3, **kwargs):
        import requests

        url = path if path.startswith("http") else f"{self.api}{path}"
        headers = {"x-api-key": self._key()}
        if self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        for attempt in range(1, retries + 1):
            files = kwargs.get("files")
            if files:  # file handles are consumed by a failed attempt
                for handle in files.values():
                    try:
                        handle[1].seek(0)
                    except Exception:
                        pass
            resp = requests.request(method, url, headers=headers, timeout=120, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = min(int(resp.headers.get("retry-after", 2 ** attempt)), 30)
                log.warn(f"Etsy answered {resp.status_code} — retrying in {wait}s", indent=3)
                time.sleep(wait)
                continue
            break
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error") or resp.text
            except ValueError:
                detail = resp.text
            raise log.AutopostError(f"Etsy {method} {path} failed ({resp.status_code}): {str(detail)[:400]}{self._hint(resp.status_code)}")
        return resp.json() if resp.content else {}

    @staticmethod
    def _hint(status):
        return {
            401: " — the sign-in has expired or is wrong; run scripts/etsy_auth.py again.",
            403: " — the API key isn't approved yet, or the token lacks the listings_w scope.",
        }.get(status, "")

    # ---------------------------------------------------------------- auth

    def authenticate(self):
        import requests

        where = store.get()
        candidates = [t for t in (where.credential(CRED), os.environ.get("ETSY_REFRESH_TOKEN", "").strip()) if t]
        last = None
        for token in dict.fromkeys(candidates):  # the saved one first, then the secret
            resp = requests.post(
                self.token_url,
                data={"grant_type": "refresh_token", "client_id": os.environ["ETSY_API_KEY"].strip(), "refresh_token": token},
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access = data["access_token"]
                new = data.get("refresh_token")
                if new and new != token:
                    if where.set_credential(CRED, new):
                        log.info("saved Etsy's new refresh token to the database", indent=3)
                    else:
                        log.warn(
                            "Etsy issued a new refresh token but there is no database to keep it in. "
                            "Set DATABASE_URL, or re-run scripts/etsy_auth.py if the next run is refused.",
                            indent=3,
                        )
                return
            last = resp
        detail = (last.text if last is not None else "no refresh token")[:300]
        raise log.AutopostError(
            f"Etsy refused the refresh token ({detail}). Refresh tokens last 90 days; "
            "run scripts/etsy_auth.py to sign in again and update ETSY_REFRESH_TOKEN."
        )

    # ------------------------------------------------------------- listing

    def _shop(self, suffix=""):
        return f"/shops/{os.environ['ETSY_SHOP_ID'].strip()}{suffix}"

    def find_existing(self, title):
        wanted = title.strip().casefold()
        for state in ("draft", "active", "inactive"):
            offset = 0
            while True:
                page = self._call("GET", self._shop("/listings"), params={"state": state, "limit": 100, "offset": offset})
                results = page.get("results") or []
                for item in results:
                    if str(item.get("title", "")).strip().casefold() == wanted:
                        return {"id": item["listing_id"], "state": item.get("state", state), "url": item.get("url")}
                offset += 100
                if offset >= int(page.get("count") or 0) or not results:
                    break
        return None

    def listing_fields(self, book):
        listing = book.listing
        title = re.sub(r"\s+", " ", listing["title"]).strip()
        if len(title) > TITLE_MAX:
            title = title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" -–—,:") 
        fields = {
            "title": title,
            "description": plain_text(listing.get("description_html")) or listing.get("summary") or title,
            "price": max(0.20, float(listing["price"])),
            "tags": ",".join(clean_tags(listing.get("tags"))),
        }
        return fields

    def create_fields(self, book):
        conf = self.conf
        return {
            **self.listing_fields(book),
            "quantity": 999,
            "type": "download",
            "taxonomy_id": int(conf["taxonomy_id"]),
            "who_made": conf.get("who_made", "i_did"),
            "when_made": conf.get("when_made", "made_to_order"),
            "is_supply": bool(conf.get("is_supply", False)),
        }

    def _upload(self, listing_id, pdf_path, book):
        existing_files = self._call("GET", self._shop(f"/listings/{listing_id}/files")).get("results") or []
        if not existing_files:
            if os.path.getsize(pdf_path) > FILE_MAX:
                raise log.AutopostError(f"{pdf_path.name} is over Etsy's 20 MB limit for a digital file — compress it first.")
            with open(pdf_path, "rb") as handle:
                self._call("POST", self._shop(f"/listings/{listing_id}/files"),
                           files={"file": (pdf_path.name, handle, "application/pdf")}, data={"name": pdf_path.name})
            log.ok("uploaded the PDF", indent=2)
        existing_images = self._call("GET", self._shop(f"/listings/{listing_id}/images")).get("results") or []
        if not existing_images:
            for rank, key in enumerate(("cover", "thumbnail", "pin"), start=1):
                image = book.images.get(key)
                if image:
                    with open(image, "rb") as handle:
                        self._call("POST", self._shop(f"/listings/{listing_id}/images"),
                                   files={"image": (os.path.basename(str(image)), handle, "image/jpeg")}, data={"rank": rank})
            log.ok("uploaded the images", indent=2)

    # ------------------------------------------------------------- publish

    def publish(self, book, pdf_path):
        should_publish = bool(self.conf.get("publish", False))

        if self.dry_run:
            log.info("dry run — this is what would be sent to Etsy:", indent=2)
            for key, value in self.listing_fields(book).items():
                log.info(f"{key:<20} {str(value)[:110]}", indent=3)
            log.info(f"then: upload {pdf_path} and the images; {'activate' if should_publish else 'leave as a draft'}", indent=3)
            return Result("dry-run")

        self.authenticate()
        existing = self.find_existing(self.listing_fields(book)["title"])
        if existing:
            listing_id = existing["id"]
            log.info(f"found listing {listing_id} ({existing['state']}) — updating it, not creating a duplicate", indent=2)
            # Price is left alone on an existing listing: on Etsy it lives in the
            # listing's inventory, not in the fields an update takes.
            update = {k: v for k, v in self.listing_fields(book).items() if k != "price"}
            self._call("PATCH", self._shop(f"/listings/{listing_id}"), data=update)
            state = existing["state"]
        else:
            created = self._call("POST", self._shop("/listings"), data=self.create_fields(book))
            listing_id = created.get("listing_id")
            if not listing_id:
                raise log.AutopostError("Etsy created the listing but returned no id — check your shop before re-running.")
            log.ok(f"draft created: {listing_id}", indent=2)
            state = "draft"

        self._upload(listing_id, pdf_path, book)
        url = (existing or {}).get("url") or f"https://www.etsy.com/listing/{listing_id}"

        if not should_publish:
            return Result("draft", str(listing_id), url)
        if state != "active":
            self._call("PATCH", self._shop(f"/listings/{listing_id}"), data={"state": "active"})
            log.ok(f"activated: {listing_id}", indent=2)
        return Result("published", str(listing_id), url)
