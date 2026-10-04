"""Gumroad, via Gumroad's own CLI.

Two things v1 got wrong and this fixes:

  * no cover was uploaded, which is the usual reason a product refuses to
    leave draft;
  * a re-run created a second product instead of finding the first. Now the
    account is searched by name first, so a half-finished run is repaired.

Every field set here is a real CLI flag. Two listing fields have no flag and
stay manual: the "Additional details" rows, and the receipt *button* text.
"""

import json
import re

from .. import log
from .base import Platform, Result


class Gumroad(Platform):
    name = "gumroad"
    label = "Gumroad"
    required_secrets = ("GUMROAD_ACCESS_TOKEN",)

    # ------------------------------------------------------------- helpers

    def gumroad(self, args, allow_fail=False):
        return self.run(["gumroad"] + args, allow_fail=allow_fail)

    @staticmethod
    def _product_id(output):
        """Pull the id out of --json output, with a regex as a last resort."""
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\b([A-Za-z0-9_=-]{8,})\b", output or "")
            return match.group(1) if match else None
        if isinstance(data, dict):
            for container in (data, data.get("product") or {}):
                for key in ("id", "product_id", "permalink"):
                    if container.get(key):
                        return str(container[key])
        return None

    def find_existing(self, name):
        """A product already in the account with this exact name."""
        out = self.gumroad(
            ["products", "list", "--all", "--json", "--non-interactive"], allow_fail=True
        )
        if not out:
            return None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            log.warn("could not parse the product list — a duplicate is possible", indent=2)
            return None
        products = data.get("products", []) if isinstance(data, dict) else data
        wanted = name.strip().casefold()
        for product in products or []:
            if str(product.get("name", "")).strip().casefold() == wanted:
                return {
                    "id": str(product.get("id") or product.get("permalink")),
                    "published": bool(product.get("published")),
                }
        return None

    def listing_flags(self, book):
        listing = book.listing
        flags = ["--name", listing["title"], "--price", str(listing["price"])]
        if listing.get("description_html"):
            flags += ["--description", listing["description_html"]]
        if listing.get("summary"):
            flags += ["--custom-summary", listing["summary"]]
        if listing.get("receipt"):
            flags += ["--custom-receipt", listing["receipt"]]
        if listing.get("refund_period"):
            flags += ["--refund-period", str(listing["refund_period"])]
        flags += ["--custom-permalink", book.slug]
        for tag in listing.get("tags", []):
            flags += ["--tag", tag]
        return flags

    def image_flags(self, book):
        flags = []
        if book.images.get("cover"):
            flags += ["--cover-image", str(book.images["cover"])]
        if book.images.get("thumbnail"):
            flags += ["--thumbnail", str(book.images["thumbnail"])]
        return flags

    def product_url(self, product_id):
        out = self.gumroad(["products", "view", product_id, "--json"], allow_fail=True)
        if out:
            try:
                data = json.loads(out)
                url = data.get("short_url") or data.get("url") or (data.get("product") or {}).get("short_url")
                if url:
                    return url
            except json.JSONDecodeError:
                pass
        out = self.gumroad(["products", "url", product_id], allow_fail=True)
        if out and out.strip().startswith("http"):
            return out.strip().splitlines()[0]
        return None

    # ------------------------------------------------------------- publish

    def publish(self, book, pdf_path):
        should_publish = bool(self.conf.get("publish", True))

        if self.dry_run:
            log.info("dry run — the listing below is what would be sent:", indent=2)
            preview = self.listing_flags(book) + self.image_flags(book) + ["--file", str(pdf_path)]
            for flag, value in zip(*[iter(preview)] * 2):
                log.info(f"{flag:<20} {str(value)[:110]}", indent=3)
            log.info(
                f"then: gumroad products {'publish' if should_publish else 'update'} <id>",
                indent=3,
            )
            return Result("dry-run")

        existing = self.find_existing(book.listing["title"])

        if existing:
            product_id = existing["id"]
            log.info(
                f"found {product_id} already in the account "
                f"(published={existing['published']}) — updating it, not creating a duplicate",
                indent=2,
            )
            self.gumroad(
                ["products", "update", product_id]
                + self.listing_flags(book)
                + self.image_flags(book)
                + ["--non-interactive"]
            )
            already_live = existing["published"]
        else:
            out = self.gumroad(
                ["products", "create"]
                + self.listing_flags(book)
                + ["--file", str(pdf_path)]
                + self.image_flags(book)
                + ["--json", "--non-interactive"]
            )
            product_id = self._product_id(out)
            if not product_id:
                raise log.AutopostError(
                    "the product was created but its id could not be read from the "
                    "CLI output — check Gumroad before re-running"
                )
            log.ok(f"draft created: {product_id}", indent=2)
            already_live = False

        if not should_publish:
            return Result("draft", product_id, self.product_url(product_id))

        if not already_live:
            self.gumroad(["products", "publish", product_id, "--non-interactive"])
            log.ok(f"published: {product_id}", indent=2)

        return Result("published", product_id, self.product_url(product_id))
