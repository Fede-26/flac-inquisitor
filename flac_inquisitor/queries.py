"""Read-side queries shared by the HTML pages and the JSON API.

Ignored files (by audio MD5) count as "ok" everywhere, so an accepted false
positive stops showing up without losing its analysis.
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath

import numpy as np

from flac_inquisitor import db, detect
from flac_inquisitor.detect import ALGO_VERSION

VERDICTS = ("fake", "suspect", "ok", "error")
PAGE_SIZE = 50

# Effective verdict of a track row, as SQL: errors first, then ignores. The
# final verdict (with album coherence) is computed when a scan ends; until
# then a freshly analyzed track shows its own verdict.
EFFECTIVE = """
CASE
  WHEN t.error IS NOT NULL OR t.verdict IS NULL THEN 'error'
  WHEN i.audio_md5 IS NOT NULL THEN 'ok'
  ELSE COALESCE(t.final_verdict, t.verdict)
END"""
RANK = f"CASE {EFFECTIVE} WHEN 'fake' THEN 3 WHEN 'error' THEN 2 WHEN 'suspect' THEN 1 ELSE 0 END"
BASE = "FROM tracks t LEFT JOIN ignores i ON i.audio_md5 = t.audio_md5 WHERE t.missing = 0"
RANK_NAME = {3: "fake", 2: "error", 1: "suspect", 0: "ok"}
# Outcome of the CD database check (cdverify), as a filter.
CD_FILTERS = {
    "verified": "t.cd_status = 'verified'",
    "not-verified": "(t.cd_status IS NULL OR t.cd_status != 'verified')",
    "mismatch": "t.cd_status = 'mismatch'",
    "not-found": "t.cd_status = 'not-found'",
    "unchecked": "(t.cd_status IS NULL OR t.cd_status = 'unsupported')",
}


@dataclass
class Filters:
    verdicts: list[str] = field(default_factory=lambda: ["fake", "suspect"])
    category: str | None = None
    fmt: str | None = None
    q: str | None = None
    cd: str | None = None
    sort: str = "severity"
    page: int = 1

    def where(self) -> tuple[str, list]:
        sql, args = [], []
        if self.verdicts:
            sql.append(f"{EFFECTIVE} IN ({','.join('?' * len(self.verdicts))})")
            args += self.verdicts
        if self.category:
            sql.append("EXISTS (SELECT 1 FROM json_each(t.final_categories) WHERE value = ?) AND i.audio_md5 IS NULL")
            args.append(self.category)
        if self.fmt:
            sql.append("(t.sample_rate || '/' || t.bits) = ?")
            args.append(self.fmt)
        if self.q:
            sql.append("(t.path LIKE ? OR t.artist LIKE ? OR t.album LIKE ? OR t.title LIKE ?)")
            args += [f"%{self.q}%"] * 4
        if self.cd in CD_FILTERS:
            sql.append(CD_FILTERS[self.cd])
        return (" AND " + " AND ".join(sql) if sql else ""), args


def _album_name(album_dir: str, tag: str | None) -> str:
    return tag or PurePosixPath(album_dir).name


def _artist_name(album_dir: str, tag: str | None) -> str:
    parent = PurePosixPath(album_dir).parent
    return tag or (parent.name if str(parent) != "." else "")


def summary() -> dict:
    with db.connect() as conn:
        rows = conn.execute(f"SELECT {EFFECTIVE} AS v, COUNT(*) AS n {BASE} GROUP BY v").fetchall()
        by_verdict = {r["v"]: r["n"] for r in rows}
        cats = conn.execute(
            f"SELECT c.value AS category, COUNT(*) AS n {BASE.replace('WHERE', ', json_each(t.final_categories) c WHERE')} "
            "AND i.audio_md5 IS NULL GROUP BY c.value ORDER BY n DESC").fetchall()
        albums = conn.execute(f"SELECT COUNT(*) FROM (SELECT t.album_key, MAX({RANK}) r {BASE} "
                              "GROUP BY t.album_key HAVING r IN (1, 3))").fetchone()[0]
        outdated = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0 AND algo_version != ?",
                                (ALGO_VERSION,)).fetchone()[0]
        ignored = conn.execute(f"SELECT COUNT(*) {BASE} AND i.audio_md5 IS NOT NULL").fetchone()[0]
        cd_verified = conn.execute(f"SELECT COUNT(*) {BASE} AND t.cd_status = 'verified'").fetchone()[0]
        cd_albums = conn.execute(f"SELECT COUNT(*) FROM (SELECT t.album_key {BASE} GROUP BY t.album_key "
                                 "HAVING MIN(t.cd_status = 'verified') = 1)").fetchone()[0]
    return {
        "tracks": sum(by_verdict.values()),
        "by_verdict": {v: by_verdict.get(v, 0) for v in VERDICTS},
        "by_category": {r["category"]: r["n"] for r in cats},
        "flagged_albums": albums,
        "outdated": outdated,
        "ignored": ignored,
        "cd_verified": cd_verified,
        "cd_albums": cd_albums,
    }


ALBUM_SORTS = {
    "severity": "rank DESC, worst_cutoff ASC, artist, album",
    "artist": "artist, album",
    "album": "album, artist",
    "cutoff": "worst_cutoff IS NULL, worst_cutoff ASC",
}


def list_albums(f: Filters) -> dict:
    where, args = f.where()
    order = ALBUM_SORTS.get(f.sort, ALBUM_SORTS["severity"])
    # Filters select which albums appear; the album row always describes all
    # its tracks, so the verdict shown is the album's real worst case.
    sql = f"""
        WITH hit AS (SELECT DISTINCT t.album_key {BASE}{where})
        SELECT t.album_key, t.album_dir,
               MAX(t.artist) AS artist, MAX(t.album) AS album, COUNT(*) AS tracks,
               MAX({RANK}) AS rank,
               SUM({EFFECTIVE} = 'fake') AS fakes, SUM({EFFECTIVE} = 'suspect') AS suspects,
               SUM(t.cd_status = 'verified') AS cd_verified,
               MIN(json_extract(t.metrics, '$.cutoff_hz')) AS worst_cutoff,
               GROUP_CONCAT(DISTINCT t.sample_rate || '/' || t.bits) AS formats,
               (SELECT GROUP_CONCAT(DISTINCT c.value) FROM tracks t2, json_each(t2.final_categories) c
                  LEFT JOIN ignores i2 ON i2.audio_md5 = t2.audio_md5
                  WHERE t2.album_key = t.album_key AND t2.missing = 0 AND i2.audio_md5 IS NULL) AS categories
        {BASE} AND t.album_key IN (SELECT album_key FROM hit)
        GROUP BY t.album_key
        ORDER BY {order}
        LIMIT ? OFFSET ?"""
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(DISTINCT t.album_key) {BASE}{where}", args).fetchone()[0]
        rows = conn.execute(sql, args + [PAGE_SIZE, (max(f.page, 1) - 1) * PAGE_SIZE]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["verdict"] = RANK_NAME[d.pop("rank")]
        d["artist"] = _artist_name(d["album_dir"], d["artist"])
        d["album"] = _album_name(d["album_dir"], d["album"])
        d["categories"] = sorted(d["categories"].split(",")) if d["categories"] else []
        d["formats"] = sorted(d["formats"].split(",")) if d["formats"] else []
        items.append(d)
    return {"items": items, "total": total, "page": f.page, "pages": max(1, -(-total // PAGE_SIZE))}


def _track(row) -> dict:
    d = db.row_to_dict(row)
    d["effective_verdict"] = d.pop("eff")
    d["ignored"] = bool(d.pop("ignored"))
    d["name"] = PurePosixPath(d["path"]).name
    return d


def list_tracks(f: Filters) -> dict:
    where, args = f.where()
    order = {"severity": f"{RANK} DESC, t.path", "artist": "t.artist, t.path", "album": "t.album, t.path",
             "cutoff": "json_extract(t.metrics, '$.cutoff_hz') IS NULL, json_extract(t.metrics, '$.cutoff_hz')"}
    sql = (f"SELECT t.*, {EFFECTIVE} AS eff, i.audio_md5 IS NOT NULL AS ignored {BASE}{where} "
           f"ORDER BY {order.get(f.sort, order['severity'])} LIMIT ? OFFSET ?")
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {BASE}{where}", args).fetchone()[0]
        rows = conn.execute(sql, args + [PAGE_SIZE, (max(f.page, 1) - 1) * PAGE_SIZE]).fetchall()
    return {"items": [_track(r) for r in rows], "total": total, "page": f.page,
            "pages": max(1, -(-total // PAGE_SIZE))}


def get_album(album_key: str) -> dict | None:
    with db.connect() as conn:
        rows = conn.execute(f"SELECT t.*, {EFFECTIVE} AS eff, i.audio_md5 IS NOT NULL AS ignored {BASE} "
                            "AND t.album_key = ? ORDER BY t.disc, t.track, t.path", (album_key,)).fetchall()
    if not rows:
        return None
    tracks = [_track(r) for r in rows]
    rank = max({"fake": 3, "error": 2, "suspect": 1, "ok": 0}[t["effective_verdict"]] for t in tracks)
    first = tracks[0]
    lead = next((t for t in tracks if t["effective_verdict"] == RANK_NAME[rank] and rank), None)
    checked = [t for t in tracks if t["cd_status"] in ("verified", "mismatch", "not-found")]
    return {
        "album_key": album_key,
        "cd_verified": sum(t["cd_status"] == "verified" for t in tracks),
        "cd_checked": len(checked),
        "cd_mismatch": sum(t["cd_status"] == "mismatch" for t in tracks),
        "album_dir": first["album_dir"],
        "artist": _artist_name(first["album_dir"], first["artist"]),
        "album": _album_name(first["album_dir"], first["album"]),
        "verdict": RANK_NAME[rank],
        "summary": (f"{lead['name']}: {lead['summary'] or lead['error']}" if lead else
                    "Every track looks like genuine lossless audio."),
        "categories": sorted({c for t in tracks if not t["ignored"] for c in (t["final_categories"] or [])}),
        "tracks": tracks,
    }


def get_track(track_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(f"SELECT t.*, {EFFECTIVE} AS eff, i.audio_md5 IS NOT NULL AS ignored {BASE} "
                           "AND t.id = ?", (track_id,)).fetchone()
    return _track(row) if row else None


def formats() -> list[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT sample_rate || '/' || bits AS f FROM tracks "
                            "WHERE missing = 0 AND sample_rate IS NOT NULL ORDER BY sample_rate, bits").fetchall()
    return [r["f"] for r in rows]


# Accuracy report. Tracks verified against the CD databases are bit-identical
# to pressed CDs: whatever the detector says about them, they are not
# transcodes made by whoever shared the file. They show where each threshold
# sits against the real library.
ACCURACY_METRICS = [
    # key, label, unit, which tail matters, thresholds (label, value)
    ("cutoff_hz", "Hard cutoff (tracks that have one)", "kHz", "low",
     [("fake below", detect.CUTOFF_FAKE_HZ), ("suspect below", detect.CUTOFF_SUSPECT_HZ)]),
    ("cutoff_drop_db", "Steepest edge found", "dB", "high", [("cutoff from", detect.CUTOFF_MIN_DROP_DB)]),
    ("hole_ratio", "Loud moments with band holes", "%", "high",
     [("suspect from", detect.HOLES_SUSPECT), ("fake from", detect.HOLES_FAKE)]),
    # Counted only when the holes cover at least SHORT_HOLES_MIN_COVER of the
    # track; the tail shows how close genuine tracks come.
    ("short_mid_hole_ratio", "Loud moments with band holes, short frames, mid", "%", "high",
     [("suspect from", detect.SHORT_HOLES_SUSPECT), ("fake from", detect.SHORT_HOLES_FAKE)]),
    ("short_side_hole_ratio", "Loud moments with band holes, short frames, side", "%", "high",
     [("suspect from", detect.SHORT_HOLES_SUSPECT), ("fake from", detect.SHORT_HOLES_FAKE)]),
    ("blocks_with_unused_bits", "Blocks with unused low bits", "%", "high",
     [("suspect from", detect.LOSSYWAV_SUSPECT_RATIO), ("fake from", detect.LOSSYWAV_FAKE_RATIO)]),
    ("frame_top_hz", "Per-frame bandwidth, 10th percentile", "kHz", "low",
     [("candidate: below", detect.FRAME_TOP_SHOWN_HZ)]),
]


def _quantiles(values: list[float], tail: str) -> list[tuple[str, float]]:
    points = (0, 1, 5, 50) if tail == "low" else (50, 95, 99, 100)
    names = {0: "min", 100: "max"}
    return [(names.get(p, f"p{p}"), float(np.percentile(values, p))) for p in points]


def accuracy() -> dict:
    # The verdict of the checks, not the effective one: verified tracks are
    # usually ignored, and the report is about what the checks say.
    with db.connect() as conn:
        rows = [db.row_to_dict(r) for r in conn.execute(
            "SELECT t.id, t.path, t.album_key, t.title, t.artist, t.album, t.verdict, t.summary, t.evidence, "
            "t.metrics, t.cd_ar, t.cd_ctdb, t.algo_version, COALESCE(t.final_verdict, t.verdict) AS checked, "
            f"i.audio_md5 IS NOT NULL AS ignored {BASE} "
            "AND t.cd_status = 'verified' AND t.verdict IS NOT NULL ORDER BY t.path")]
    checks: dict[str, dict[str, int]] = {}
    for r in rows:
        for e in r["evidence"] or []:
            if e["result"] in ("warn", "fail"):
                counts = checks.setdefault(e["check"], {"warn": 0, "fail": 0})
                counts[e["result"]] += 1
    metrics = []
    for key, label, unit, tail, thresholds in ACCURACY_METRICS:
        values = [v for r in rows if (v := (r["metrics"] or {}).get(key)) is not None]
        if not values:
            continue
        beyond = [(name, limit, sum((v < limit) if tail == "low" else (v >= limit) for v in values))
                  for name, limit in thresholds]
        metrics.append({"key": key, "label": label, "unit": unit, "count": len(values),
                        "quantiles": _quantiles(values, tail), "beyond": beyond})
    flagged = [{**r, "name": PurePosixPath(r["path"]).name} for r in rows if r["checked"] in ("fake", "suspect")]
    return {
        "tracks": len(rows),
        "albums": len({r["album_key"] for r in rows}),
        "outdated": sum(r["algo_version"] != ALGO_VERSION for r in rows),
        "ignored": sum(bool(r["ignored"]) for r in rows),
        "by_verdict": {v: sum(r["checked"] == v for r in rows) for v in ("ok", "suspect", "fake")},
        "checks": sorted(checks.items(), key=lambda kv: -(kv[1]["warn"] + kv[1]["fail"])),
        "metrics": metrics,
        "flagged": flagged,
    }
