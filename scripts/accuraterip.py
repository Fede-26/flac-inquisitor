"""Check CD-ripped albums against AccurateRip, to get ground truth for calibration.

A track whose checksum matches AccurateRip is a bit-exact copy of a pressed CD:
whatever the detector says about it, it is not a transcode (the CD itself may
still have been mastered from a lossy source). A track that does not match,
on a disc AccurateRip knows, was not ripped from that CD.

The disc layout (TOC) comes from MusicBrainz, through the MUSICBRAINZ_ALBUMID
tag, and must agree with the track lengths of the files.

    uv run python scripts/accuraterip.py ALBUM_DIR [ALBUM_DIR...]
    uv run python scripts/accuraterip.py --root LIBRARY --list albums.txt --detect --jobs 3
    uv run python scripts/accuraterip.py --root LIBRARY --discover --detect --jobs 3

With --detect every track is also analyzed by the detector, and the output is
a TSV table: on tracks AccurateRip confirms, any verdict other than "ok" is a
false positive.

Checksum and disc ID formulas follow the cdtoc Rust crate and the
flaccuraterip Haskell package. Only offset-corrected rips can match; data
tracks (enhanced CDs) are not supported.
"""

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flac_inquisitor.analyzer import decode_chunks  # noqa: E402
from flac_inquisitor.flacmeta import read_flac_info  # noqa: E402

SECTOR = 588  # samples per CD sector
SKIP = 5 * SECTOR  # AccurateRip ignores 5 sectors at each end of the disc
USER_AGENT = "flac-inquisitor/0.1 ( https://github.com/Fede-26/flac-inquisitor )"
CACHE = Path(tempfile.gettempdir()) / "flac-inquisitor-ar-cache"

_last_mb_call = 0.0


def fetch(url: str) -> bytes | None:
    """GET with a disk cache; None on 404. MusicBrainz allows 1 request/s."""
    global _last_mb_call
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / url.replace("/", "_").replace(":", "_").replace("?", "_")[-200:]
    if cached.exists():
        data = cached.read_bytes()
        return data or None
    for attempt in range(6):
        if "musicbrainz.org" in url:
            time.sleep(max(0.0, 1.1 - (time.time() - _last_mb_call)))
            _last_mb_call = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                        timeout=30) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                data = b""
                break
            # 503 is MusicBrainz throttling: back off and retry.
            if exc.code != 503 or attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))
    cached.write_bytes(data)
    return data or None


