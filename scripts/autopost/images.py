"""Cover art, rendered from page 1 of the PDF.

A listing with no cover image very often refuses to leave draft, which is
the most likely reason the first Gumroad run stalled. So every publish now
builds its own images and uploads them:

  cover      16:9, the product page hero
  thumbnail  1:1, search results and grids
  pin        2:3 with a title band — Pinterest, the best traffic source
             a printables shop has
  social     1:1 with a title band — Instagram, Facebook, X

Rendering uses pypdfium2, a pip wheel with no system dependencies, so CI
needs no apt-get step.
"""

from pathlib import Path

from . import log

# Fonts present on the GitHub Actions ubuntu runners, best first.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

INK = (32, 34, 46)
MUTED = (110, 114, 130)


def _font(size):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_first_page(pdf_path, scale=2.5):
    """Page 1 of the PDF as a PIL image, or None if it cannot be rendered."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(pdf_path))
        if len(document) == 0:
            log.warn("the PDF has no pages — skipping cover art")
            return None
        image = document[0].render(scale=scale).to_pil().convert("RGB")
        document.close()
        return image
    except Exception as exc:
        log.warn(f"could not render page 1 ({exc}) — continuing without cover art")
        return None


def _backdrop(page, size, blur=30):
    """A blurred, lightened crop of the page, so any page shape fills the
    frame without letterboxing."""
    from PIL import Image, ImageFilter, ImageOps

    canvas = ImageOps.fit(page, size, method=Image.LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    return Image.blend(canvas, Image.new("RGB", size, "white"), 0.45)


def _paste_centred(canvas, page, box, top=None):
    from PIL import Image, ImageDraw, ImageOps

    fitted = ImageOps.contain(page, box, method=Image.LANCZOS)
    x = (canvas.width - fitted.width) // 2
    y = top if top is not None else (canvas.height - fitted.height) // 2

    # A soft edge so a white page does not vanish into a white backdrop.
    shadow = Image.new("RGB", (fitted.width + 8, fitted.height + 8), (218, 220, 228))
    canvas.paste(shadow, (x - 4, y - 4))
    canvas.paste(fitted, (x, y))
    ImageDraw.Draw(canvas).rectangle(
        [x - 1, y - 1, x + fitted.width, y + fitted.height], outline=(205, 208, 218)
    )
    return fitted, (x, y)


def _wrap(draw, text, font, max_width, max_lines=3):
    words, lines, current = str(text).split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if lines and len(words) > sum(len(line.split()) for line in lines):
        lines[-1] = lines[-1].rstrip(" ,.–—-") + "…"
    return lines


def _with_caption(page, size, title, kicker):
    """A tall or square card: the page on top, a title band underneath."""
    from PIL import Image, ImageDraw

    width, height = size
    band = int(height * 0.26)
    canvas = _backdrop(page, size)
    canvas.paste(Image.new("RGB", (width, band), "white"), (0, height - band))

    pad = int(width * 0.06)
    _paste_centred(canvas, page, (width - pad * 2, height - band - pad * 2), top=pad)

    draw = ImageDraw.Draw(canvas)
    draw.line([(0, height - band), (width, height - band)], fill=(228, 230, 238), width=2)

    title_font = _font(max(20, int(width * 0.062)))
    kicker_font = _font(max(14, int(width * 0.036)))

    lines = _wrap(draw, title, title_font, width - pad * 2, max_lines=3)
    line_height = int(title_font.size * 1.22)
    gap = int(kicker_font.size * 0.5)
    block = len(lines) * line_height + gap + int(kicker_font.size * 1.3)
    y = height - band + max(8, (band - block) // 2)

    for line in lines:
        draw.text(((width - draw.textlength(line, font=title_font)) / 2, y), line,
                  font=title_font, fill=INK)
        y += line_height
    y += gap
    draw.text(((width - draw.textlength(kicker, font=kicker_font)) / 2, y), kicker,
              font=kicker_font, fill=MUTED)
    return canvas


def _supplied(book, supplied, out_dir, kind, size, quality):
    """Fit a user-supplied image to the size this slot needs."""
    source = (supplied or {}).get(kind)
    if not source or not Path(source).exists():
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(source) as image:
            fitted = ImageOps.fit(image.convert("RGB"), size, method=Image.LANCZOS)
        target = Path(out_dir) / f"{book.slug}-{kind}.jpg"
        fitted.save(target, quality=quality)
        log.ok(f"{kind}: using your own {Path(source).name}", indent=2)
        return target
    except Exception as exc:
        log.warn(f"could not use {Path(source).name} as the {kind} ({exc}) — rendering one instead", indent=2)
        return None


def build(book, pdf_path, out_dir, cfg, supplied=None):
    """Render every enabled image for one book. Returns {kind: Path}.

    `supplied` is {kind: Path} of artwork the user dropped in the folder
    next to the PDF; anything in it wins over the rendered version.
    """
    if not cfg.get("images.enabled", True):
        return {}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    quality = int(cfg.get("images.quality", 90))
    made = {}

    for kind in ("cover", "thumbnail", "pin", "social"):
        own = _supplied(book, supplied, out_dir, kind, cfg.image_size(kind), quality)
        if own:
            made[kind] = own
    if len(made) == 4:
        book.images = made
        return made  # nothing left to render, so the PDF is never opened

    page = render_first_page(pdf_path)
    if page is None:
        if made:
            book.images = made
            return made
        return {}

    kicker = " · ".join(
        part for part in ["Printable PDF", f"{book.pages} pages" if book.pages else "", "Instant download"] if part
    )

    try:
        from PIL import Image

        # cover — 16:9, page floated on a blurred backdrop
        if "cover" not in made:
            size = cfg.image_size("cover")
            cover = _backdrop(page, size)
            _paste_centred(cover, page, (int(size[0] * 0.88), int(size[1] * 0.9)))
            made["cover"] = out_dir / f"{book.slug}-cover.jpg"
            cover.save(made["cover"], quality=quality)

        # thumbnail — 1:1, page on plain white so it stays legible when tiny
        if "thumbnail" not in made:
            size = cfg.image_size("thumbnail")
            thumb = Image.new("RGB", size, "white")
            _paste_centred(thumb, page, (int(size[0] * 0.92), int(size[1] * 0.92)))
            made["thumbnail"] = out_dir / f"{book.slug}-thumbnail.jpg"
            thumb.save(made["thumbnail"], quality=quality)

        # The parenthetical "(Printable PDF, 26 Pages)" is already the kicker
        # line, so the band shows the book's actual name and nothing else.
        caption = book.display_title or book.name

        # pin — 2:3 with a title band
        if "pin" not in made:
            made["pin"] = out_dir / f"{book.slug}-pin.jpg"
            _with_caption(page, cfg.image_size("pin"), caption, kicker).save(
                made["pin"], quality=quality
            )

        # social — 1:1 with a title band
        if "social" not in made:
            made["social"] = out_dir / f"{book.slug}-social.jpg"
            _with_caption(page, cfg.image_size("social"), caption, kicker).save(
                made["social"], quality=quality
            )
    except Exception as exc:
        log.warn(f"cover art stopped early ({exc}) — publishing with whatever was built")

    for kind, path in made.items():
        if (supplied or {}).get(kind):
            continue  # already logged as "using your own ..."
        log.ok(f"{kind}: {path.name} ({path.stat().st_size // 1024} KB)", indent=2)
    book.images = made
    return made


def save_dashboard_cover(book, cfg, size=480):
    """A small square copy committed next to the dashboard, so the status
    page shows real covers without hot-linking anything."""
    from .config import ROOT

    source = book.images.get("thumbnail") or book.images.get("cover")
    if not source or not Path(source).exists():
        return None
    target = ROOT / cfg.get("dashboard.output", "docs/index.html")
    target = target.parent / "covers" / f"{book.slug}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageOps

        with Image.open(source) as image:
            ImageOps.fit(image.convert("RGB"), (size, size), method=Image.LANCZOS).save(
                target, quality=82, optimize=True
            )
    except Exception as exc:
        log.warn(f"could not write the dashboard cover ({exc})", indent=2)
        return None
    return target
