"""One run, start to finish."""

import os
import tempfile
from pathlib import Path

from . import dashboard, images, log, marketing, metadata, scheduler, social, sources, state as state_mod
from .config import ROOT
from .platforms import enabled as enabled_platforms

PREVIEW_DIR = ROOT / "out"


def collect(cfg, st):
    """Every book that could be published, oldest file first.

    Also the detection step: any Drive file we have not seen before is
    registered here, which starts its settle clock.
    """
    overrides = metadata.load_overrides()
    candidates, service = [], None

    local_dir = os.environ.get("AUTOPOST_LOCAL_DIR", "").strip()
    ignore = cfg.get("source.ignore_patterns", [])
    min_size = float(cfg.get("source.min_size_mb", 0))
    recursive = bool(cfg.get("source.recursive", True))

    artwork = []
    if local_dir:
        log.info(f"scanning local directory {local_dir}")
        files, artwork = sources.scan_local(local_dir, recursive, ignore, min_size)
    else:
        service = sources.drive_service()
        if service:
            folder = cfg.folder_id
            if not folder:
                log.fail(
                    "no Drive folder configured. Set source.folder_id in config.yml "
                    "(the part of the folder URL after /folders/)."
                )
            log.info(f"scanning Drive folder {folder}")
            files, artwork = sources.scan_drive(service, folder, recursive, ignore, min_size)
        else:
            log.warn("no service account configured — only books.json `link:` entries can be used")
            files = []

    new_count = 0
    for source_file in files:
        slug = metadata.slugify(source_file.stem)
        if st.note_file(source_file.id, source_file.name, slug, source_file.size):
            new_count += 1
            log.ok(f"new file detected: {source_file.name}  →  {slug}")

    # Panel uploads live in the repo and beat anything in Drive, so they go last.
    repo_artwork = sources.scan_repo_artwork(ROOT)
    artwork = list(artwork) + repo_artwork

    log.info(
        f"{len(files)} PDF(s) in the folder, {new_count} new since the last run"
        + (f", {len(artwork)} image(s) available as artwork" if artwork else "")
        + (f" ({len(repo_artwork)} uploaded in the panel)" if repo_artwork else "")
    )

    for source_file in files:
        slug = metadata.slugify(source_file.stem)
        if st.is_live(slug):
            continue
        candidates.append((slug, source_file))

    # Books that exist only in books.json, with a shareable link.
    known = {slug for slug, _ in candidates}
    for slug, override in overrides.items():
        if slug in known or st.is_live(slug) or not override.get("link"):
            continue
        book = metadata.from_override(slug, cfg, overrides)
        if book:
            candidates.append((slug, book.source_file))

    return candidates, service, overrides, artwork


