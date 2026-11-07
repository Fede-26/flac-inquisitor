"""Move files out of the library and back.

Files go to <quarantine>/<date>/<path relative to the library>, so the
quarantine mirrors the library and stays readable without the app. Every move
is recorded in the database and in a manifest.json per date folder.

Nothing is ever overwritten: a restore that would replace a file first moves
that file to the quarantine too.
"""

import errno
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import date
from pathlib import Path, PurePosixPath

from flac_inquisitor import db, settings
from flac_inquisitor.flacmeta import NotFlacError, read_flac_info


class QuarantineError(RuntimeError):
    pass


def _roots() -> tuple[Path, Path]:
    return Path(settings.get("library_path")), Path(settings.get("quarantine_path"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def move_file(src: Path, dst: Path) -> None:
    """rename() when possible; across mounts, copy, verify, then delete."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise QuarantineError(f"{dst} already exists")
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    tmp = dst.with_name(dst.name + ".partial")
    shutil.copy2(src, tmp)
    if _sha256(tmp) != _sha256(src):
        tmp.unlink()
        raise QuarantineError(f"copy of {src} does not match the original; nothing was moved")
    tmp.replace(dst)
    src.unlink()


def _free_path(path: Path) -> Path:
    """Same file quarantined twice on one day: keep both."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise QuarantineError(f"too many copies of {path}")


def _update_manifest(folder: Path, entries: list[dict]) -> None:
    manifest = folder / "manifest.json"
    data = json.loads(manifest.read_text()) if manifest.exists() else []
    by_stored = {e["stored_path"]: e for e in data}
    for e in entries:
        by_stored[e["stored_path"]] = e
    manifest.write_text(json.dumps(list(by_stored.values()), indent=2, ensure_ascii=False))


def _prune_empty(folder: Path, stop: Path) -> None:
    """Remove folders emptied by a move, up to (not including) stop."""
    while folder != stop and folder.is_relative_to(stop):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _sidecars(audio: Path) -> list[Path]:
    """Files next to a track that belong to it: same name, other extension."""
    return sorted(p for p in audio.parent.glob(glob_escape(audio.stem) + ".*") if p != audio and p.is_file())


def glob_escape(text: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", text)


def _track_rows(conn, where: str, args: list) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT * FROM tracks WHERE missing = 0 AND {where}", args)]


def quarantine(track_ids: list[int] | None = None, album_keys: list[str] | None = None,
               paths: list[str] | None = None) -> dict:
    """paths: library-relative files that were never scanned (a restore
    replacing a freshly downloaded file); they are moved and logged too."""
    library, qroot = _roots()
    batch = uuid.uuid4().hex[:12]
    today = date.today().isoformat()
    folder = qroot / today

    with db.connect() as conn:
        if album_keys:
            marks = ",".join("?" * len(album_keys))
            tracks = _track_rows(conn, f"album_key IN ({marks})", album_keys)
            album_dirs = sorted({t["album_dir"] for t in tracks})
        else:
            marks = ",".join("?" * len(track_ids or []))
            tracks = _track_rows(conn, f"id IN ({marks})", list(track_ids or []))
            album_dirs = []
    if not tracks and not paths:
        raise QuarantineError("Nothing to quarantine: the files are no longer in the library.")

    # Work out every file to move before touching anything.
    by_path = {t["path"]: t for t in tracks}
    moves: list[tuple[str, dict | None]] = []  # (relative path, track row or None for sidecars)
    if album_dirs:
        for album_dir in album_dirs:
            for p in sorted((library / album_dir).rglob("*")):
                if p.is_file():
                    rel = p.relative_to(library).as_posix()
                    moves.append((rel, by_path.get(rel)))
    else:
        for t in tracks:
            audio = library / t["path"]
            moves.append((t["path"], t))
            moves += [(p.relative_to(library).as_posix(), None) for p in _sidecars(audio)]

    for rel in paths or []:
        moves.append((rel, None))
        moves += [(p.relative_to(library).as_posix(), None) for p in _sidecars(library / rel)]
    unscanned_audio = {rel for rel in paths or [] if rel.lower().endswith(".flac")}

    entries, errors = [], []
    for rel, track in moves:
        src = library / rel
        if not src.exists():
            errors.append(f"{rel}: file not found")
            continue
        dst = _free_path(folder / rel)
        try:
            move_file(src, dst)
        except (OSError, QuarantineError) as exc:
            errors.append(f"{rel}: {exc}")
            continue
        owner = track or by_path.get(str(PurePosixPath(rel).with_suffix(".flac")))
        entries.append({
            "batch": batch, "original_path": rel, "stored_path": dst.relative_to(qroot).as_posix(),
            "is_audio": int(track is not None or rel in unscanned_audio),
            "album_dir": (owner or {}).get("album_dir") or str(PurePosixPath(rel).parent),
            "artist": (owner or {}).get("artist"), "album": (owner or {}).get("album"),
            "title": (owner or {}).get("title"), "disc": (owner or {}).get("disc"),
            "track": (owner or {}).get("track"), "audio_md5": (owner or {}).get("audio_md5"),
            "verdict": (owner or {}).get("final_verdict"),
            "categories": (owner or {}).get("final_categories"),
            "created_at": db.now(),
        })

    entry_ids = []
    with db.connect() as conn:
        for e in entries:
            cols = ", ".join(e)
            cur = conn.execute(f"INSERT INTO quarantine ({cols}) VALUES ({','.join('?' * len(e))})", list(e.values()))
            if e["is_audio"]:
                entry_ids.append(cur.lastrowid)
                conn.execute("UPDATE tracks SET missing = 1 WHERE path = ?", (e["original_path"],))
    if entries:
        _update_manifest(folder, [{**e, "status": "quarantined"} for e in entries])
    for album_dir in album_dirs:
        _prune_empty(library / album_dir, library)
    for t in tracks if not album_dirs else []:
        _prune_empty((library / t["path"]).parent, library)

    audio = [e for e in entries if e["is_audio"]]
    if audio:
        db.log("quarantine", f"Quarantined {len(audio)} track(s) from "
                             f"{', '.join(sorted({e['album_dir'] for e in audio}))[:200]}.")
    return {"batch": batch, "moved": len(entries), "tracks": len(audio), "errors": errors,
            "entry_ids": entry_ids, "album_dirs": sorted({e["album_dir"] for e in entries})}


def entries(status: str | None = "quarantined") -> list[dict]:
    sql = "SELECT * FROM quarantine"
    args: list = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY artist, album, album_dir, disc, track, original_path"
    with db.connect() as conn:
        return [db.row_to_dict(r) | {"categories": json.loads(r["categories"]) if r["categories"] else []}
                for r in conn.execute(sql, args)]


def grouped(status: str | None = "quarantined") -> list[dict]:
    """Entries grouped by album, for the Quarantine page and the copy list."""
    groups: dict[str, dict] = {}
    for e in entries(status):
        g = groups.setdefault(e["album_dir"], {
            "album_dir": e["album_dir"], "artist": e["artist"] or str(PurePosixPath(e["album_dir"]).parent.name),
            "album": e["album"] or PurePosixPath(e["album_dir"]).name, "tracks": [], "others": [],
            "created_at": e["created_at"]})
        (g["tracks"] if e["is_audio"] else g["others"]).append(e)
        g["created_at"] = min(g["created_at"], e["created_at"])
    return sorted(groups.values(), key=lambda g: (g["artist"].lower(), g["album"].lower()))


def _get(ids: list[int]) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(f"SELECT * FROM quarantine WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return [dict(r) for r in rows]


def _with_sidecars(selected: list[dict]) -> list[dict]:
    """A restored track brings back the sidecars quarantined with it."""
    with db.connect() as conn:
        out = {e["id"]: e for e in selected}
        for e in selected:
            if not e["is_audio"]:
                continue
            stem = str(PurePosixPath(e["original_path"]).with_suffix(""))
            for r in conn.execute("SELECT * FROM quarantine WHERE batch = ? AND is_audio = 0 AND status = "
                                  "'quarantined' AND original_path LIKE ?", (e["batch"], stem + ".%")):
                out[r["id"]] = dict(r)
    return list(out.values())


def _track_key(path: Path) -> tuple | None:
    try:
        tags = read_flac_info(path).tags
    except (NotFlacError, OSError):
        tags = {}
    number = lambda key: re.match(r"\d+", (tags.get(key) or [""])[0])  # noqa: E731
    disc, track = number("DISCNUMBER"), number("TRACKNUMBER")
    if track:
        return int(disc.group()) if disc else 1, int(track.group())
    m = re.match(r"(\d+)", path.name)
    return (None, int(m.group(1))) if m else None


def conflicts(selected: list[dict]) -> list[dict]:
    library, qroot = _roots()
    found = []
    for e in selected:
        target = library / e["original_path"]
        if target.exists():
            found.append({"entry_id": e["id"], "type": "path-taken", "path": e["original_path"],
                          "current": e["original_path"]})
            continue
        if not target.parent.is_dir():
            found.append({"entry_id": e["id"], "type": "folder-missing", "path": e["original_path"]})
            continue
        if not e["is_audio"]:
            continue
        mine = _track_key(qroot / e["stored_path"])
        if mine is None:
            continue
        for other in sorted(target.parent.glob("*.flac")):
            key = _track_key(other)
            same = key == mine if None not in (key or (None,))[:1] and None not in mine[:1] else (
                key is not None and key[1] == mine[1])
            if same:
                found.append({"entry_id": e["id"], "type": "same-track", "path": e["original_path"],
                              "current": other.relative_to(library).as_posix()})
    return found


def restore(ids: list[int], mode: str = "check") -> dict:
    """mode: check (refuse on any conflict), replace (quarantine what is in
    the way, then restore), force (only create missing folders)."""
    library, qroot = _roots()
    selected = [e for e in _with_sidecars(_get(ids)) if e["status"] == "quarantined"]
    if not selected:
        raise QuarantineError("Nothing to restore.")
    found = conflicts(selected)
    blocking = [c for c in found if c["type"] != "folder-missing" or mode == "check"]
    if found and mode == "check":
        return {"restored": 0, "conflicts": found}
    if blocking and mode == "force":
        return {"restored": 0, "conflicts": blocking}
    if mode == "replace":
        in_the_way = sorted({c["current"] for c in found if c.get("current")})
        if in_the_way:
            with db.connect() as conn:
                rows = conn.execute(f"SELECT id FROM tracks WHERE missing = 0 AND path IN "
                                    f"({','.join('?' * len(in_the_way))})", in_the_way).fetchall()
            known = [r["id"] for r in rows]
            with db.connect() as conn:
                scanned = {r["path"] for r in conn.execute(
                    f"SELECT path FROM tracks WHERE id IN ({','.join('?' * len(known))})", known)}
            quarantine(track_ids=known or None, paths=[p for p in in_the_way if p not in scanned])

    restored, errors = [], []
    for e in selected:
        try:
            move_file(qroot / e["stored_path"], library / e["original_path"])
        except (OSError, QuarantineError) as exc:
            errors.append(f"{e['original_path']}: {exc}")
            continue
        restored.append(e)
        _prune_empty((qroot / e["stored_path"]).parent, qroot)
    with db.connect() as conn:
        for e in restored:
            conn.execute("UPDATE quarantine SET status = 'restored', resolved_at = ? WHERE id = ?", (db.now(), e["id"]))
            if e["is_audio"]:
                conn.execute("UPDATE tracks SET missing = 0 WHERE path = ?", (e["original_path"],))
    for day in {PurePosixPath(e["stored_path"]).parts[0] for e in restored}:
        if (qroot / day).is_dir():
            _update_manifest(qroot / day, [{**e, "status": "restored"} for e in restored
                                           if e["stored_path"].startswith(day + "/")])
    audio = [e for e in restored if e["is_audio"]]
    if audio:
        db.log("restore", f"Restored {len(audio)} track(s) to "
                          f"{', '.join(sorted({e['album_dir'] for e in audio}))[:200]}.")
    return {"restored": len(restored), "tracks": len(audio), "errors": errors, "conflicts": [],
            "album_dirs": sorted({e["album_dir"] for e in restored})}


def delete(ids: list[int]) -> dict:
    _, qroot = _roots()
    selected = [e for e in _with_sidecars(_get(ids)) if e["status"] == "quarantined"]
    deleted = []
    for e in selected:
        path = qroot / e["stored_path"]
        path.unlink(missing_ok=True)
        _prune_empty(path.parent, qroot)
        deleted.append(e)
    with db.connect() as conn:
        for e in deleted:
            conn.execute("UPDATE quarantine SET status = 'deleted', resolved_at = ? WHERE id = ?", (db.now(), e["id"]))
    audio = [e for e in deleted if e["is_audio"]]
    db.log("delete", f"Deleted {len(audio)} quarantined track(s) for good.")
    return {"deleted": len(deleted), "tracks": len(audio)}


def stored_file(entry_id: int) -> Path | None:
    _, qroot = _roots()
    rows = _get([entry_id])
    if not rows or rows[0]["status"] != "quarantined":
        return None
    path = qroot / rows[0]["stored_path"]
    return path if path.is_file() else None


def copy_list() -> str:
    """Plain text for re-downloading: one line per album."""
    lines = []
    for g in grouped():
        numbers = sorted(t["track"] for t in g["tracks"] if t["track"])
        detail = f" (tracks {', '.join(map(str, numbers))})" if numbers and not g["others"] else ""
        lines.append(f"{g['artist']} – {g['album']}{detail}")
    return "\n".join(lines)
