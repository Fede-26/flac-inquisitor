"""Library scan: find FLAC files, analyze the new or changed ones in a process
pool, store the results and recompute album coherence.

A file is "unchanged" when the audio is: tagging tools rewrite files, so size
and mtime change while the STREAMINFO MD5 does not. Results are also reused
for files that were moved or renamed (same audio MD5 under a new path).
"""

import fnmatch
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

from flac_inquisitor import cdverify, db, settings
from flac_inquisitor.analyzer import measure
from flac_inquisitor.detect import ALGO_VERSION, album_coherence, evaluate, grade
from flac_inquisitor.flacmeta import FlacInfo, NotFlacError, read_flac_info

DISC_DIR = re.compile(r"^(disc|disk|cd)\s*\d+\b", re.IGNORECASE)
VERDICT_RANK = {"ok": 0, "suspect": 1, "fake": 2}

# Leading bytes of formats that end up renamed to .flac.
MAGIC = [(b"ID3", "MP3"), (b"\xff\xfb", "MP3"), (b"\xff\xf3", "MP3"), (b"\xff\xf2", "MP3"),
         (b"OggS", "Ogg (Vorbis or Opus)"), (b"RIFF", "WAV"), (b"\xff\xf1", "AAC"), (b"\xff\xf9", "AAC")]


def album_dir_of(rel: str) -> str:
    parent = PurePosixPath(rel).parent
    if DISC_DIR.match(parent.name) and parent.parent != PurePosixPath("."):
        parent = parent.parent
    return str(parent)


def album_key_of(album_dir: str) -> str:
    return hashlib.sha1(album_dir.encode()).hexdigest()[:12]


def _int_prefix(values: list[str] | None) -> int | None:
    match = re.match(r"\s*(\d+)", values[0]) if values else None
    return int(match.group(1)) if match else None


def tag_columns(info: FlacInfo) -> dict:
    t = info.tags
    first = lambda key: t[key][0] if t.get(key) else None  # noqa: E731
    return {
        "artist": first("ALBUMARTIST") or first("ARTIST"),
        "album": first("ALBUM"),
        "title": first("TITLE"),
        "disc": _int_prefix(t.get("DISCNUMBER")),
        "track": _int_prefix(t.get("TRACKNUMBER")),
        "sample_rate": info.sample_rate,
        "bits": info.bits_per_sample,
        "channels": info.channels,
        "duration": info.duration,
        "streaminfo_md5": info.md5_hex,
    }