def publish_one(cfg, st, slug, source_file, service, overrides, dry_run, use_ai, artwork=()):
    """Download, write the listing, build the art, push it, record it."""
    log.step(f"Publishing {slug}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pdf_path = tmp / f"{slug}.pdf"

        if getattr(source_file, "path", None):
            pdf_path = Path(source_file.path)
        elif str(source_file.id).startswith("books.json:"):
            link = (overrides.get(slug) or {}).get("link")
            sources.download_link(link, pdf_path)
        else:
            sources.download_drive(service, source_file.id, pdf_path)

        size_mb = pdf_path.stat().st_size / 1e6
        if size_mb < float(cfg.get("source.min_size_mb", 0)):
            raise log.AutopostError(
                f"the downloaded file is only {size_mb:.2f} MB — check the sharing permissions"
            )
        log.ok(f"PDF ready: {size_mb:.2f} MB", indent=2)

        book = metadata.build(source_file, cfg, overrides, pdf_path)
        log.info(
            f"{book.name}  ·  {book.pages or '?'} pages  ·  ${book.price}"
            f"  ·  theme: {book.theme or 'generic'}"
            + ("  ·  books.json override" if book.overridden else ""),
            indent=2,
        )

        st.record(slug, name=book.name, price=book.price, pages=book.pages,
                  status="pending", source={"id": source_file.id, "filename": source_file.name})

        listing = marketing.compose(book, cfg, use_ai=use_ai)
        log.ok(f"title: {listing['title']}", indent=2)
        log.info(f"tags: {', '.join(listing['tags'])}", indent=2)

        # Artwork the user dropped next to the PDF wins over a render.
        supplied = {}
        for kind, image_file in sources.match_artwork(artwork, slug).items():
            try:
                if getattr(image_file, "path", None):
                    supplied[kind] = Path(image_file.path)
                else:
                    dest = tmp / image_file.name
                    supplied[kind] = sources.download_drive(service, image_file.id, dest)
            except Exception as exc:
                log.warn(f"could not fetch {image_file.name} ({exc}) — rendering the {kind} instead", indent=2)

        art_dir = PREVIEW_DIR if dry_run else tmp
        images.build(book, pdf_path, art_dir / slug, cfg, supplied=supplied)
        if not dry_run:
            images.save_dashboard_cover(book, cfg)

        results = {}
        failures = []
        for platform in enabled_platforms(cfg, dry_run=dry_run):
            log.info(f"→ {platform.label}", indent=1)
            try:
                platform.preflight()
                result = platform.publish(book, pdf_path)
            except log.AutopostError as exc:
                log.error(str(exc), indent=2)
                result = {"status": "failed", "error": str(exc), "product_id": None, "url": None}
                failures.append(platform.label)
            results[platform.name] = dict(result)
            st.record_platform(slug, platform.name, **dict(result))

        listing["results"] = results
        pack_dir = None if dry_run else social.write_pack(book, cfg)

        statuses = {r.get("status") for r in results.values()}
        if dry_run:
            overall = "dry-run"
        elif not results:
            overall = "failed"
        elif statuses <= {"published", "skipped"}:
            overall = "published"
        elif "published" in statuses or "draft" in statuses:
            overall = "partial"
        else:
            overall = "failed"

        if not dry_run:
            st.record(
                slug,
                status=overall,
                title=listing["title"],
                summary=listing["summary"],
                tags=listing["tags"],
                social_pack=pack_dir,
                ai_polished=listing["ai_polished"],
            )

        return book, overall, failures


def run(cfg, force=False, dry_run=False, only_slug="", use_ai=True, scan_only=False):
    st = state_mod.State()

    log.step("Preflight")
    platforms = enabled_platforms(cfg, dry_run=dry_run)
    if not platforms:
        log.fail("no platform is enabled in config.yml — nothing could be published")
    for platform in platforms:
        try:
            platform.preflight()
            log.ok(f"{platform.label} ready")
        except log.AutopostError as exc:
            log.fail(str(exc))
    log.info(
        f"social: {', '.join(cfg.enabled_networks()) or 'off'}   ·   "
        f"AI polish: {'on' if use_ai and os.environ.get('ANTHROPIC_API_KEY') else 'off'}"
    )

    log.step("Looking for new files")
    candidates, service, overrides, artwork = collect(cfg, st)

    if only_slug:
        candidates = [c for c in candidates if c[0] == only_slug]
        if not candidates:
            st.save()
            log.fail(
                f"'{only_slug}' is not waiting to be published. Either its PDF is not in "
                "the folder, or it is already live (see state/published.json)."
            )

    if scan_only:
        log.step("Scan only — nothing will be published")
        for slug, _ in candidates:
            log.info(f"queued: {slug}")
        st.save()
        return 0

    log.step("Schedule")
    may_publish, reason = scheduler.decide(cfg, st, force=force or dry_run)
    upcoming = scheduler.next_run(cfg)
    log.info(f"now:  {scheduler.local_now(cfg).strftime('%a %d %b %H:%M %Z')}")
    log.info(f"next: {upcoming.strftime('%a %d %b %H:%M %Z')}")
    log.info(f"{'publishing' if may_publish else 'holding'} — {reason}")

    if not may_publish:
        st.save()
        log.summary(f"### Autopost\n\nHolding: {reason}\n\nNext window: {upcoming:%a %d %b %H:%M %Z}\n")
        return 0

    if not candidates:
        st.save()
        log.ok("nothing new to publish — every PDF in the folder is already live", indent=0)
        log.summary(f"### Autopost\n\nNothing new to publish.\n\nNext window: {upcoming:%a %d %b %H:%M %Z}\n")
        return 0

    limit = max(1, int(cfg.get("schedule.max_per_run", 1)))
    published, held, failed = [], [], []

    for slug, source_file in candidates:
        if len(published) >= limit and not only_slug:
            held.append((slug, f"holding for the next run (max_per_run is {limit})"))
            continue
        ready, why = scheduler.settled(cfg, st, source_file)
        if not ready and not (force or only_slug):
            held.append((slug, why))
            log.info(f"holding {slug} — {why}")
            continue
        try:
            book, overall, failures = publish_one(
                cfg, st, slug, source_file, service, overrides, dry_run, use_ai, artwork
            )
            if overall in ("published", "dry-run"):
                published.append((slug, book))
            else:
                failed.append((slug, ", ".join(failures) or overall))
        except log.AutopostError as exc:
            log.error(f"{slug}: {exc}")
            st.record(slug, status="failed", error=str(exc))
            failed.append((slug, str(exc)))
        except Exception as exc:  # never let one bad book kill the run
            log.error(f"{slug}: unexpected error: {exc}")
            st.record(slug, status="failed", error=repr(exc))
            failed.append((slug, repr(exc)))

    log.step("Result")
    for slug, book in published:
        url = next(
            (r.get("url") for r in (book.listing.get("results") or {}).values() if r.get("url")),
            None,
        )
        if dry_run:
            log.ok(f"{slug} → dry run, nothing was sent (previews in out/{slug}/)", indent=0)
        else:
            log.ok(f"{slug} → {url or 'live'}", indent=0)
    for slug, why in held:
        log.info(f"held: {slug} — {why}", indent=0)
    for slug, why in failed:
        log.error(f"failed: {slug} — {why}", indent=0)

    remaining = len(candidates) - len(published)
    log.info(f"{remaining} book(s) still queued", indent=0)

    if not dry_run:
        st.save()
        if cfg.get("dashboard.enabled", True):
            dashboard.build(cfg, st)

    _write_summary(cfg, published, held, failed, remaining, dry_run)
    return 1 if failed and not published else 0


def _write_summary(cfg, published, held, failed, remaining, dry_run):
    rows = ["### Autopost" + (" (dry run)" if dry_run else ""), ""]
    for slug, book in published:
        url = next(
            (r.get("url") for r in (book.listing.get("results") or {}).values() if r.get("url")),
            None,
        )
        title = book.listing["title"]
        rows.append(f"- ✅ **{f'[{title}]({url})' if url else title}** — ${book.price}, {book.pages or '?'} pages")
    for slug, why in failed:
        rows.append(f"- ❌ `{slug}` — {why}")
    for slug, why in held:
        rows.append(f"- ⏸ `{slug}` — {why}")
    if not (published or failed or held):
        rows.append("Nothing to do.")
    rows += ["", f"{remaining} book(s) queued · next window {scheduler.next_run(cfg):%a %d %b %H:%M %Z}"]
    log.summary("\n".join(rows))
