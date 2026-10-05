"""Raket.ph — a ready-to-paste listing, because Raket.ph has no public API.

Raket.ph sellers add products through its dashboard (name, description, image,
a link to the file, price). I found no API or import for that, so this platform
writes everything you would type into content/raketph/<slug>/ and reports the
book as a draft. Open the folder, copy across, publish. The workflow commits
it like the social packs.

Raket.ph's own rules and fees are not checked here; see docs/RAKETPH.md.
"""

import shutil

from .. import log
from ..config import ROOT
from .base import Platform, Result
from .etsy import plain_text


class RaketPH(Platform):
    name = "raketph"
    label = "Raket.ph"
    required_secrets = ()

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
            "Copy each part into Raket.ph → Add product.",
            "",
            "## Name", listing["title"], "",
            f"## Price ({currency})", str(price), "",
            "## Description", plain_text(listing.get("description_html")) or listing.get("summary", ""), "",
            "## Image", "cover.jpg (in this folder)", "",
            "## File link",
            link or "(none — upload the PDF, or share it from Drive and paste the link)",
            "Make sure the Drive link is set to “Anyone with the link can view”.",
            "",
        ])

    def publish(self, book, pdf_path):
        out = ROOT / self.conf.get("output_dir", "content/raketph") / book.slug
        if self.dry_run:
            log.info(f"dry run — would write the listing to {out.relative_to(ROOT)}/listing.md", indent=2)
            return Result("dry-run")
        out.mkdir(parents=True, exist_ok=True)
        (out / "listing.md").write_text(self.render(book), encoding="utf-8")
        cover = book.images.get("cover")
        if cover:
            shutil.copyfile(cover, out / "cover.jpg")
        log.ok(f"listing ready to paste: {out.relative_to(ROOT)}/listing.md", indent=2)
        return Result("draft", book.slug, None, note="Paste it into Raket.ph by hand — no API.")
