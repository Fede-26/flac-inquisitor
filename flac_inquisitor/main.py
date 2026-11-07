import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from flac_inquisitor import __version__, db, lidarr, queries, quarantine, settings, spectrogram
from flac_inquisitor.detect import ALGO_VERSION
from flac_inquisitor.scanner import regrade_if_changed, scanner

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    regrade_if_changed()
    yield
    scanner.stop()


app = FastAPI(title="flac-inquisitor", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(version=__version__, algo_version=ALGO_VERSION)


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"nav": name.split(".")[0], **context})


def filters_from(verdict: list[str] | None, category: str | None, fmt: str | None, q: str | None,
                 sort: str, page_no: int, cd: str | None = None) -> queries.Filters:
    verdicts = [v for v in (verdict or ["fake", "suspect"]) if v in queries.VERDICTS]
    # "all" in the query string means no verdict filter.
    if verdict == ["all"]:
        verdicts = []
    return queries.Filters(verdicts=verdicts, category=category or None, fmt=fmt or None, q=q or None,
                           cd=cd or None, sort=sort, page=max(page_no, 1))


def library_ok() -> str | None:
    path = Path(settings.get("library_path"))
    if not path.is_dir():
        return f"The library folder {path} does not exist inside the container."
    return None


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__}


# Pages.

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return page(request, "dashboard.html", summary=queries.summary(), scan=scanner.status(),
                activity=db.recent_activity(), library_error=library_ok())


@app.get("/albums", response_class=HTMLResponse)
@app.get("/tracks", response_class=HTMLResponse)
def library(request: Request, verdict: list[str] | None = Query(None), category: str | None = None,
            fmt: str | None = None, q: str | None = None, cd: str | None = None, sort: str = "severity",
            page_no: int = Query(1, alias="page")):
    f = filters_from(verdict, category, fmt, q, sort, page_no, cd)
    by_track = request.url.path == "/tracks"
    result = queries.list_tracks(f) if by_track else queries.list_albums(f)
    return page(request, "library.html", result=result, filters=f, by_track=by_track,
                categories=sorted(queries.summary()["by_category"]), formats=queries.formats(),
                query=request.url.query)


@app.get("/albums/{album_key}", response_class=HTMLResponse)
def album_page(request: Request, album_key: str):
    album = queries.get_album(album_key)
    if album is None:
        raise HTTPException(404, "Album not found. It may have been moved or quarantined.")
    return page(request, "album.html", album=album, scanning=scanner.running)


@app.get("/quarantine", response_class=HTMLResponse)
def quarantine_page(request: Request, status: str = "quarantined"):
    status = status if status in ("quarantined", "restored", "deleted") else "quarantined"
    return page(request, "quarantine.html", groups=quarantine.grouped(status), status=status,
                links=lidarr.artist_links() if status == "quarantined" else {}, scanning=scanner.running)


@app.get("/accuracy", response_class=HTMLResponse)
def accuracy_page(request: Request):
    return page(request, "accuracy.html", report=queries.accuracy())


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return page(request, "settings.html", settings=settings.describe(), data_path=str(db.DATA_PATH))


# JSON API.

@app.get("/api/scan")
def scan_status() -> dict:
    return scanner.status()


class ScanRequest(BaseModel):
    full: bool = False
    # Library-relative folder: an album, a disc or an artist. Empty for all.
    folder: str | None = None


def library_folder(folder: str | None) -> str | None:
    """The folder as a clean library-relative path, None for the whole library."""
    if not folder or not folder.strip("/ "):
        return None
    root = Path(settings.get("library_path")).resolve()
    target = (root / folder.strip("/")).resolve()
    # Only folders inside the library, never "../" tricks.
    if not target.is_relative_to(root) or not target.is_dir():
        raise HTTPException(400, f"{folder} is not a folder of the library.")
    rel = target.relative_to(root).as_posix()
    return None if rel == "." else rel


@app.post("/api/scan")
def scan_start(body: ScanRequest | None = None) -> dict:
    if error := library_ok():
        raise HTTPException(400, error)
    folder = library_folder(body.folder if body else None)
    if not scanner.start(full=bool(body and body.full), folder=folder):
        raise HTTPException(409, "A scan is already running.")
    return scanner.status()