def cddb_id(offsets: list[int], leadout: int) -> int:
    def digit_sum(n: int) -> int:
        return sum(int(d) for d in str(n))

    checksum = sum(digit_sum(o // 75) for o in offsets)
    total = leadout // 75 - offsets[0] // 75
    return ((checksum % 255) << 24) | (total << 8) | len(offsets)


def ar_url(offsets: list[int], leadout: int) -> str:
    """offsets: track starts in sectors including the 150-sector lead-in."""
    rel = [o - 150 for o in offsets] + [leadout - 150]
    id1 = sum(rel) & 0xFFFFFFFF
    id2 = sum(max(o, 1) * i for i, o in enumerate(rel, start=1)) & 0xFFFFFFFF
    id3 = cddb_id(offsets, leadout)
    hex1 = f"{id1:08x}"
    return (f"http://www.accuraterip.com/accuraterip/{hex1[-1]}/{hex1[-2]}/{hex1[-3]}/"
            f"dBAR-{len(offsets):03d}-{hex1}-{id2:08x}-{id3:08x}.bin")


def parse_dbar(data: bytes, tracks: int) -> list[dict[int, int]]:
    """Per track: {crc: total confidence} over all pressings in the file."""
    size = 13 + 9 * tracks
    out: list[dict[int, int]] = [{} for _ in range(tracks)]
    for start in range(0, len(data) - size + 1, size):
        chunk = data[start + 13 : start + size]
        for t in range(tracks):
            conf = chunk[9 * t]
            crc = int.from_bytes(chunk[9 * t + 1 : 9 * t + 5], "little")
            if crc:
                out[t][crc] = out[t].get(crc, 0) + conf
    return out


def mb_tocs(release_id: str, disc: int) -> list[tuple[list[int], int]]:
    data = fetch(f"https://musicbrainz.org/ws/2/release/{release_id}?inc=discids&fmt=json")
    if not data:
        return []
    for medium in json.loads(data).get("media", []):
        if medium.get("position") == disc:
            return [(d["offsets"], d["sectors"]) for d in medium.get("discs", [])]
    return []


def matching_toc(lengths: list[int], tocs: list[tuple[list[int], int]]) -> tuple[list[int], int] | None:
    """The TOC whose track lengths equal the files' lengths, in sectors. The
    first track may be shorter by its pregap (hidden or silent lead-in)."""
    if any(n % SECTOR for n in lengths):
        return None
    sectors = [n // SECTOR for n in lengths]
    for offsets, leadout in tocs:
        if len(offsets) != len(sectors):
            continue
        toc_lengths = [b - a for a, b in zip(offsets, offsets[1:] + [leadout])]
        if toc_lengths[1:] == sectors[1:] and toc_lengths[0] in (sectors[0], sectors[0] + offsets[0] - 150):
            return offsets, leadout
    return None


def track_crcs(paths: list[Path], total: int) -> list[tuple[int, int]]:
    """AccurateRip v1 and v2 checksums of each track of a whole disc."""
    out = []
    position = 0  # sample index within the disc
    for path in paths:
        info = read_flac_info(path)
        v1 = v2 = 0
        n = 0  # sample index within the track
        with tempfile.TemporaryFile() as err:
            for chunk in decode_chunks(path, 2, err):
                # Back to 16-bit, then one little-endian word per stereo sample.
                s = (chunk >> 16).astype(np.int16)
                words = s.view(np.uint16).astype(np.uint64)
                x = words[:, 0] | (words[:, 1] << np.uint64(16))
                idx = np.arange(len(x), dtype=np.uint64)
                disc_idx = idx + np.uint64(position + n)
                keep = (disc_idx >= SKIP - 1) & (disc_idx < total - SKIP)
                x = np.where(keep, x, np.uint64(0))
                product = (idx + np.uint64(n + 1)) * x
                v1 = (v1 + int(product.sum() & np.uint64(0xFFFFFFFF))) & 0xFFFFFFFF
                v2 = (v2 + int(((product & np.uint64(0xFFFFFFFF)) + (product >> np.uint64(32))).sum()
                               & np.uint64(0xFFFFFFFF))) & 0xFFFFFFFF
                n += len(x)
        if n != info.total_samples:
            raise ValueError(f"{path}: decoded {n} samples, header says {info.total_samples}")
        out.append((v1, v2))
        position += n
    return out


def disc_groups(album: Path) -> dict[tuple[str, int], list[tuple[int, Path]]]:
    groups: dict[tuple[str, int], list[tuple[int, Path]]] = {}
    for path in sorted(album.rglob("*.flac")):
        tags = read_flac_info(path).tags
        release = (tags.get("MUSICBRAINZ_ALBUMID") or [""])[0]
        disc = int((tags.get("DISCNUMBER") or ["1"])[0].split("/")[0] or 1)
        track = int((tags.get("TRACKNUMBER") or ["0"])[0].split("/")[0] or 0)
        groups.setdefault((release, disc), []).append((track, path))
    return groups


def check_album(album: Path, detect: bool = False) -> list[list[str]]:
    """Rows: album, disc, file, AccurateRip status, confidence, then verdict,
    categories and summary when detect is on."""
    rows = []
    for (release, disc), items in disc_groups(album).items():
        items.sort()
        paths = [p for _, p in items]
        infos = [read_flac_info(p) for p in paths]

        def disc_status(status: str) -> None:
            rows.append([str(album), str(disc), "", status, ""])

        if any((i.sample_rate, i.bits_per_sample, i.channels) != (44_100, 16, 2) for i in infos):
            disc_status("not-cd-format")
            continue
        if not release:
            disc_status("no-musicbrainz-id")
            continue
        toc = matching_toc([i.total_samples for i in infos], mb_tocs(release, disc))
        if toc is None:
            disc_status("no-matching-toc")
            continue
        data = fetch(ar_url(*toc))
        if not data:
            disc_status("not-in-accuraterip")
            continue
        known = parse_dbar(data, len(paths))
        crcs = track_crcs(paths, sum(i.total_samples for i in infos))
        for path, (v1, v2), entries in zip(paths, crcs, known):
            conf = entries.get(v1) or entries.get(v2)
            status = "accurate" if conf else ("not-accurate" if entries else "no-data")
            row = [str(album), str(disc), path.name, status, str(conf or "")]
            if detect:
                from flac_inquisitor.detect import analyze_file

                r = analyze_file(path)
                row += [r.verdict, ",".join(r.categories), r.summary.replace("\t", " ").replace("\n", " ")]
            rows.append(row)
    return rows


def _job(args: tuple[Path, bool]) -> list[list[str]]:
    album, detect = args
    try:
        return check_album(album, detect)
    except Exception as exc:  # one broken album must not stop the run
        return [[str(album), "", "", f"error: {exc}", ""]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("albums", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."), help="base for relative album paths")
    parser.add_argument("--list", type=Path, help="file with one album folder per line")
    parser.add_argument("--discover", action="store_true", help="check every album folder under --root")
    parser.add_argument("--detect", action="store_true", help="also run the detector on every track")
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    albums = list(args.albums)
    if args.list:
        albums += [Path(line) for line in args.list.read_text().splitlines() if line.strip()]
    if args.discover:
        from flac_inquisitor.scanner import album_dir_of, list_flacs

        albums += sorted({Path(album_dir_of(rel)) for rel in list_flacs(args.root, [])})
    albums = [a if a.is_absolute() else args.root / a for a in albums]

    header = ["album", "disc", "file", "accuraterip", "confidence"]
    print("\t".join(header + (["verdict", "categories", "summary"] if args.detect else [])), flush=True)
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max(1, args.jobs)) as pool:
        for rows in pool.map(_job, [(a, args.detect) for a in albums]):
            for row in rows:
                print("\t".join(row), flush=True)


if __name__ == "__main__":
    main()
