"""Turn a file in a Drive folder into a book we can list.

A brand-new PDF needs no configuration at all: the filename becomes the
slug, the title is reconstructed from it, the price comes from the pricing
rules, and the real page count is read out of the PDF itself.

books.json stays supported and always wins — put an entry there (matched by
`slug`) to override any field for one specific book.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import log
from .config import ROOT

BOOKS_FILE = ROOT / "books.json"

# Words that should not be title-cased in a reconstructed title.
SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of",
    "on", "or", "the", "to", "with", "yet",
}

# Filename noise that carries no meaning in a title.
NOISE = re.compile(
    r"\b(final|v\d+|ver\d+|rev\d+|copy|new|updated?|export|print|ready|"
    r"letter|a4|us|\d{6,8}|\d{4}-\d{2}-\d{2})\b",
    re.I,
)

# Topic → the phrasing and keywords that sell it. Used for copy and tags.
THEMES = {
    "coloring": {
        "match": ["coloring", "colouring", "color book"],
        "kind": "coloring book",
        "hook": "big, bold pages little hands can actually finish",
        "tags": ["coloring book", "coloring pages", "screen free"],
    },
    "storybook": {
        "match": ["storybook", "story", "tale", "adventure"],
        "kind": "read-aloud storybook",
        "hook": "a bedtime story they will ask for again tomorrow",
        "tags": ["storybook", "read aloud", "bedtime story"],
    },
    "growth-mindset": {
        "match": ["growth mindset", "yet", "confidence", "resilience"],
        "kind": "growth-mindset storybook",
        "hook": "turns \"I can't do it\" into \"not yet\"",
        "tags": ["growth mindset", "social emotional learning", "confidence"],
    },
    "worksheet": {
        "match": ["worksheet", "practice", "tracing", "handwriting", "math"],
        "kind": "worksheet pack",
        "hook": "quiet, independent practice that actually holds their attention",
        "tags": ["worksheets", "homeschool", "learning"],
    },
    "planner": {
        "match": ["planner", "tracker", "chart", "routine", "checklist"],
        "kind": "printable planner",
        "hook": "the calm-morning routine, on one page",
        "tags": ["planner", "printable planner", "organization"],
    },
    "bundle": {
        "match": ["bundle", "pack", "collection", "mega"],
        "kind": "bundle",
        "hook": "everything in one download, at a fraction of the single price",
        "tags": ["bundle", "value pack"],
    },
}

DEFAULT_THEME = {
    "kind": "printable activity book",
    "hook": "screen-free time that keeps them busy and proud",
    "tags": ["kids activity", "printable"],
}


@dataclass
class Book:
    slug: str
    name: str
    price: str
    description: str = ""
    tags: list = field(default_factory=list)
    summary: str = ""
    link: str = ""
    pages: int = 0
    theme: str = ""
    kind: str = ""
    hook: str = ""
    source_file: object = None
    overridden: bool = False
    #: The name with marketplace boilerplate stripped, for images and captions.
    display_title: str = ""
    # Filled in later by the marketing and image stages.
    listing: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)


def slugify(text):
    text = re.sub(r"[''']", "", str(text))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return re.sub(r"-{2,}", "-", text).strip("-")


def title_from_filename(stem):
    """`mermaid-friends-coloring-3` → `Mermaid Friends Coloring 3`.

    A filename that already reads like a title (has spaces and capitals) is
    left alone apart from stripping version noise.
    """
    cleaned = NOISE.sub(" ", stem.replace("_", " ").replace("-", " "))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—")
    if not cleaned:
        return stem

    if re.search(r"[a-z]", stem) and re.search(r"[A-Z]", stem) and " " in stem:
        return cleaned  # already human-written

    words = []
    for index, word in enumerate(cleaned.split(" ")):
        lowered = word.lower()
        if index > 0 and lowered in SMALL_WORDS:
            words.append(lowered)
        elif word.isupper() and len(word) <= 4:
            words.append(word)  # keep acronyms like PDF, USA
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def detect_theme(*texts):
    """Best-matching theme for a book, by keyword hits across its text."""
    haystack = " ".join(t for t in texts if t).lower()
    best, best_score = None, 0
    for name, theme in THEMES.items():
        score = sum(1 for kw in theme["match"] if kw in haystack)
        if score > best_score:
            best, best_score = name, score
    if not best:
        return "", DEFAULT_THEME
    return best, THEMES[best]


# ----------------------------------------------------------------- the PDF


def inspect_pdf(path):
    """Page count and a little first-page text, straight from the file.

    The page count is what makes the listing title honest — v1 hard-coded it
    in books.json and it drifted.
    """
    info = {"pages": 0, "text": "", "title": ""}
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        info["pages"] = len(reader.pages)
        meta = reader.metadata or {}
        info["title"] = (meta.get("/Title") or "").strip()
        chunks = []
        for page in reader.pages[:2]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                pass
        info["text"] = re.sub(r"\s+", " ", " ".join(chunks)).strip()[:1200]
    except Exception as exc:
        log.warn(f"could not read the PDF's structure ({exc}) — continuing without page count")
    return info


# -------------------------------------------------------------- books.json


def load_overrides():
    """books.json, keyed by slug. Missing or broken → no overrides."""
    if not BOOKS_FILE.exists():
        return {}
    try:
        data = json.loads(BOOKS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warn(f"books.json is not valid JSON ({exc}) — ignoring all overrides")
        return {}
    if not isinstance(data, list):
        log.warn("books.json should be a list of books — ignoring it")
        return {}
    return {b["slug"]: b for b in data if isinstance(b, dict) and b.get("slug")}


def build(source_file, cfg, overrides, pdf_path=None):
    """Assemble a Book from a source file, its overrides and its PDF."""
    slug = slugify(source_file.stem)
    override = overrides.get(slug, {})

    pdf_info = inspect_pdf(pdf_path) if pdf_path else {}
    name = override.get("name") or title_from_filename(source_file.stem)

    theme_name, theme = detect_theme(name, override.get("description", ""), pdf_info.get("text", ""))

    return Book(
        slug=slug,
        name=name,
        price=str(override.get("price") or cfg.price_for(name, source_file.name, theme_name)),
        description=override.get("description", ""),
        tags=list(override.get("tags") or []),
        summary=override.get("summary", ""),
        link=override.get("link", ""),
        pages=int(override.get("pages") or pdf_info.get("pages") or 0),
        theme=theme_name,
        kind=theme["kind"],
        hook=theme["hook"],
        source_file=source_file,
        overridden=bool(override),
    )


def from_override(slug, cfg, overrides):
    """A book that exists only in books.json (has a `link:`, no Drive file)."""
    override = overrides.get(slug)
    if not override:
        return None
    theme_name, theme = detect_theme(override.get("name", ""), override.get("description", ""))
    return Book(
        slug=slug,
        name=override.get("name") or title_from_filename(slug),
        price=str(override.get("price") or cfg.price_for(override.get("name", ""), slug)),
        description=override.get("description", ""),
        tags=list(override.get("tags") or []),
        summary=override.get("summary", ""),
        link=override.get("link", ""),
        pages=int(override.get("pages") or 0),
        theme=theme_name,
        kind=theme["kind"],
        hook=theme["hook"],
        source_file=type("Stub", (), {"id": f"books.json:{slug}", "name": f"{slug}.pdf", "stem": slug, "size": 0, "path": None})(),
        overridden=True,
    )
