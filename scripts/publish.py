#!/usr/bin/env python3
"""
Publish the next unpublished book to Gumroad.

Reads books.json for metadata, finds the matching PDF (Google Drive or a
direct link), creates the Gumroad product, publishes it, and records the
result in state/published.json so nothing is ever posted twice.

Environment:
  GUMROAD_ACCESS_TOKEN        required  - Gumroad API token
  GDRIVE_FOLDER_ID            optional  - Drive folder to scan (service-account mode)
  GOOGLE_SERVICE_ACCOUNT_JSON optional  - service account key JSON (service-account mode)
  ONLY_SLUG                   optional  - publish this specific slug instead of the next one
  DRY_RUN                     optional  - "true" to do everything except call Gumroad
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS_FILE = ROOT / "books.json"
STATE_FILE = ROOT / "state" / "published.json"

DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"
ONLY_SLUG = os.environ.get("ONLY_SLUG", "").strip()


def log(msg):
    print(msg, flush=True)


def fail(msg, code=1):
    log(f"ERROR: {msg}")
    sys.exit(code)


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- Drive access

def drive_service():
    """Build a Drive client from the service account key, or return None."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def drive_list_pdfs(service, folder_id):
    """Return {filename_stem_lowercased: file_id} for PDFs in the folder."""
    found = {}
    page_token = None
    query = (
        f"'{folder_id}' in parents "
        "and mimeType='application/pdf' "
        "and trashed=false"
    )
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, size)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files", []):
            stem = Path(f["name"]).stem.strip().lower()
            found[stem] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return found


def drive_download(service, file_id, dest):
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                log(f"  downloading... {int(status.progress() * 100)}%")
    return dest


def download_via_link(url, dest):
    """Fallback: public 'anyone with the link' Drive file, or any direct URL."""
    log(f"  fetching via link: {url}")
    result = subprocess.run(
        ["gdown", "--fuzzy", "-O", str(dest), url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not Path(dest).exists():
        log(result.stdout)
        log(result.stderr)
        fail("link download failed - is the file shared as 'anyone with the link'?")
    return dest


# -------------------------------------------------------------------- Gumroad

def run_gumroad(args):
    cmd = ["gumroad"] + args
    log(f"  $ gumroad {' '.join(args)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.returncode != 0:
        log(result.stderr.strip())
        fail(f"gumroad command failed (exit {result.returncode})")
    return result.stdout


def extract_product_id(output):
    """Pull the product id out of --json output, falling back to a regex."""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            for key in ("id", "product_id"):
                if data.get(key):
                    return str(data[key])
            prod = data.get("product") or {}
            for key in ("id", "product_id"):
                if prod.get(key):
                    return str(prod[key])
    except json.JSONDecodeError:
        pass
    match = re.search(r"\b([A-Za-z0-9_-]{8,})\b", output)
    return match.group(1) if match else None


def publish_book(book, pdf_path):
    name = book["name"]
    price = str(book["price"])
    description = book.get("description", "")

    create_args = [
        "products", "create",
        "--name", name,
        "--price", price,
        "--file", str(pdf_path),
        "--json",
        "--non-interactive",
    ]
    if description:
        create_args += ["--description", description]

    if DRY_RUN:
        log(f"  DRY RUN - would run: gumroad {' '.join(create_args)}")
        return {"product_id": "dry-run", "url": None}

    out = run_gumroad(create_args)
    product_id = extract_product_id(out)
    if not product_id:
        fail("could not determine the new product id from gumroad output")

    log(f"  created draft: {product_id}")
    run_gumroad(["products", "publish", product_id, "--non-interactive"])
    log(f"  published: {product_id}")

    url = None
    try:
        view = run_gumroad(["products", "view", product_id, "--json"])
        data = json.loads(view)
        url = data.get("short_url") or data.get("url")
    except Exception:
        pass

    return {"product_id": product_id, "url": url}


# ----------------------------------------------------------------------- main

def main():
    if not os.environ.get("GUMROAD_ACCESS_TOKEN") and not DRY_RUN:
        fail("GUMROAD_ACCESS_TOKEN is not set - add it as a repository secret")

    books = load_json(BOOKS_FILE, None)
    if not books:
        fail(f"{BOOKS_FILE} is missing or empty")

    state = load_json(STATE_FILE, {"published": []})
    done = {entry["slug"] for entry in state.get("published", [])}

    # Which PDFs are actually available right now?
    service = drive_service()
    available = {}
    if service:
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
        if not folder_id:
            fail("GOOGLE_SERVICE_ACCOUNT_JSON is set but GDRIVE_FOLDER_ID is not")
        log(f"Scanning Drive folder {folder_id} ...")
        available = drive_list_pdfs(service, folder_id)
        log(f"  found {len(available)} PDF(s): {', '.join(sorted(available)) or 'none'}")
    else:
        log("No service account configured - will use per-book 'link' fields instead.")

    # Pick the next book to publish.
    candidates = []
    for book in books:
        slug = book["slug"]
        if slug in done:
            continue
        if ONLY_SLUG and slug != ONLY_SLUG:
            continue
        has_drive_file = slug.lower() in available
        has_link = bool(book.get("link"))
        if has_drive_file or has_link:
            candidates.append((book, available.get(slug.lower())))

    if not candidates:
        if ONLY_SLUG:
            fail(f"'{ONLY_SLUG}' has no PDF available (or is already published)")
        log("Nothing to publish today - no new PDFs found. Exiting cleanly.")
        return

    book, drive_file_id = candidates[0]
    slug = book["slug"]
    log(f"\nPublishing: {slug} - {book['name']}")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / f"{slug}.pdf"
        if drive_file_id:
            drive_download(service, drive_file_id, pdf_path)
        else:
            download_via_link(book["link"], pdf_path)

        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        log(f"  PDF ready: {size_mb:.1f} MB")
        if size_mb < 0.05:
            fail("downloaded file is suspiciously small - check sharing permissions")

        result = publish_book(book, pdf_path)

    if DRY_RUN:
        log("\nDRY RUN complete - state not modified.")
        return

    state.setdefault("published", []).append(
        {
            "slug": slug,
            "name": book["name"],
            "product_id": result["product_id"],
            "url": result["url"],
            "price": book["price"],
            "published_at": subprocess.run(
                ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                capture_output=True, text=True,
            ).stdout.strip(),
        }
    )
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    remaining = len([b for b in books if b["slug"] not in done]) - 1
    log(f"\nDone. {remaining} book(s) still queued.")
    if result["url"]:
        log(f"Live at: {result['url']}")


if __name__ == "__main__":
    main()
