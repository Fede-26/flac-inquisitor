import shutil
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pytest

from flac_inquisitor import cdverify, db
from flac_inquisitor.analyzer import decode_chunks, measure
from flac_inquisitor.flacmeta import read_flac_info

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import accuraterip  # noqa: E402  the calibration script, checked against real discs


@pytest.fixture
def disc(samples, tmp_path, monkeypatch):
    """Three CD-format tracks of 600 sectors each, as one disc."""
    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    db.init()
    paths = [samples[k] for k in ("real", "tape", "mp3_128")]
    cds = [measure(p, read_flac_info(p)).cd for p in paths]
    return paths, cds


def pcm(path: Path) -> bytes:
    with tempfile.TemporaryFile() as err:
        return b"".join((c >> 16).astype("<i2").tobytes() for c in decode_chunks(path, 2, err))


def expected_ctdb(paths) -> tuple[int, list[int]]:
    """CTDB checksums straight from the definition: CRC-32 of the disc PCM
    without its first 5880 samples and its last 5880 + S % 5880."""
    data = [pcm(p) for p in paths]
    total = sum(len(d) for d in data) // 4
    head, tail = 5880, 5880 + total % 5880
    tracks, pos = [], 0
    for d in data:
        n = len(d) // 4
        lo, hi = max(pos, head) - pos, min(pos + n, total - tail) - pos
        tracks.append(zlib.crc32(d[4 * lo : 4 * hi]))
        pos += n
    whole = b"".join(data)
    return zlib.crc32(whole[4 * head : 4 * (total - tail)]), tracks


def dbar(crcs: list[int], confidence: int = 7) -> bytes:
    """One AccurateRip pressing record."""
    out = bytes([len(crcs)]) + bytes(12)
    for crc in crcs:
        out += bytes([confidence]) + crc.to_bytes(4, "little") + bytes(4)
    return out


def ctdb_xml(toc: str, disc_crc: int, track_crcs: list[int] | None = None, confidence: int = 5) -> bytes:
    tracks = f' trackcrcs="{" ".join(f"{c:08x}" for c in track_crcs)}"' if track_crcs else ""
    return (f'<ctdb><entry confidence="{confidence}" crc32="{disc_crc:08x}" id="1" npar="8" stride="5880" '
            f'toc="{toc}"{tracks} /></ctdb>').encode()


def fake_fetch(monkeypatch, ar: bytes | None, ctdb: bytes | None):
    calls = []

    def fetch(url):
        calls.append(url)
        return ar if "accuraterip" in url else ctdb if "cue.tools" in url else None

    monkeypatch.setattr(cdverify, "fetch", fetch)
    return calls


def test_accuraterip_checksums_match_the_calibration_script(disc):
    paths, cds = disc
    expected = accuraterip.track_crcs(paths, sum(cd["samples"] for cd in cds))
    got = [cdverify.ar_track_crc(cd, first=i == 0, last=i == 2) for i, cd in enumerate(cds)]
    assert got == expected


def test_verified_on_both_databases(disc, monkeypatch):
    paths, cds = disc
    ar = [v1 for v1, _ in accuraterip.track_crcs(paths, sum(cd["samples"] for cd in cds))]
    disc_crc, _ = expected_ctdb(paths)
    toc = cdverify.ctdb_toc(cdverify.toc_from_lengths([cd["samples"] for cd in cds]))
    assert toc == "0:600:1200:1800"
    fake_fetch(monkeypatch, dbar(ar), ctdb_xml(toc, disc_crc))
    assert cdverify.verify_disc(cds) == [{"status": "verified", "ar": 7, "ctdb": 5}] * 3


def test_ctdb_track_checksums_verify_single_tracks(disc, monkeypatch):
    paths, cds = disc
    _, tracks = expected_ctdb(paths)
    toc = "0:600:1200:1800"
    # Another rip of the disc: tracks 1 and 3 agree, the disc checksum does not.
    fake_fetch(monkeypatch, None, ctdb_xml(toc, 0x12345678, [tracks[0], 0xDEADBEEF, tracks[2]]))
    assert [r["status"] for r in cdverify.verify_disc(cds)] == ["verified", "mismatch", "verified"]


def test_known_disc_without_match_is_a_mismatch(disc, monkeypatch):
    _, cds = disc
    fake_fetch(monkeypatch, dbar([1, 2, 3]), None)
    assert [r["status"] for r in cdverify.verify_disc(cds)] == ["mismatch"] * 3


def test_unknown_disc(disc, monkeypatch):
    _, cds = disc
    calls = fake_fetch(monkeypatch, None, None)
    assert [r["status"] for r in cdverify.verify_disc(cds)] == ["not-found"] * 3
    assert len(calls) == 2  # no MusicBrainz fallback without a release ID


