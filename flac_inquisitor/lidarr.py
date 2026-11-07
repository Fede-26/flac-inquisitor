"""Optional Lidarr integration: tell Lidarr to rescan an artist folder after
files were quarantined or restored, and link quarantined albums to Lidarr.

Uses only RescanFolders (verified against Lidarr's DiskScanService on
07/11/2026): with the "Known" filter it drops vanished files from Lidarr's
database and imports new ones, without searching or downloading anything.
"""

import json
import urllib.error
import urllib.request
from pathlib import PurePosixPath

from flac_inquisitor import db, settings

TIMEOUT = 10


class LidarrError(RuntimeError):
    pass


def configured(values: dict | None = None) -> bool:
    v = values or {}
    return bool((v.get("lidarr_url") or settings.get("lidarr_url")).strip()
                and (v.get("lidarr_api_key") or settings.get("lidarr_api_key")).strip())


def _call(method: str, path: str, body: dict | None = None, values: dict | None = None):
    v = values or {}
    base = (v.get("lidarr_url") or settings.get("lidarr_url")).strip().rstrip("/")
    key = (v.get("lidarr_api_key") or settings.get("lidarr_api_key")).strip()
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise LidarrError("Lidarr refused the API key.") from exc
        raise LidarrError(f"Lidarr answered {exc.code} {exc.reason}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LidarrError(f"Cannot reach Lidarr at {base}: {exc}") from exc


def test(values: dict | None = None) -> str:
    status = _call("GET", "/api/v1/system/status", values=values)
    return f"Connected to Lidarr {status.get('version', '?')}."


def lidarr_path(album_dir: str) -> str:
    """The artist folder (first path component) as Lidarr sees it."""
    prefix = settings.get("lidarr_path_prefix").strip() or settings.get("library_path")
    artist = PurePosixPath(album_dir).parts[0]
    return str(PurePosixPath(prefix) / artist)


def rescan(album_dirs: list[str]) -> None:
    """Best effort: a Lidarr problem must not undo or block a file move."""
    if not configured() or not album_dirs:
        return
    folders = sorted({lidarr_path(d) for d in album_dirs})
    try:
        # The command's default filter is "Known"; leaving it out avoids
        # depending on how Lidarr serializes the enum.
        _call("POST", "/api/v1/command", {"name": "RescanFolders", "folders": folders, "addNewArtists": False})
        db.log("lidarr", f"Asked Lidarr to rescan {', '.join(folders)[:200]}.")
    except LidarrError as exc:
        db.log("lidarr", f"Lidarr rescan failed: {exc}")


def artist_links() -> dict[str, str]:
    """Artist folder name -> Lidarr artist page."""
    if not configured():
        return {}
    try:
        artists = _call("GET", "/api/v1/artist")
    except LidarrError:
        return {}
    base = settings.get("lidarr_url").strip().rstrip("/")
    return {PurePosixPath(a["path"]).name: f"{base}/artist/{a['foreignArtistId']}"
            for a in artists if a.get("path") and a.get("foreignArtistId")}
