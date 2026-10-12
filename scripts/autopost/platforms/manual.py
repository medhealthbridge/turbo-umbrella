"""Platforms with no product-creation API: write a ready-to-paste listing.

Used by Raket.ph, Payhip and Lemon Squeezy. Each writes everything you would
type into content/<platform>/<slug>/ and reports the book as a draft. Open the
folder, copy across, publish. The workflow commits it like the social packs.
Each store's own rules and fees are not checked here.
"""

import shutil

from .. import log
from ..config import ROOT
from .base import Platform, Result
from .etsy import plain_text


class ManualListing(Platform):
    """Subclass and set name, label and (optionally) hosts_file."""

    required_secrets = ()
    #: True when the store wants the PDF uploaded to it rather than linked.
    hosts_file = False

    def drive_link(self, book):
        file_id = str(getattr(book.source_file, "id", "") or "")
        if not file_id or ":" in file_id:  # books.json stubs have no Drive file
            return book.listing.get("link", "")
        return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    def render(self, book):
        listing = book.listing
        link = self.drive_link(book)
        price = self.conf.get("price") or listing["price"]
        currency = self.conf.get("currency", "PHP")
        return "\n".join([
            f"# {listing['title']}",
            "",
            "Copy each part into the store's Add product form.",
            "",
            "## Name", listing["title"], "",
            f"## Price ({currency})", str(price), "",
            "## Description", plain_text(listing.get("description_html")) or listing.get("summary", ""), "",
            "## Image", "cover.jpg (in this folder)", "",
            *(["## File", "Upload the PDF (the same one that is in your Drive folder).", ""] if self.hosts_file else [
                "## File link",
                link or "(none — upload the PDF, or share it from Drive and paste the link)",
                "Make sure the Drive link is set to “Anyone with the link can view”.",
                "",
            ]),
        ])

    def publish(self, book, pdf_path):
        out = ROOT / self.conf.get("output_dir", f"content/{self.name}") / book.slug
        if self.dry_run:
            log.info(f"dry run — would write the listing to {out.relative_to(ROOT)}/listing.md", indent=2)
            return Result("dry-run")
        out.mkdir(parents=True, exist_ok=True)
        (out / "listing.md").write_text(self.render(book), encoding="utf-8")
        cover = book.images.get("cover")
        if cover:
            shutil.copyfile(cover, out / "cover.jpg")
        log.ok(f"listing ready to paste: {out.relative_to(ROOT)}/listing.md", indent=2)
        return Result("prepared", book.slug, None, note=f"Paste it into {self.label} by hand — no API.")
