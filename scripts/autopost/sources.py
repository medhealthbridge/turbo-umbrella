"""Where the PDFs come from.

Three sources, tried in this order:

  gdrive   a service account watches the folder in config.yml. Fully
           hands-off: drop a PDF in, it gets picked up.
  local    a directory on disk (AUTOPOST_LOCAL_DIR). Used by the test
           suite, and handy for a one-off publish from your own machine.
  link     a per-book `link:` in books.json, fetched with gdown. The
           fallback for when no service account is configured.
"""

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import log

PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")

# An image dropped beside the PDF overrides the one rendered from page 1.
# `ocean-buddies.pdf` + `ocean-buddies-cover.jpg` → that JPEG is the cover.
ART_SUFFIXES = ("cover", "thumbnail", "pin", "social")


@dataclass
class SourceFile:
    """One candidate file, before we know anything about the book."""

    id: str
    name: str
    size: int = 0
    created_at: str = ""
    modified_at: str = ""
    path: Path = None  # set for local files; Drive files are downloaded later
    extra: dict = field(default_factory=dict)

    @property
    def stem(self):
        return Path(self.name).stem.strip()


def _ignored(name, patterns):
    lowered = name.lower()
    for pattern in patterns or []:
        pattern = str(pattern).lower()
        if pattern in lowered or fnmatch.fnmatch(lowered, pattern):
            return True
    return False


# --------------------------------------------------------------- Google Drive


def drive_service():
    """Build a read-only Drive client, or None if no key is configured."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    import json

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        log.fail(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole "
            "downloaded key file, including the outer { }."
        )
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _drive_children(service, folder_id):
    fields = (
        "nextPageToken, files(id, name, size, mimeType, createdTime, "
        "modifiedTime, md5Checksum)"
    )
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces="drive",
                fields=fields,
                pageToken=page_token,
                pageSize=200,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        yield from resp.get("files", [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def scan_drive(service, folder_id, recursive=True, ignore=None, min_size_mb=0.0):
    """(pdfs, images) in the folder. PDFs come back newest upload last."""
    found, images, folders, visited = [], [], [folder_id], set()
    while folders:
        current = folders.pop(0)
        if current in visited:
            continue  # Drive shortcuts can make the tree cyclic
        visited.add(current)
        try:
            children = list(_drive_children(service, current))
        except Exception as exc:
            log.fail(
                f"could not read Drive folder {current}: {exc}\n"
                "  Check that the folder is shared with the service account's "
                "client_email (Viewer is enough)."
            )
        for f in children:
            if f.get("mimeType") == FOLDER_MIME:
                if recursive:
                    folders.append(f["id"])
                continue
            if _ignored(f["name"], ignore):
                log.info(f"skipping {f['name']} (matches an ignore pattern)")
                continue
            if f.get("mimeType") in IMAGE_MIMES:
                images.append(
                    SourceFile(
                        id=f["id"],
                        name=f["name"],
                        size=int(f.get("size") or 0),
                        created_at=f.get("createdTime", ""),
                    )
                )
                continue
            if f.get("mimeType") != PDF_MIME:
                continue
            size = int(f.get("size") or 0)
            if size < min_size_mb * 1024 * 1024:
                log.warn(f"skipping {f['name']} — only {size / 1e6:.2f} MB, looks like a partial upload")
                continue
            found.append(
                SourceFile(
                    id=f["id"],
                    name=f["name"],
                    size=size,
                    created_at=f.get("createdTime", ""),
                    modified_at=f.get("modifiedTime", ""),
                    extra={"md5": f.get("md5Checksum")},
                )
            )
    found.sort(key=lambda f: (f.created_at, f.name))
    return found, images


def download_drive(service, file_id, dest):
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done, last = False, -1
        while not done:
            status, done = downloader.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct >= last + 25:  # quarter-way updates, not a wall of text
                    log.info(f"downloading… {pct}%", indent=2)
                    last = pct
    return Path(dest)


# ----------------------------------------------------------------- local dir


def scan_local(directory, recursive=True, ignore=None, min_size_mb=0.0):
    """(pdfs, images), matching scan_drive."""
    directory = Path(directory)
    if not directory.is_dir():
        log.fail(f"AUTOPOST_LOCAL_DIR points at {directory}, which is not a directory")
    glob = directory.rglob if recursive else directory.glob
    found, images = [], []
    for path in sorted(glob("*")):
        if not path.is_file() or _ignored(path.name, ignore):
            continue
        suffix = path.suffix.lower()
        stat = path.stat()
        entry = SourceFile(
            id=f"local:{path.resolve()}",
            name=path.name,
            size=stat.st_size,
            created_at=str(int(stat.st_mtime)),
            path=path,
        )
        if suffix in (".jpg", ".jpeg", ".png", ".webp"):
            images.append(entry)
        elif suffix == ".pdf":
            if entry.size < min_size_mb * 1024 * 1024:
                log.warn(f"skipping {path.name} — only {entry.size / 1e6:.2f} MB")
                continue
            found.append(entry)
    return found, images


# --------------------------------------------------------------------- links


def download_link(url, dest):
    """A public 'anyone with the link' Drive file, or any direct URL."""
    log.info(f"fetching via link: {url}", indent=2)
    result = subprocess.run(
        ["gdown", "--fuzzy", "-O", str(dest), url], capture_output=True, text=True
    )
    if result.returncode != 0 or not Path(dest).exists():
        log.warn(result.stderr.strip()[:400] or result.stdout.strip()[:400])
        raise log.AutopostError(
            "link download failed — is the file shared as 'anyone with the link'?"
        )
    return Path(dest)


def match_artwork(images, slug):
    """Images a user supplied for one book, by filename convention.

    `<slug>-cover.jpg`, `<slug>-thumbnail.png`, `<slug>-pin.jpg`,
    `<slug>-social.jpg`. A bare `<slug>.jpg` counts as the cover, which is
    what people reach for first.

    Returns {kind: SourceFile}.
    """
    matched = {}
    for image in images:
        stem = Path(image.name).stem.strip().lower()
        if stem == slug:
            matched.setdefault("cover", image)
            continue
        for kind in ART_SUFFIXES:
            if stem == f"{slug}-{kind}":
                matched[kind] = image
    return matched
