"""Check CD rips against AccurateRip and CTDB (CUETools DB).

A match means the audio is bit-identical to what other people ripped from a
pressed CD with the same layout: the file is a real CD rip, not a transcode
(the CD itself may still have been mastered from a lossy source). No match
proves nothing on its own: a different pressing, a rip not corrected for the
drive offset, a rip error or a disc missing from the databases all look alike.

Checksums are collected per track while the analyzer decodes it, so the check
needs no second decode. They are kept in pieces (see CDChecksums) because the
values the databases expect depend on where the track sits on the disc.

Only offset-corrected rips can match: neither database is searched for other
offsets. Hidden track one audio and data tracks (enhanced CDs) are not
supported; such discs are not found.

AccurateRip formulas follow the cdtoc Rust crate and the flaccuraterip Haskell
package; CTDB ones the CUETools sources (CUETools.AccurateRip/AccurateRip.cs,
CUETools.CTDB/CUEToolsDB.cs).
"""

import json
import re
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from flac_inquisitor import __version__, db

SECTOR = 588  # stereo samples per CD sector
AR_SKIP = 5 * SECTOR  # AccurateRip ignores 5 sectors at each end of the disc
CTDB_STRIDE = 10 * SECTOR
CTDB_HEAD = CTDB_STRIDE  # CTDB ignores 10 sectors at the start...
CTDB_TAIL_MAX = 20 * SECTOR  # ...and 10 to 19 at the end, depending on the disc length
MASK = 0xFFFFFFFF

USER_AGENT = f"flac-inquisitor/{__version__} (+https://github.com/Fede-26/flac-inquisitor)"
CACHE_DAYS = 30
# Pauses between requests to one service. MusicBrainz asks for at most one
# request per second; CUETools waits 0.5 s between AccurateRip requests.
PAUSE = {"musicbrainz.org": 1.1, "accuraterip.com": 0.5, "db.cue.tools": 0.5}

STATUSES = ("verified", "mismatch", "not-found", "unsupported")


def is_cd_format(sample_rate, bits, channels) -> bool:
    return (sample_rate, bits, channels) == (44_100, 16, 2)


class CDChecksums:
    """Accumulates, over one decode of a track, what both databases need.

    AccurateRip: v1 and v2 sums over the whole track, over its first 2939
    samples and over its last 2940 (the parts skipped when the track opens or
    closes the disc). The sums are additive, so the skipped parts are
    subtracted later.

    CTDB: CRC-32 of the 16-bit little-endian PCM, in pieces: the first 5880
    samples, the middle, then each of the last 20 sectors. The disc checksum
    skips a tail of 10 to 19 sectors depending on the disc length, unknown
    until the whole disc is known; zlib.crc32_combine joins the pieces then.
    """

    def __init__(self, n_samples: int):
        self.n = n_samples
        self.pos = 0
        self.ar = [0] * 6
        self.ctdb_ok = n_samples >= CTDB_HEAD + CTDB_TAIL_MAX
        tail_start = n_samples - CTDB_TAIL_MAX
        self.bounds = [0, CTDB_HEAD, tail_start] + [tail_start + SECTOR * (j + 1) for j in range(20)]
        self.crcs = [0] * (len(self.bounds) - 1)

    def update(self, samples: np.ndarray) -> None:
        """samples: frames x 2, 16-bit values in any integer dtype."""
        n = len(samples)
        idx = np.arange(self.pos, self.pos + n, dtype=np.uint64)
        u = samples.astype(np.int64) & 0xFFFF
        words = (u[:, 0] | (u[:, 1] << 16)).astype(np.uint64)
        product = (idx + np.uint64(1)) * words
        v2_terms = (product & np.uint64(MASK)) + (product >> np.uint64(32))
        # uint64 sums wrap modulo 2**64, which keeps them right modulo 2**32.
        head = idx < np.uint64(AR_SKIP - 1)
        tail = idx >= np.uint64(max(self.n - AR_SKIP, 0))
        for k, p in enumerate((product, v2_terms)):
            self.ar[k] = (self.ar[k] + int(p.sum())) & MASK
            self.ar[2 + k] = (self.ar[2 + k] + int(p[head].sum())) & MASK
            self.ar[4 + k] = (self.ar[4 + k] + int(p[tail].sum())) & MASK

        if self.ctdb_ok:
            data = samples.astype("<i2").tobytes()
            start = self.pos
            while start < self.pos + n:
                piece = next(i for i in range(len(self.crcs)) if start < self.bounds[i + 1])
                end = min(self.pos + n, self.bounds[piece + 1])
                self.crcs[piece] = zlib.crc32(data[4 * (start - self.pos) : 4 * (end - self.pos)], self.crcs[piece])
                start = end
        self.pos += n

    def result(self) -> dict | None:
        if self.pos != self.n:
            return None
        return {"samples": self.n, "ar": self.ar, "ctdb": self.crcs if self.ctdb_ok else None}


