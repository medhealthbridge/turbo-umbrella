"""Write the listing.

Digital printables sell on four things, in this order:

  1. a title a buyer would actually search for
  2. a first line that names the problem it solves
  3. a scannable "what you get" block (pages, format, contents)
  4. reassurance — instant download, print forever, refund

So that is exactly what gets generated, every time, from the book's own
title, theme and real page count. No API key is needed: the generator is
deterministic and always produces a complete listing.

If an ANTHROPIC_API_KEY secret is present, Claude additionally rewrites the
hook and the social captions in the shop's voice. That is a bonus layer —
if the call fails for any reason the deterministic copy is what ships.
"""

import html
import json
import os
import re

from . import log

MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"


# ------------------------------------------------------------------ helpers


def _esc(text):
    return html.escape(str(text), quote=False)


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _strip_html(text):
    return _clean(re.sub(r"<[^>]+>", " ", str(text or "")))


def _truncate(text, limit):
    """Cut to `limit` without leaving half a word behind."""
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:—-") + "…"


def _dedupe(items, limit=None):
    seen, out = set(), []
    for item in items:
        key = _clean(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_clean(item))
        if limit and len(out) >= limit:
            break
    return out


# --------------------------------------------------------------- the pieces


def build_title(book, cfg):
    """`<Name> (Printable PDF, 26 Pages)` — without ever doubling a suffix
    the hand-written name already carries, and without repeating a page
    count the PDF disagrees with."""
    name = _clean(book.name)
    limit = int(cfg.get("marketing.max_title_length", 120))

    # A hand-written title often claims a page count that has since drifted
    # from the file. The PDF is the source of truth.
    if book.pages:
        def _fix(match):
            claimed = int(match.group(1))
            if claimed != book.pages:
                log.info(
                    f"title claims {claimed} pages but the PDF has {book.pages} — correcting it",
                    indent=2,
                )
            return f"{book.pages} {match.group(2)}"

        name = re.sub(r"\b(\d{1,3})[\s-]+(pages?)\b", _fix, name, flags=re.I)

    already_tagged = re.search(r"\((?:printable|instant|digital)[^)]*\)\s*$", name, re.I)
    suffix = ""
    if not already_tagged:
        template = cfg.get("marketing.title_suffix", "") or ""
        if template and "{pages}" in template and not book.pages:
            template = re.sub(r",?\s*\{pages\}\s*Pages?", "", template)
        suffix = template.format(pages=book.pages or "")
        suffix = re.sub(r"\s{2,}", " ", suffix).replace("( ", "(").replace(" )", ")")
        suffix = "" if suffix.strip() in ("()", "") else suffix

    if len(name) + len(suffix) > limit:
        name = _truncate(name, max(20, limit - len(suffix)))
    return (name + suffix).strip()


def short_title(book):
    """The title with the marketplace boilerplate stripped off.

    `Dino Diggers Coloring Book for Kids Ages 3-5 (Printable PDF, 26 Pages)`
    → `Dino Diggers Coloring Book for Kids Ages 3-5`

    Used on the pin and social images, and in captions, where the format and
    page count already appear on their own line and repeating them just eats
    the space the actual name needs.
    """
    name = _clean(book.name)
    name = re.sub(r"\s*[\(\[][^)\]]*(?:printable|pdf|pages?|instant|digital)[^)\]]*[\)\]]", "", name, flags=re.I)
    name = re.sub(r"\s*[—–-]\s*(?:printable|instant download|digital download)\b.*$", "", name, flags=re.I)
    return _clean(name) or _clean(book.name)


def build_tags(book, cfg):
    """The book's own tags, plus its theme's, plus the shop's base tags."""
    from .metadata import DEFAULT_THEME, THEMES

    theme = THEMES.get(book.theme, DEFAULT_THEME)
    candidates = list(book.tags) + list(theme["tags"]) + list(cfg.get("marketing.base_tags", []))
    # Gumroad rejects very long tags and ignores one-character ones.
    usable = [t for t in candidates if 2 <= len(_clean(t)) <= 20]
    return _dedupe(usable, int(cfg.get("marketing.max_tags", 10)))