def test_tracks_not_on_sector_boundaries_are_unsupported(disc):
    _, cds = disc
    odd = [{**cds[0], "samples": cds[0]["samples"] - 1}]
    assert cdverify.verify_disc(odd)[0]["status"] == "unsupported"


def test_lookups_are_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    db.init()
    hits = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"body"

    monkeypatch.setattr(cdverify.urllib.request, "urlopen", lambda *a, **k: hits.append(1) or Response())
    assert cdverify.fetch("https://example.org/x") == b"body"
    assert cdverify.fetch("https://example.org/x") == b"body"
    assert len(hits) == 1


def test_scan_stores_the_cd_status(samples, tmp_path, monkeypatch):
    from tests.test_scanner import scan, tracks

    album = tmp_path / "music" / "Artist" / "Album"
    album.mkdir(parents=True)
    paths = []
    for i, key in enumerate(("real", "tape", "mp3_128"), start=1):
        paths.append(album / f"{i:02d}.flac")
        shutil.copy(samples[key], paths[-1])
    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    monkeypatch.setenv("LIBRARY_PATH", str(tmp_path / "music"))
    monkeypatch.setenv("CD_LOOKUP", "true")
    db.init()
    total = 3 * 8 * 44_100
    fake_fetch(monkeypatch, dbar([v1 for v1, _ in accuraterip.track_crcs(paths, total)]), None)
    scan()
    assert {t["cd_status"] for t in tracks().values()} == {"verified"}
    # The verdict is unchanged: a real CD can be mastered from a lossy source.
    assert tracks()["Artist/Album/03.flac"]["final_verdict"] == "fake"


def test_more_samples_than_the_header_promises():
    # A damaged file decoded to more samples than its STREAMINFO says: the
    # analysis must go on, without CD checksums for that track.
    cd = cdverify.CDChecksums(30_000)
    cd.update(np.ones((20_000, 2), dtype=np.int32))
    cd.update(np.ones((20_000, 2), dtype=np.int32))
    assert cd.result() is None


def _as_samples(words: np.ndarray) -> np.ndarray:
    return np.stack([(words & 0xFFFF).astype(np.uint16).view(np.int16),
                     (words >> 16).astype(np.uint16).view(np.int16)], axis=1)


@pytest.mark.parametrize("shift", [-37, 0, 25])
def test_sliding_checksums_equal_a_rip_shifted_by_hand(shift):
    rng = np.random.default_rng(3)
    words = [rng.integers(0, 2**32, 588 * n, dtype=np.uint64).astype(np.uint32) for n in (40, 33, 41)]
    disc = np.concatenate(words)
    total, r = len(disc), 50
    tail_sectors = 10 + (total % cdverify.CTDB_STRIDE) // cdverify.SECTOR
    # The corrected rip: what the rip holds `shift` frames later, silence outside.
    padded = np.concatenate([np.zeros(r, np.uint32), disc, np.zeros(r, np.uint32)])
    corrected = padded[r + shift : r + shift + total]
    start = 0
    for i, w in enumerate(words):
        first, last = i == 0, i == len(words) - 1
        cd = cdverify.CDChecksums(len(w))
        cd.update(_as_samples(corrected[start : start + len(w)]))
        expected = cd.result()
        ext = padded[start : start + len(w) + 2 * r]
        got = cdverify.track_shifts(ext, r, first, last, tail_sectors)
        v1, v2 = cdverify.ar_track_crc(expected, first, last)
        assert got["ar_v1"][shift + r] == v1
        assert cdverify.ar_at(ext, r, shift, first, last) == (v1, v2)
        assert got["ctdb"][shift + r] == cdverify.ctdb_track_crc(expected, first, tail_sectors if last else None)
        start += len(w)


def test_offset_search_finds_an_uncorrected_rip(disc, tmp_path, monkeypatch):
    from tests.conftest import write_wav

    paths, cds = disc
    original = np.concatenate([cdverify._decode_words(p) for p in paths])
    # The same disc read by a drive 12 samples late, not corrected.
    shifted = np.concatenate([np.zeros(12, np.uint32), original[:-12]])
    rip, start = [], 0
    for i, cd in enumerate(cds):
        wav = tmp_path / f"{i}.wav"
        write_wav(wav, _as_samples(shifted[start : start + cd["samples"]]) / 32767)
        rip.append(wav)
        start += cd["samples"]
    v = [cdverify.ar_track_crc(cd, first=i == 0, last=i == 2) for i, cd in enumerate(cds)]
    # v1 finds the shift; the last track is only known by its v2 checksum,
    # which the second pass computes at that shift.
    ar = cdverify.parse_dbar(dbar([v[0][0], v[1][0], v[2][1]]), 3)
    found = cdverify.best_offset(rip, start, ar, [], lambda: False, lambda *a: None, r=20)
    assert found["shift"] == 12 and all(found["ar"])
