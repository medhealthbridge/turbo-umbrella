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
    """Every usable PDF in the folder, newest upload last."""
    found, folders, visited = [], [folder_id], set()
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
            if f.get("mimeType") != PDF_MIME:
                continue
            if _ignored(f["name"], ignore):
                log.info(f"skipping {f['name']} (matches an ignore pattern)")
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
    return found


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
    directory = Path(directory)
    if not directory.is_dir():
        log.fail(f"AUTOPOST_LOCAL_DIR points at {directory}, which is not a directory")
    pattern = "**/*.pdf" if recursive else "*.pdf"
    found = []
    for path in sorted(directory.glob(pattern)):
        if _ignored(path.name, ignore):
            continue
        size = path.stat().st_size
        if size < min_size_mb * 1024 * 1024:
            log.warn(f"skipping {path.name} — only {size / 1e6:.2f} MB")
            continue
        stat = path.stat()
        found.append(
            SourceFile(
                id=f"local:{path.resolve()}",
                name=path.name,
                size=size,
                created_at=str(int(stat.st_mtime)),
                path=path,
            )
        )
    return found


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