def ar_track_crc(cd: dict, first: bool, last: bool) -> tuple[int, int]:
    v1, v2, h1, h2, t1, t2 = cd["ar"]
    if first:
        v1, v2 = v1 - h1, v2 - h2
    if last:
        v1, v2 = v1 - t1, v2 - t2
    return v1 & MASK, v2 & MASK


def ctdb_track_crc(cd: dict, skip_head: bool, tail_sectors: int | None) -> int:
    """CRC of the track without its first 5880 samples (skip_head) and without
    its last tail_sectors sectors (10 to 19), or of the whole track."""
    crcs, n = cd["ctdb"], cd["samples"]
    lengths = [CTDB_HEAD, n - CTDB_HEAD - CTDB_TAIL_MAX] + [SECTOR] * 20
    keep = len(lengths) - (tail_sectors or 0)
    crc = 0
    for i in range(1 if skip_head else 0, keep):
        crc = zlib.crc32_combine(crc, crcs[i], 4 * lengths[i])
    return crc


# Lookups.

class LookupFailed(Exception):
    """A database could not be reached; the disc is left unchecked."""


_last_call: dict[str, float] = {}


def fetch(url: str) -> bytes | None:
    """GET with a cache in the database; None when the server has no entry."""
    with db.connect() as conn:
        row = conn.execute("SELECT body, fetched_at FROM cd_lookups WHERE url = ?", (url,)).fetchone()
    if row and time.time() - row["fetched_at"] < CACHE_DAYS * 86400:
        return row["body"]
    host = next((h for h in PAUSE if h in url), None)
    if host:
        time.sleep(max(0.0, PAUSE[host] - (time.time() - _last_call.get(host, 0.0))))
        _last_call[host] = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise LookupFailed(f"{url.split('/')[2]}: HTTP {exc.code}") from exc
        body = None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LookupFailed(f"{url.split('/')[2]}: {exc}") from exc
    with db.connect() as conn:
        conn.execute("INSERT INTO cd_lookups (url, body, fetched_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(url) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at",
                     (url, body, time.time()))
    return body


@dataclass
class Toc:
    offsets: list[int]  # track starts in sectors, including the 150-sector lead-in
    leadout: int


def toc_from_lengths(lengths: list[int]) -> Toc:
    offsets, pos = [], 150
    for n in lengths:
        offsets.append(pos)
        pos += n // SECTOR
    return Toc(offsets, pos)