@app.get("/api/folders")
def api_folders() -> list[str]:
    """Library folders two levels deep (artist, artist/album), for the folder
    field of the scan form. Deeper folders can still be typed in."""
    root = Path(settings.get("library_path"))
    if not root.is_dir():
        return []
    out = []
    for first in sorted(os.scandir(root), key=lambda e: e.name):
        if first.is_dir() and not first.name.startswith("."):
            out.append(first.name)
            out += [f"{first.name}/{e.name}" for e in sorted(os.scandir(first.path), key=lambda e: e.name)
                    if e.is_dir() and not e.name.startswith(".")]
    return out


@app.delete("/api/scan")
def scan_stop() -> dict:
    scanner.stop()
    return scanner.status()


@app.get("/api/summary")
def api_summary() -> dict:
    return queries.summary()


@app.get("/api/accuracy")
def api_accuracy() -> dict:
    return queries.accuracy()


@app.get("/api/albums")
def api_albums(verdict: list[str] | None = Query(None), category: str | None = None, fmt: str | None = None,
               q: str | None = None, cd: str | None = None, sort: str = "severity",
               page_no: int = Query(1, alias="page")) -> dict:
    return queries.list_albums(filters_from(verdict, category, fmt, q, sort, page_no, cd))


@app.get("/api/albums/{album_key}")
def api_album(album_key: str) -> dict:
    album = queries.get_album(album_key)
    if album is None:
        raise HTTPException(404, "Album not found.")
    return album


@app.post("/api/albums/{album_key}/offset-search")
def api_offset_search(album_key: str) -> dict:
    album = queries.get_album(album_key)
    if album is None:
        raise HTTPException(404, "Album not found.")
    if not album["cd_mismatch"]:
        raise HTTPException(400, "No disc of this album is known to the CD databases without matching.")
    if not scanner.start_offset_search(album["album_dir"]):
        raise HTTPException(409, "A scan is already running.")
    return scanner.status()


@app.get("/api/tracks")
def api_tracks(verdict: list[str] | None = Query(None), category: str | None = None, fmt: str | None = None,
               q: str | None = None, cd: str | None = None, sort: str = "severity",
               page_no: int = Query(1, alias="page")) -> dict:
    return queries.list_tracks(filters_from(verdict, category, fmt, q, sort, page_no, cd))


def _track_or_404(track_id: int) -> dict:
    track = queries.get_track(track_id)
    if track is None:
        raise HTTPException(404, "Track not found.")
    return track


@app.get("/api/tracks/{track_id}")
def api_track(track_id: int) -> dict:
    return _track_or_404(track_id)


@app.get("/api/tracks/{track_id}/spectrogram.png")
def api_spectrogram(track_id: int, width: int = 1200, height: int = 480):
    track = _track_or_404(track_id)
    path = Path(settings.get("library_path")) / track["path"]
    if not path.is_file():
        raise HTTPException(404, "The file is no longer in the library.")
    return _spectrogram_response(path, track["audio_md5"] or track["path"], width, height)


@app.get("/api/tracks/{track_id}/audio")
def api_audio(track_id: int):
    track = _track_or_404(track_id)
    path = Path(settings.get("library_path")) / track["path"]
    if not path.is_file():
        raise HTTPException(404, "The file is no longer in the library.")
    return FileResponse(path, media_type="audio/flac")


class SettingsUpdate(BaseModel):
    values: dict[str, str]


class IgnoreRequest(BaseModel):
    track_ids: list[int]
    ignore: bool = True
    note: str | None = None


@app.post("/api/ignore")
def api_ignore(body: IgnoreRequest) -> dict:
    tracks = [_track_or_404(i) for i in body.track_ids]
    md5s = [t["audio_md5"] for t in tracks if t["audio_md5"]]
    with db.connect() as conn:
        if body.ignore:
            conn.executemany("INSERT OR IGNORE INTO ignores (audio_md5, note, created_at) VALUES (?, ?, ?)",
                             [(m, body.note, db.now()) for m in md5s])
        else:
            conn.executemany("DELETE FROM ignores WHERE audio_md5 = ?", [(m,) for m in md5s])
    names = ", ".join(t["path"] for t in tracks[:3]) + ("…" if len(tracks) > 3 else "")
    db.log("ignore", f"{'Ignored' if body.ignore else 'Un-ignored'} {len(md5s)} track(s): {names}")
    return {"changed": len(md5s)}