def _sniff(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(4)
    # An ID3 tag can also precede real FLAC; read_flac_info already tried that.
    for magic, name in MAGIC:
        if head.startswith(magic):
            return name
    return "an unknown format"


def analyze_track(path: str, soft: frozenset[str] = frozenset()) -> dict:
    """Runs in a worker process. Returns the columns to store. soft comes from
    the caller: a worker started with spawn cannot read the settings."""
    p = Path(path)
    try:
        info = read_flac_info(p)
    except NotFlacError:
        kind = _sniff(p)
        evidence = [{"check": "format", "result": "fail", "category": "not-flac", "value": kind, "threshold": None,
                     "message": f"Not a FLAC file: the content is {kind} with a .flac name."}]
        return {"verdict": "fake", "categories": ["not-flac"], "summary": evidence[0]["message"],
                "evidence": evidence, "metrics": {}, "audio_md5": None, "error": None}
    try:
        m = measure(p, info)
        result = evaluate(info, m, soft)
    except Exception as exc:  # a broken file must not stop the scan
        return {**tag_columns(info), "error": f"{type(exc).__name__}: {exc}", "verdict": None}
    d = result.to_dict()
    return {**tag_columns(info), "verdict": d["verdict"], "categories": d["categories"], "summary": d["summary"],
            "evidence": d["evidence"], "metrics": {**d["metrics"], "cd": m.cd}, "audio_md5": d["audio_md5"],
            "error": None}


def list_flacs(root: Path, exclude: list[str], sub: str = "") -> list[str]:
    """Paths relative to root, of the FLAC files under root/sub."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root / sub):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            if not name.lower().endswith(".flac") or name.startswith("._"):
                continue
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not any(fnmatch.fnmatch(rel, pattern) for pattern in exclude):
                found.append(rel)
    return found


ANALYSIS_COLUMNS = ("verdict", "categories", "summary", "evidence", "metrics", "audio_md5", "error")


def _store(conn, rel: str, values: dict) -> None:
    values = {k: (json.dumps(v) if k in db.JSON_COLUMNS and v is not None else v) for k, v in values.items()}
    values["path"] = rel
    values["album_dir"] = album_dir_of(rel)
    values["album_key"] = album_key_of(values["album_dir"])
    values["missing"] = 0
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    updates = ", ".join(f"{c} = excluded.{c}" for c in values if c != "path")
    conn.execute(f"INSERT INTO tracks ({cols}) VALUES ({marks}) ON CONFLICT(path) DO UPDATE SET {updates}",
                 list(values.values()))


def recompute_albums(album_dirs: set[str] | None = None) -> None:
    """Album coherence plus the final verdict of every track. The track's own
    verdict is graded again from its evidence, so it follows the settings."""
    soft = settings.soft_categories()
    with db.connect() as conn:
        if album_dirs is None:
            album_dirs = {r[0] for r in conn.execute("SELECT DISTINCT album_dir FROM tracks WHERE missing = 0")}
        for album_dir in album_dirs:
            rows = conn.execute("SELECT id, sample_rate, bits, verdict, categories, evidence, metrics FROM tracks "
                                "WHERE album_dir = ? AND missing = 0 AND verdict IS NOT NULL ORDER BY path",
                                (album_dir,)).fetchall()
            # Files that are not FLAC at all have no format to compare.
            comparable = [i for i, r in enumerate(rows) if r["sample_rate"]]
            metrics = [json.loads(rows[i]["metrics"] or "{}") for i in comparable]
            tracks = [{"format": f"{rows[i]['sample_rate']}/{rows[i]['bits']}", "cutoff_hz": m.get("cutoff_hz"),
                       "mono": m.get("mono")} for i, m in zip(comparable, metrics)]
            flagged = {comparable[i]: ev for i, ev in album_coherence(tracks).items()}
            for i, r in enumerate(rows):
                categories = json.loads(r["categories"] or "[]")
                own = grade([(e["result"], e.get("category")) for e in json.loads(r["evidence"] or "[]")], soft)
                verdict = own
                album_ev = flagged.get(i)
                if album_ev:
                    categories = sorted(set(categories) | {album_ev.category})
                    if verdict == "ok":
                        verdict = "suspect"
                conn.execute("UPDATE tracks SET verdict = ?, album_evidence = ?, final_verdict = ?, "
                             "final_categories = ? WHERE id = ?",
                             (own, json.dumps([album_ev.__dict__]) if album_ev else None, verdict,
                              json.dumps(categories), r["id"]))


def regrade_if_changed() -> None:
    """Verdicts depend on the upsampled setting. When it changed since the
    verdicts were last graded (Settings page, or the environment between two
    starts), grade every track again: no decoding, only stored evidence."""
    current = settings.get("upsampled_verdict").strip().lower()
    if db.get_setting("graded_upsampled_verdict") != current:
        recompute_albums()
        db.set_setting("graded_upsampled_verdict", current)


class Scanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = {"state": "idle", "total": 0, "done": 0, "cached": 0, "analyzed": 0, "errors": 0,
                       "current": None, "started_at": None, "finished_at": None, "message": None,
                       "folder": None, "phase": None, "cd_done": 0, "cd_total": 0}

    def status(self) -> dict:
        with self._lock:
            s = dict(self._state)
        if s["state"] == "running" and s["done"] > s["cached"] and s["started_at"]:
            elapsed = time.time() - s["started_at"]
            rate = (s["done"] - s["cached"]) / max(elapsed, 1e-6)
            s["eta_seconds"] = round((s["total"] - s["done"]) / rate) if rate else None
        return s

    def _update(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def _bump(self, key: str, current: str | None = None) -> None:
        with self._lock:
            self._state[key] += 1
            self._state["done"] += 1
            if current is not None:
                self._state["current"] = current

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, full: bool = False, folder: str | None = None) -> bool:
        """folder: a library-relative folder to scan instead of the whole library."""
        return self._launch(self._scan, (full, folder), folder, "analyzing")

    def start_offset_search(self, album_dir: str) -> bool:
        """Look for a drive offset that makes the album match the CD databases."""
        return self._launch(self._search_offsets, (album_dir,), album_dir, "searching offsets")

    def _launch(self, job, args: tuple, folder: str | None, phase: str) -> bool:
        # One job at a time: scans and offset searches both read the whole
        # album and write its results.
        if self.running:
            return False
        self._stop.clear()
        self._update(state="running", total=0, done=0, cached=0, analyzed=0, errors=0, current=None,
                     started_at=time.time(), finished_at=None, message=None, folder=folder, phase=phase,
                     cd_done=0, cd_total=0)
        self._thread = threading.Thread(target=self._run, args=(job, args), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.running:
            self._stop.set()
            self._update(state="stopping")

    def _run(self, job, args: tuple) -> None:
        try:
            job(*args)
        except Exception as exc:
            self._update(message=f"{type(exc).__name__}: {exc}")
            db.log("scan", f"{'Scan' if job == self._scan else 'Offset search'} failed: {exc}")
        finally:
            self._update(state="idle", current=None, phase=None, finished_at=time.time())

    def _scan(self, full: bool, folder: str | None) -> None:
        root = Path(settings.get("library_path"))
        if not root.is_dir():
            raise FileNotFoundError(f"library folder {root} does not exist")
        exclude = [p.strip() for p in settings.get("exclude").splitlines() if p.strip()]
        files = list_flacs(root, exclude, folder or "")
        self._update(total=len(files))

        with db.connect() as conn:
            existing = {r["path"]: dict(r) for r in conn.execute(
                "SELECT path, size, mtime, streaminfo_md5, algo_version, verdict, error FROM tracks")}
            reusable = {r["audio_md5"]: dict(r) for r in conn.execute(
                "SELECT * FROM tracks WHERE algo_version = ? AND verdict IS NOT NULL AND audio_md5 IS NOT NULL",
                (ALGO_VERSION,))}

        todo = []
        for rel in files:
            if self._stop.is_set():
                break
            path = root / rel
            st = path.stat()
            stamp = {"size": st.st_size, "mtime": st.st_mtime}
            row = existing.get(rel)
            try:
                info = read_flac_info(path)
            except (NotFlacError, OSError):
                todo.append((rel, stamp))
                continue
            current = (not full and row is not None and row["algo_version"] == ALGO_VERSION
                       and row["verdict"] is not None and row["error"] is None)
            same_audio = row is not None and (
                row["streaminfo_md5"] == info.md5_hex if info.md5_hex
                else (row["size"], row["mtime"]) == (st.st_size, st.st_mtime))
            if current and same_audio:
                with db.connect() as conn:
                    _store(conn, rel, {**tag_columns(info), **stamp})
                self._bump("cached")
            elif not full and info.md5_hex and info.md5_hex in reusable:
                # Moved or renamed: same audio, analysis still valid.
                src = reusable[info.md5_hex]
                with db.connect() as conn:
                    _store(conn, rel, {**tag_columns(info), **stamp, "algo_version": ALGO_VERSION,
                                       "analyzed_at": src["analyzed_at"],
                                       **{c: db.row_to_dict(src)[c] for c in ANALYSIS_COLUMNS}})
                self._bump("cached")
            else:
                todo.append((rel, stamp))

        workers = max(1, int(settings.get("workers") or 1))
        soft = settings.soft_categories()
        with ProcessPoolExecutor(workers) as pool:
            futures = {pool.submit(analyze_track, str(root / rel), soft): (rel, stamp) for rel, stamp in todo}
            for future in as_completed(futures):
                rel, stamp = futures[future]
                if self._stop.is_set():
                    pool.shutdown(wait=True, cancel_futures=True)
                    break
                values = future.result()
                with db.connect() as conn:
                    _store(conn, rel, {**values, **stamp, "algo_version": ALGO_VERSION, "analyzed_at": db.now(),
                                       "cd_offset": None})
                self._bump("errors" if values.get("error") else "analyzed", rel)

        stopped = self._stop.is_set()
        gone = []
        if not stopped:
            seen = set(files)
            # A folder scan only knows about its own folder: files elsewhere
            # were not looked for, so they are not missing.
            gone = [p for p in existing if p not in seen and (folder is None or p.startswith(folder + "/"))]
            with db.connect() as conn:
                conn.executemany("UPDATE tracks SET missing = 1 WHERE path = ?", [(p,) for p in gone])
        album_dirs = None if folder is None else {album_dir_of(p) for p in files + gone}
        recompute_albums(album_dirs)
        s = self.status()
        where = f" of {folder}" if folder else ""
        db.log("scan", f"Scan{where} {'stopped' if stopped else 'finished'}: {s['analyzed']} analyzed, "
                       f"{s['cached']} unchanged, {s['errors']} errors.")
        if not stopped and settings.enabled("cd_lookup"):
            self._check_cds(album_dirs)

    def _check_cds(self, album_dirs: set[str] | None) -> None:
        # Lookups are cached, so albums checked before cost no network time.
        self._update(phase="checking CDs", current=None)
        report = cdverify.verify_albums(
            album_dirs, self._stop.is_set,
            lambda album, i, n: self._update(current=album, cd_done=i, cd_total=n))
        c = report["counts"]
        message = (f"CD check: {c['verified']} tracks verified, {c['mismatch']} not matching, "
                   f"{c['not-found']} on discs the databases do not know, "
                   f"{c['unsupported']} not checkable (not in CD format, or not cut on CD sectors).")
        if c["failed"]:
            message += f" {c['failed']} discs not checked: {report['error']}."
        db.log("cd-check", message)


    def _search_offsets(self, album_dir: str) -> None:
        report = cdverify.search_offsets(album_dir, self._stop.is_set,
                                         lambda path, i, n: self._update(current=path, cd_done=i, cd_total=n))
        lines = []
        for d in report:
            disc = f"disc {d['disc']}: " if len(report) > 1 else ""
            if d["shift"] is None:
                lines.append(f"{disc}no shift within {cdverify.OFFSET_RANGE} samples matches")
            else:
                lines.append(f"{disc}{d['verified']} of {d['tracks']} tracks match when shifted by "
                             f"{d['shift']:+d} samples")
        stopped = " (stopped)" if self._stop.is_set() else ""
        db.log("cd-check", f"Offset search for {album_dir}{stopped}: " + ("; ".join(lines) or "nothing to search")
               + ".")


scanner = Scanner()
