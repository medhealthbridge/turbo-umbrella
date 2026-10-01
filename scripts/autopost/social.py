"""Ready-to-post social packs.

Deliberately not an API integration by default. Every social API worth
having (Instagram, TikTok, Pinterest) needs a reviewed app, a business
account and a token that expires — weeks of setup before the first post.

So instead, every publish writes a folder you can post from in about
ninety seconds:

    content/social/<slug>/
      pinterest.txt     caption + hashtags, ready to paste
      instagram.txt
      ...
      pin.jpg           the right image for each network
      social.jpg
      pack.json         the same thing as data, for a real integration later

The packs are committed, so they are on your phone through the GitHub app
too. When you are ready to automate one network, set `auto_post: true` and
implement its `post()` — the copy and the images are already here.
"""

import json
import shutil
from pathlib import Path

from . import log
from .config import ROOT


def write_pack(book, cfg):
    """Write one book's social folder. Returns its repo-relative path."""
    if not cfg.get("social.enabled", True):
        return None
    packs = (book.listing or {}).get("social") or {}
    if not packs:
        return None

    out_dir = ROOT / cfg.get("social.output_dir", "content/social") / book.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    url = None
    for platform in (book.listing.get("results") or {}).values():
        if isinstance(platform, dict) and platform.get("url"):
            url = platform["url"]
            break

    index = ["# " + book.listing["title"], ""]
    if url:
        index += [f"Live at: {url}", ""]
    index += ["Paste each caption into the matching app. Images are in this folder.", ""]

    for network, pack in sorted(packs.items()):
        text = pack["text"]
        if url:
            text = f"{text}\n\n{url}"
        (out_dir / f"{network}.txt").write_text(text + "\n", encoding="utf-8")

        image = book.images.get(pack.get("image", "social"))
        if image and Path(image).exists():
            target = out_dir / f"{pack['image']}.jpg"
            if not target.exists():
                shutil.copyfile(image, target)
            pack["image_file"] = target.name

        index.append(f"- **{network}** → `{network}.txt` ({len(text)} chars)")

    (out_dir / "pack.json").write_text(
        json.dumps(
            {
                "slug": book.slug,
                "title": book.listing["title"],
                "url": url,
                "networks": packs,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    relative = out_dir.relative_to(ROOT).as_posix()
    log.ok(f"social pack for {len(packs)} network(s): {relative}/", indent=2)

    if cfg.get("social.auto_post", False):
        log.warn(
            "social.auto_post is on, but no network has an implementation yet — "
            "the packs were written for you to post by hand",
            indent=2,
        )
    return relative