def musicbrainz_tocs(release: str, disc: int, lengths: list[int]) -> list[Toc]:
    """Layouts MusicBrainz knows for the disc that agree with the files. The
    first track may be shorter by a pregap the rip left out."""
    body = fetch(f"https://musicbrainz.org/ws/2/release/{release}?inc=discids&fmt=json")
    if not body:
        return []
    sectors = [n // SECTOR for n in lengths]
    out = []
    for medium in json.loads(body).get("media", []):
        if medium.get("position") != disc:
            continue
        for d in medium.get("discs", []):
            offsets, leadout = d["offsets"], d["sectors"]
            if len(offsets) != len(sectors):
                continue
            toc_lengths = [b - a for a, b in zip(offsets, offsets[1:] + [leadout])]
            if toc_lengths[1:] == sectors[1:] and toc_lengths[0] in (sectors[0], sectors[0] + offsets[0] - 150):
                out.append(Toc(offsets, leadout))
    return out


def _cddb_id(toc: Toc) -> int:
    digits = sum(sum(int(d) for d in str(o // 75)) for o in toc.offsets)
    total = toc.leadout // 75 - toc.offsets[0] // 75
    return ((digits % 255) << 24) | (total << 8) | len(toc.offsets)


def accuraterip_url(toc: Toc) -> str:
    rel = [o - 150 for o in toc.offsets] + [toc.leadout - 150]
    id1 = sum(rel) & MASK
    id2 = sum(max(o, 1) * i for i, o in enumerate(rel, start=1)) & MASK
    hex1 = f"{id1:08x}"
    return (f"http://www.accuraterip.com/accuraterip/{hex1[-1]}/{hex1[-2]}/{hex1[-3]}/"
            f"dBAR-{len(toc.offsets):03d}-{hex1}-{id2:08x}-{_cddb_id(toc):08x}.bin")


def parse_dbar(data: bytes, tracks: int) -> list[dict[int, int]]:
    """Per track: {crc: total confidence} over all pressings in the file."""
    size = 13 + 9 * tracks
    out: list[dict[int, int]] = [{} for _ in range(tracks)]
    for start in range(0, len(data) - size + 1, size):
        chunk = data[start + 13 : start + size]
        for t in range(tracks):
            crc = int.from_bytes(chunk[9 * t + 1 : 9 * t + 5], "little")
            if crc:
                out[t][crc] = out[t].get(crc, 0) + chunk[9 * t]
    return out


def ctdb_toc(toc: Toc) -> str:
    return ":".join(str(o - 150) for o in toc.offsets + [toc.leadout])


def ctdb_url(toc: Toc) -> str:
    # db.cuetools.net serves a certificate for another name; db.cue.tools is
    # the same service.
    return f"https://db.cue.tools/lookup2.php?version=3&ctdb=1&fuzzy=1&metadata=none&toc={ctdb_toc(toc)}"


def parse_ctdb(xml: bytes) -> list[dict]:
    entries = []
    for tag in re.findall(r"<entry\b[^>]*>", xml.decode("utf-8", "replace")):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if "crc32" not in a or "toc" not in a:
            continue
        entries.append({"crc32": int(a["crc32"], 16), "confidence": int(a.get("confidence", 0)), "toc": a["toc"],
                        "trackcrcs": [int(x, 16) for x in a.get("trackcrcs", "").split()]})
    return entries


# Matching.

def _accuraterip(toc: Toc, cds: list[dict]) -> list[int] | None:
    """Confidence per track, or None when AccurateRip does not know the disc."""
    data = fetch(accuraterip_url(toc))
    if not data:
        return None
    known = parse_dbar(data, len(cds))
    out = []
    for i, (cd, entries) in enumerate(zip(cds, known)):
        v1, v2 = ar_track_crc(cd, first=i == 0, last=i == len(cds) - 1)
        out.append(entries.get(v1) or entries.get(v2) or 0)
    return out


def _ctdb(toc: Toc, cds: list[dict]) -> list[int] | None:
    """Confidence per track, or None when CTDB does not know the disc."""
    if any(cd["ctdb"] is None for cd in cds):
        return None
    xml = fetch(ctdb_url(toc))
    entries = parse_ctdb(xml) if xml else []
    if not entries:
        return None
    total = sum(cd["samples"] for cd in cds)
    tail_sectors = 10 + (total % CTDB_STRIDE) // SECTOR
    n = len(cds)
    tracks = [ctdb_track_crc(cd, skip_head=i == 0, tail_sectors=tail_sectors if i == n - 1 else None)
              for i, cd in enumerate(cds)]
    disc = tracks[0]
    for i in range(1, n):
        length = cds[i]["samples"] - (tail_sectors * SECTOR if i == n - 1 else 0)
        disc = zlib.crc32_combine(disc, tracks[i], 4 * length)
    audio = toc.leadout - toc.offsets[0]
    out = [0] * n
    for e in entries:
        fields = [int(x.lstrip("-")) for x in e["toc"].split(":")]
        # Fuzzy results include other layouts; only the same audio length compares.
        if fields[-1] - fields[0] != audio:
            continue
        for i in range(n):
            if e["crc32"] == disc or (len(e["trackcrcs"]) == n and e["trackcrcs"][i] == tracks[i]):
                out[i] += e["confidence"]
    return out


def verify_disc(cds: list[dict], release: str | None = None, disc_no: int = 1) -> list[dict]:
    """cds: the tracks of one disc in order, each the "cd" metrics of the
    analyzer. Returns per track: status, AccurateRip and CTDB confidence."""
    lengths = [cd["samples"] for cd in cds]
    if any(n % SECTOR for n in lengths) or min(lengths) < 2 * AR_SKIP:
        return [{"status": "unsupported", "ar": None, "ctdb": None}] * len(cds)
    toc = toc_from_lengths(lengths)
    ar, ct = _accuraterip(toc, cds), _ctdb(toc, cds)
    if ar is None and ct is None and release:
        # MusicBrainz only as a fallback: it is slow (one request per second)
        # and only helps discs whose first track has a pregap the rip left out.
        for other in musicbrainz_tocs(release, disc_no, lengths):
            if other != toc:
                ar, ct = _accuraterip(other, cds), _ctdb(other, cds)
                if ar is not None or ct is not None:
                    break
    out = []
    for i in range(len(cds)):
        a = ar[i] if ar else None
        c = ct[i] if ct else None
        status = "verified" if (a or c) else ("mismatch" if ar is not None or ct is not None else "not-found")
        out.append({"status": status, "ar": a, "ctdb": c})
    return out


def verify_albums(album_dirs: set[str] | None, should_stop: Callable[[], bool],
                  progress: Callable[[str, int, int], None]) -> dict:
    """Checks every disc of the given albums and stores the result per track.
    Returns counts by outcome, and the first lookup error if any."""
    with db.connect() as conn:
        if album_dirs is None:
            album_dirs = {r[0] for r in conn.execute("SELECT DISTINCT album_dir FROM tracks WHERE missing = 0")}
    counts = {s: 0 for s in STATUSES} | {"failed": 0}
    first_error = None
    for i, album_dir in enumerate(sorted(album_dirs)):
        if should_stop():
            break
        progress(album_dir, i, len(album_dirs))
        with db.connect() as conn:
            rows = [db.row_to_dict(r) for r in conn.execute(
                "SELECT id, path, disc, track, sample_rate, bits, channels, metrics FROM tracks "
                "WHERE album_dir = ? AND missing = 0", (album_dir,))]
        discs: dict[int, list[dict]] = {}
        for r in rows:
            discs.setdefault(r["disc"] or 1, []).append(r)
        updates = []
        for disc_no, tracks in discs.items():
            tracks.sort(key=lambda r: (r["track"] or 0, r["path"]))
            cds = [(r["metrics"] or {}).get("cd") for r in tracks]
            if not all(cds) or not all(is_cd_format(r["sample_rate"], r["bits"], r["channels"]) for r in tracks):
                results = [{"status": "unsupported", "ar": None, "ctdb": None}] * len(tracks)
            else:
                try:
                    results = verify_disc(cds, cds[0].get("mb_release"), disc_no)
                except LookupFailed as exc:
                    counts["failed"] += 1
                    first_error = first_error or str(exc)
                    continue
            for r, res in zip(tracks, results):
                counts[res["status"]] += 1
                updates.append((res["status"], res["ar"], res["ctdb"], r["id"]))
        with db.connect() as conn:
            conn.executemany("UPDATE tracks SET cd_status = ?, cd_ar = ?, cd_ctdb = ? WHERE id = ?", updates)
    return {"counts": counts, "error": first_error}