def build_summary(book, cfg):
    """One sentence, under the marketplace's limit. Shown in search results,
    so it leads with the concrete thing being bought."""
    limit = int(cfg.get("marketing.max_summary_length", 190))
    if book.summary:
        return _truncate(book.summary, limit)

    pages = f"{book.pages}-page " if book.pages else ""
    lead = f"{pages}printable {book.kind} — {book.hook}."

    # Top it up from the book's own words, never from the generated body —
    # otherwise the summary just repeats the hook back.
    story = _strip_html(book.description)
    if story and len(lead) < limit * 0.65:
        match = re.match(r"(.{30,}?[.!?])(\s|$)", story)
        extra = match.group(1) if match else story
        if extra.lower().rstrip(".") not in lead.lower():
            lead = _truncate(f"{lead} {extra}", limit)
    return _truncate(lead, limit)


def build_description(book, cfg):
    """The listing body, as HTML.

    A hand-written `description` in books.json is never thrown away — it is
    used as the story paragraph and the generated structure is built around
    it.
    """
    pages = book.pages
    audience = cfg.get("marketing.audience", "parents and teachers")
    guarantees = cfg.get("marketing.guarantees", []) or []
    promise = cfg.get("marketing.brand_promise", "")

    story = _strip_html(book.description)
    if story and pages:
        # Hand-written copy often carries a stale "28-page PDF". Correct the
        # unambiguous forms so the body never contradicts the What-you-get
        # block right below it.
        story = re.sub(
            r"\b(\d{1,3})([\s-]+)(pages?)\b",
            lambda m: f"{pages}{m.group(2)}{m.group(3)}",
            story,
            flags=re.I,
        )
    if not story:
        story = (
            f"{_clean(book.name)} is a printable {book.kind} made for "
            f"{audience} — {book.hook}. Print it once for a rainy afternoon, "
            "or print it again every time it gets used up."
        )

    what_you_get = []
    if pages:
        what_you_get.append(f"<strong>{pages} pages</strong> in one PDF, ready to print")
    what_you_get += [
        "Sized for <strong>US Letter</strong> — and it prints fine on A4",
        "Black-and-white friendly, so it costs pennies at home",
        "Yours forever: print it again for the next child, the next class, the next rainy day",
    ]

    perfect_for = [
        "rainy days, long flights and restaurant waits",
        "classroom early-finishers and quiet time",
        "a screen-free birthday or party-bag gift",
        "homeschool morning baskets",
    ]

    parts = [
        f"<p><strong>{_esc(book.hook.capitalize())}.</strong></p>",
        f"<p>{_esc(story)}</p>",
        "<h3>What you get</h3><ul>" + "".join(f"<li>{item}</li>" for item in what_you_get) + "</ul>",
    ]
    if guarantees:
        parts.append(
            "<h3>Why you can buy this with confidence</h3><ul>"
            + "".join(f"<li>{_esc(g)}</li>" for g in guarantees)
            + "</ul>"
        )
    parts.append(
        "<h3>Perfect for</h3><ul>"
        + "".join(f"<li>{_esc(item)}</li>" for item in perfect_for)
        + "</ul>"
    )
    parts.append(
        "<h3>How to print</h3><p>Download the PDF, open it in any free PDF "
        "reader, and print the pages you want. Choose <em>Fit to page</em> "
        "and, for coloring pages, plain printer paper is all you need.</p>"
    )
    parts.append(
        "<p><em>This is a digital download. Nothing is shipped — the PDF is "
        "yours the moment you pay.</em></p>"
    )
    if promise:
        parts.append(f"<p>{_esc(promise)}</p>")
    return "".join(parts)


# ------------------------------------------------------------------- social


def build_social(book, cfg, listing):
    """A ready-to-post pack per enabled network.

    Each network gets copy written to its own strengths — Pinterest wants a
    keyword-rich sentence, X wants one line, Instagram wants a hook plus a
    call to action — rather than the same blurb pasted five times.
    """
    packs = {}
    name = short_title(book)
    pages = f"{book.pages} pages" if book.pages else "instant download"
    hook = book.hook

    templates = {
        "pinterest": (
            f"{name} — {pages}, printable PDF. {hook.capitalize()}. "
            f"Print it at home as many times as you like, for one child or a whole class. "
            f"Instant download, no shipping, no waiting."
        ),
        "instagram": (
            f"{hook.capitalize()} ✨\n\n"
            f"{name} — {pages} of printable, screen-free fun you can print tonight.\n\n"
            f"Print it once, print it a hundred times. It's yours forever.\n\n"
            f"Tap the link in bio to grab the PDF 👆"
        ),
        "facebook": (
            f"New in the shop: {name}.\n\n"
            f"{pages.capitalize()} of printable, screen-free activity — {hook}. "
            f"Download it, print it at home, and print it again whenever it gets used up.\n\n"
            f"Instant download, 30-day refund, no shipping."
        ),
        "x": f"New: {name} — {pages}, printable PDF. {hook.capitalize()}. Instant download.",
        "tiktok": (
            f"POV: it's raining and you have {pages} of printable activities ready to go 🖍️\n\n"
            f"{name} — print it at home, print it forever. Link in bio."
        ),
    }

    for network in cfg.enabled_networks():
        conf = cfg.section(f"social.networks.{network}")
        caption = templates.get(network, f"{name} — printable PDF. {hook.capitalize()}.")
        tags = _dedupe(list(conf.get("hashtags", []) or []))
        limit = int(conf.get("max_length", 2000))
        # Trim the caption, not the hashtags — the hashtags are the reach.
        room = limit - (len(" ".join(tags)) + 2)
        body = _truncate(caption, max(40, room))
        packs[network] = {
            "caption": body,
            "hashtags": tags,
            "text": f"{body}\n\n{' '.join(tags)}".strip(),
            "image": conf.get("image", "social"),
            "url_placeholder": True,
        }
    return packs