class QuarantineRequest(BaseModel):
    track_ids: list[int] = []
    album_keys: list[str] = []


def _no_scan() -> None:
    # The scanner holds a list of files; moving them under it would make it
    # report them missing.
    if scanner.running:
        raise HTTPException(409, "A scan is running. Wait for it to finish before moving files.")


@app.post("/api/quarantine")
def api_quarantine(body: QuarantineRequest) -> dict:
    _no_scan()
    if not body.track_ids and not body.album_keys:
        raise HTTPException(400, "Nothing selected.")
    try:
        result = quarantine.quarantine(track_ids=body.track_ids or None, album_keys=body.album_keys or None)
    except quarantine.QuarantineError as exc:
        raise HTTPException(400, str(exc)) from exc
    lidarr.rescan(result["album_dirs"])
    return result


@app.get("/api/quarantine")
def api_quarantine_list(status: str | None = "quarantined") -> list[dict]:
    return quarantine.grouped(status)


@app.get("/api/quarantine/list.txt")
def api_quarantine_text() -> dict:
    return {"text": quarantine.copy_list()}


class EntriesRequest(BaseModel):
    ids: list[int]
    mode: str = "check"


@app.post("/api/quarantine/restore")
def api_restore(body: EntriesRequest):
    _no_scan()
    if body.mode not in ("check", "replace", "force"):
        raise HTTPException(400, "mode must be check, replace or force.")
    try:
        result = quarantine.restore(body.ids, body.mode)
    except quarantine.QuarantineError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["conflicts"]:
        from fastapi.responses import JSONResponse

        return JSONResponse(result, status_code=409)
    lidarr.rescan(result["album_dirs"])
    return result


@app.post("/api/quarantine/delete")
def api_delete(body: EntriesRequest) -> dict:
    return quarantine.delete(body.ids)


@app.get("/api/quarantine/{entry_id}/spectrogram.png")
def api_quarantine_spectrogram(entry_id: int, width: int = 1200, height: int = 480):
    path = quarantine.stored_file(entry_id)
    if path is None:
        raise HTTPException(404, "Not in quarantine.")
    return _spectrogram_response(path, f"q{entry_id}", width, height)


@app.get("/api/library-file/spectrogram.png")
def api_library_spectrogram(path: str, width: int = 1200, height: int = 480):
    root = Path(settings.get("library_path")).resolve()
    file = (root / path).resolve()
    # Only files inside the library, never "../" tricks.
    if not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(404, "File not found in the library.")
    return _spectrogram_response(file, "lib-" + path, width, height)


def _spectrogram_response(path: Path, key: str, width: int, height: int):
    try:
        png = spectrogram.render(path, key, min(width, 2400), min(height, 1200))
    except spectrogram.SpectrogramError as exc:
        raise HTTPException(500, str(exc)) from exc
    return FileResponse(png, media_type="image/png")


@app.post("/api/lidarr/test")
def api_lidarr_test(body: SettingsUpdate | None = None) -> dict:
    values = body.values if body else {}
    if not lidarr.configured(values):
        raise HTTPException(400, "Fill in the Lidarr URL and API key first.")
    try:
        return {"message": lidarr.test(values)}
    except lidarr.LidarrError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/activity")
def api_activity(limit: int = 50) -> list[dict]:
    return db.recent_activity(min(limit, 500))


@app.get("/api/settings")
def api_settings() -> list[dict]:
    return [{**s, "value": "" if s["secret"] and s["value"] else s["value"]} | {"has_value": bool(s["value"])}
            for s in settings.describe()]


@app.post("/api/settings/validate")
def api_settings_validate(body: SettingsUpdate) -> dict:
    return settings.validate(body.values)


@app.put("/api/settings")
def api_settings_save(body: SettingsUpdate) -> dict:
    report = settings.validate(body.values)
    if report["errors"]:
        raise HTTPException(400, " ".join(report["errors"]))
    try:
        settings.save(body.values)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    db.log("settings", "Settings saved.")
    regrade_if_changed()
    return report


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return page(request, "error.html", status=exc.status_code, detail=exc.detail)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