# ------------------------------------------------------- optional AI polish


def _claude_polish(book, cfg, listing):
    """Ask Claude for a sharper hook, summary and captions. Best-effort."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    import requests

    networks = cfg.enabled_networks()
    prompt = f"""You write listing copy for a small shop selling printable PDF activity books for kids.

Book title: {book.name}
Type: {book.kind}
Pages: {book.pages or "unknown"}
Audience: {cfg.get("marketing.audience")}
Existing description: {_strip_html(book.description) or "(none)"}

Write marketing copy that is warm, concrete and free of hype. No em-dash-heavy
prose, no "unleash", no "dive into", no exclamation marks in the summary.
Name the real moment a parent would use this.

Return ONLY a JSON object with these keys:
  "hook": one sentence, under 90 characters, the benefit not the feature
  "summary": one sentence under {cfg.get("marketing.max_summary_length", 190)} characters
  "tags": 6-10 lowercase search phrases a buyer would type, as an array
  "captions": an object with a caption for each of: {", ".join(networks) or "none"}
"""

    try:
        resp = requests.post(
            API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = "".join(
            block.get("text", "") for block in resp.json().get("content", [])
        )
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("no JSON object in the reply")
        return json.loads(match.group(0))
    except Exception as exc:
        log.warn(f"Claude polish skipped ({exc}) — using the generated copy")
        return None


# -------------------------------------------------------------------- entry


def compose(book, cfg, use_ai=True):
    """Build the full listing for one book. Sets `book.listing` and returns it."""
    description = build_description(book, cfg)
    listing = {
        "title": build_title(book, cfg),
        "summary": build_summary(book, cfg),
        "description_html": description,
        "tags": build_tags(book, cfg),
        "receipt": _clean(cfg.get("marketing.receipt", "")) or "Thank you for your purchase!",
        "refund_period": str(cfg.get("marketing.refund_period", "30")),
        "price": book.price,
        "pages": book.pages,
        "ai_polished": False,
    }
    listing["social"] = build_social(book, cfg, listing)

    if use_ai:
        polish = _claude_polish(book, cfg, listing)
        if polish:
            if polish.get("hook"):
                book.hook = _clean(polish["hook"]).rstrip(".")
                listing["description_html"] = build_description(book, cfg)
            if polish.get("summary"):
                listing["summary"] = _truncate(
                    polish["summary"], int(cfg.get("marketing.max_summary_length", 190))
                )
            if isinstance(polish.get("tags"), list):
                listing["tags"] = _dedupe(
                    [t for t in polish["tags"] if 2 <= len(_clean(t)) <= 20]
                    + listing["tags"],
                    int(cfg.get("marketing.max_tags", 10)),
                )
            captions = polish.get("captions")
            if isinstance(captions, dict):
                for network, pack in listing["social"].items():
                    caption = captions.get(network)
                    if not caption:
                        continue
                    limit = int(cfg.get(f"social.networks.{network}.max_length", 2000))
                    room = limit - (len(" ".join(pack["hashtags"])) + 2)
                    pack["caption"] = _truncate(caption, max(40, room))
                    pack["text"] = f"{pack['caption']}\n\n{' '.join(pack['hashtags'])}".strip()
            listing["ai_polished"] = True
            log.ok("listing copy polished by Claude")

    book.display_title = short_title(book)
    book.listing = listing
    return listing
