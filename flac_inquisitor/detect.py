"""Turn measurements into evidence and a verdict.

Every check reports what it measured, the threshold it compared against and a
one-line explanation, so the user can judge a verdict instead of trusting it.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from flac_inquisitor.analyzer import REF_BAND, Measurements, measure
from flac_inquisitor.flacmeta import FlacInfo, read_flac_info

# Bump whenever a threshold or a check changes: stored results with an older
# version are re-analyzed on the next scan.
ALGO_VERSION = 2

# Thresholds, calibrated on 07/11/2026 against 58 tracks of a real library and
# fakes made from them with LAME, AAC, Opus, Vorbis and ffmpeg's resampler
# (scripts/calibrate.py). Numbers in the comments are from that run.
CUTOFF_MIN_DROP_DB = 20.0  # level drop across the edge to call it a cutoff
CUTOFF_FAKE_HZ = 19_000  # LAME 128-192 and AAC 128 cut at 16-17.5 kHz
CUTOFF_SUSPECT_HZ = 20_500  # MP3 320 and Opus cut at 20.0-20.5 kHz; 49 of 58 originals have no hard cutoff
# Upsampled files show the resampler's roll-off as a step between 20 and 26
# kHz: 48 of 50 upsampled fakes stepped >= 14.5 dB, real hi-res <= 11 dB.
HIRES_STEP_SUSPECT_DB = 12.0
HIRES_STEP_FAKE_DB = 20.0
HIRES_FLAT_DB = 5.0  # above 27 kHz an upsampled file is a flat noise floor
# Isolated band holes: one 500 Hz band vanishes while both neighbours keep
# playing, which music does not do but MP3 and Vorbis encoders do. Share of
# loud frames with at least one such hole: originals p90 0.08%, LAME V0
# median 1%.
HOLE_DROP_DB = 25.0
HOLE_NEIGHBOUR_DB = 10.0
HOLES_SUSPECT = 0.005
HOLES_FAKE = 0.04
LOSSYWAV_FAKE_RATIO = 0.25
LOSSYWAV_SUSPECT_RATIO = 0.05
MIN_LOUD_BLOCKS = 10
CD_SECTOR = 588  # samples per CD sector at 44.1 kHz (44100 / 75)


@dataclass
class Evidence:
    check: str
    result: str  # pass | warn | fail | info
    message: str
    category: str | None = None
    value: float | str | None = None
    threshold: str | None = None


@dataclass
class Result:
    verdict: str  # ok | suspect | fake
    categories: list[str]
    summary: str
    evidence: list[Evidence]
    audio_md5: str
    metrics: dict = field(default_factory=dict)
    algo_version: int = ALGO_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def _db(power: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.asarray(power) + 1e-30)


def _band_mean_db(freqs, power, lo, hi) -> float | None:
    sel = (freqs >= lo) & (freqs < hi)
    return float(_db(power[sel].mean())) if sel.any() else None


def find_cutoff(freqs: np.ndarray, power: np.ndarray) -> dict:
    """Find the highest frequency where the spectrum falls off a cliff.

    A cliff is a 500 Hz stretch whose level is CUTOFF_MIN_DROP_DB above
    everything from 250 Hz further up to Nyquist. Requiring the whole range
    above to stay low separates an encoder lowpass from a dip, and a gentle
    analog roll-off never produces such a step.
    """
    df = freqs[1] - freqs[0]
    width = max(1, round(200 / df))
    smooth = np.convolve(power, np.ones(width) / width, mode="same")
    ref = _band_mean_db(freqs, power, *REF_BAND)
    rel = _db(smooth) - ref

    below_w = max(1, round(500 / df))
    gap = max(1, round(250 / df))
    csum = np.concatenate([[0.0], np.cumsum(rel)])
    suffix_max = np.maximum.accumulate(rel[::-1])[::-1]

    n = len(rel)
    start = max(below_w, int(np.searchsorted(freqs, 4_000)))
    idx = np.arange(start, n - gap)
    below = (csum[idx + 1] - csum[idx + 1 - below_w]) / below_w
    drop = below - suffix_max[idx + gap]

    hits = idx[drop >= CUTOFF_MIN_DROP_DB]
    if len(hits) == 0:
        return {"cutoff_hz": None, "drop_db": float(drop.max()) if len(drop) else 0.0}
    # Topmost run of consecutive hits, then the strongest edge inside it.
    run_start = len(hits) - 1
    while run_start > 0 and hits[run_start - 1] == hits[run_start] - 1:
        run_start -= 1
    run = hits[run_start:]
    best = run[np.argmax(drop[run - start])]
    return {
        "cutoff_hz": float(freqs[best]),
        "drop_db": float(drop[best - start]),
        "floor_db": float(np.median(rel[best + gap :])),
    }


def hires_step(m: Measurements) -> tuple[float, float]:
    """Largest drop between a 2 kHz stretch and the 2 kHz above it, for edges
    between 20 and 26 kHz, plus how flat the spectrum is from 27 kHz up."""
    level = lambda lo, hi: _band_mean_db(m.freqs, m.mid_power, lo, hi)  # noqa: E731
    step = max(level(c - 2_000, c) - level(c + 500, c + 2_500) for c in range(20_000, 26_001, 500))
    top = min(m.sample_rate / 2 - 1_000, 40_000)
    highs = [level(f, f + 1_000) for f in range(27_000, int(top), 1_000)]
    flat = max(highs) - min(highs) if highs else float("inf")
    return step, flat


def hole_ratio(m: Measurements, cutoff_hz: float | None) -> tuple[float | None, int]:
    """Share of loud frames where a high band vanishes while the bands on both
    sides keep playing: the encoder giving up on that band for a moment."""
    if m.band_db.size == 0:
        return None, 0
    top = (cutoff_hz or m.sample_rate / 2) - 500
    bands = m.band_edges + 500 <= top
    loud = m.frame_ref_db >= np.median(m.frame_ref_db) - 10
    cells = m.band_db[loud][:, bands]
    if len(cells) < 20:
        return None, 0
    typical = np.median(cells, axis=0)
    # Bands with nothing in them (silence, very dark recordings) say nothing.
    active = typical > -80
    if active.sum() < 3:
        return None, 0
    cells, typical = cells[:, active], typical[active]
    low = cells < typical - HOLE_DROP_DB
    playing = cells >= typical - HOLE_NEIGHBOUR_DB
    isolated = low[:, 1:-1] & playing[:, :-2] & playing[:, 2:]
    return float(isolated.any(axis=1).mean()), int(active.sum())


def evaluate(info: FlacInfo, m: Measurements) -> Result:
    ev: list[Evidence] = []
    metrics: dict = {}

    # Integrity.
    if m.decode_errors:
        ev.append(Evidence("decode", "fail", f"The decoder reported errors: {m.decode_errors[:300]}", "corrupt"))
    if info.md5_hex and info.md5_hex != m.audio_md5:
        ev.append(Evidence("md5", "fail", "Decoded audio does not match the MD5 stored in the file.", "corrupt",
                           m.audio_md5, info.md5_hex))
    if info.total_samples and info.total_samples != m.decoded_samples:
        ev.append(Evidence("length", "fail",
                           f"Decoded {m.decoded_samples} samples, the header promises {info.total_samples}.",
                           "corrupt", m.decoded_samples, str(info.total_samples)))
    if not any(e.category == "corrupt" for e in ev):
        ev.append(Evidence("integrity", "pass", "Audio decodes cleanly and matches its MD5."
                           if info.md5_hex else "Audio decodes cleanly (the file has no MD5 to compare)."))

    # Cutoff.
    cut = find_cutoff(m.freqs, m.mid_power)
    cutoff = cut["cutoff_hz"]
    metrics["cutoff_hz"] = cutoff
    metrics["cutoff_drop_db"] = cut["drop_db"]
    nyquist = m.sample_rate / 2
    if m.sample_rate > 48_000:
        step, flat = hires_step(m)
        metrics["hires_step_db"] = step
        metrics["hires_flat_db"] = flat
        rate = f"{m.sample_rate / 1000:g} kHz"
        if step >= HIRES_STEP_FAKE_DB or (step >= HIRES_STEP_SUSPECT_DB and flat <= HIRES_FLAT_DB):
            ev.append(Evidence("hires-content", "fail",
                               f"The spectrum falls {step:.0f} dB between 20 and 26 kHz and is flat above: this "
                               f"{rate} file was upsampled from 44.1 or 48 kHz audio.",
                               "upsampled", step, f">= {HIRES_STEP_FAKE_DB:g} dB"))
        elif step >= HIRES_STEP_SUSPECT_DB:
            ev.append(Evidence("hires-content", "warn",
                               f"The spectrum falls {step:.0f} dB between 20 and 26 kHz: this {rate} file may "
                               "have been upsampled.", "upsampled", step, f">= {HIRES_STEP_SUSPECT_DB:g} dB"))
        else:
            ev.append(Evidence("hires-content", "pass", f"Content continues smoothly above 22 kHz "
                               f"(largest step {step:.0f} dB).", value=step))
    if cutoff is not None and cutoff < CUTOFF_FAKE_HZ:
        ev.append(Evidence("cutoff", "fail",
                           f"Hard cutoff at {cutoff / 1000:.1f} kHz (level drops {cut['drop_db']:.0f} dB): typical "
                           "of a lossy encoder. Audio resampled from 32 kHz looks the same.",
                           "lossy-transcode", cutoff, f"< {CUTOFF_FAKE_HZ} Hz"))
    elif cutoff is not None and cutoff < CUTOFF_SUSPECT_HZ:
        ev.append(Evidence("cutoff", "warn",
                           f"Hard cutoff at {cutoff / 1000:.1f} kHz: high-bitrate MP3 cuts here, some "
                           "real recordings too.", "lossy-transcode", cutoff, f"< {CUTOFF_SUSPECT_HZ} Hz"))
    else:
        where = "no hard cutoff" if cutoff is None else f"cutoff at {cutoff / 1000:.1f} kHz"
        ev.append(Evidence("cutoff", "pass", f"Full bandwidth ({where}, Nyquist {nyquist / 1000:g} kHz).", value=cutoff))

    # Band dropouts.
    holes, n_bands = hole_ratio(m, cutoff)
    metrics["hole_ratio"] = holes
    if holes is None:
        ev.append(Evidence("hf-holes", "info", "Not enough high-frequency content to look for dropouts."))
    elif holes >= HOLES_FAKE:
        ev.append(Evidence("hf-holes", "fail", f"In {holes:.1%} of loud moments a high band vanishes while its "
                           "neighbours play on: an encoder dropping bands.", "lossy-transcode", holes,
                           f">= {HOLES_FAKE:.1%}"))
    elif holes >= HOLES_SUSPECT:
        ev.append(Evidence("hf-holes", "warn", f"In {holes:.1%} of loud moments a high band vanishes while its "
                           "neighbours play on.", "lossy-transcode", holes, f">= {HOLES_SUSPECT:.1%}"))
    else:
        ev.append(Evidence("hf-holes", "pass", f"High bands are continuous ({holes:.2%} of loud moments with "
                           f"isolated dropouts, {n_bands} bands checked).", value=holes))

    # Bit depth.
    tz = m.block_trailing_zeros
    if len(tz) >= MIN_LOUD_BLOCKS:
        used = m.bits_per_sample - int(tz.min())
        metrics["effective_bits"] = used
        uneven = float((tz > 0).mean())
        metrics["blocks_with_unused_bits"] = uneven
        if m.bits_per_sample > 16 and used <= 16:
            ev.append(Evidence("bit-depth", "fail", f"A {m.bits_per_sample}-bit file that only uses {used} bits.",
                               "padded-bits", used, "<= 16"))
        elif uneven >= 0.9 and tz.min() == tz.max():
            ev.append(Evidence("bit-depth", "fail", f"Every loud block leaves the lowest {int(tz.min())} bits "
                               f"unused: really {used}-bit audio.", "padded-bits", used))
        elif uneven >= LOSSYWAV_FAKE_RATIO:
            ev.append(Evidence("bit-depth", "fail", f"{uneven:.0%} of loud blocks drop a varying number of low bits: "
                               "processed with lossyWAV.", "lossywav", uneven, f">= {LOSSYWAV_FAKE_RATIO:.0%}"))
        elif uneven >= LOSSYWAV_SUSPECT_RATIO:
            ev.append(Evidence("bit-depth", "warn", f"{uneven:.0%} of loud blocks leave low bits unused.",
                               "lossywav", uneven, f">= {LOSSYWAV_SUSPECT_RATIO:.0%}"))
        else:
            ev.append(Evidence("bit-depth", "pass", f"All {m.bits_per_sample} bits are in use.", value=used))

    # Weak hints: shown to the user, never decisive on their own.
    if info.has_id3v2 or info.has_id3v1:
        ev.append(Evidence("id3", "info", "The file carries ID3 tags, which FLAC encoders do not write: it went "
                           "through a tool made for MP3 files."))
    tag_text = " ".join([info.vendor or ""] + [v for vals in info.tags.values() for v in vals]).lower()
    if "lame" in tag_text or ".mp3" in tag_text:
        ev.append(Evidence("tags", "info", "Tags or vendor string mention LAME or an .mp3 file."))
    if info.vendor:
        ev.append(Evidence("vendor", "info", f"Encoded by: {info.vendor}", value=info.vendor))
    if m.sample_rate == 44_100 and m.decoded_samples % CD_SECTOR == 0:
        ev.append(Evidence("cd-length", "info", "Length is a whole number of CD sectors, as in a CD rip."))
    if 1_000 <= m.leading_zero_samples <= 1_300:
        ev.append(Evidence("leading-silence", "info", f"{m.leading_zero_samples} samples of digital silence at the "
                           "start, close to an MP3 decoder delay.", value=m.leading_zero_samples))
    if info.duration:
        kbps = (info.file_size - info.audio_offset) * 8 / info.duration / 1000
        metrics["bitrate_kbps"] = kbps
        ev.append(Evidence("bitrate", "info", f"FLAC bitrate {kbps:.0f} kbps.", value=kbps))

    return _verdict(ev, m.audio_md5, metrics)


def _verdict(ev: list[Evidence], audio_md5: str, metrics: dict) -> Result:
    fails = [e for e in ev if e.result == "fail"]
    warns = [e for e in ev if e.result == "warn"]
    warn_cats = [e.category for e in warns]
    # Two independent warnings pointing to the same cause are as good as a fail.
    doubled = {c for c in warn_cats if warn_cats.count(c) >= 2}
    if fails or doubled:
        verdict = "fake"
    elif warns:
        verdict = "suspect"
    else:
        verdict = "ok"
    categories = sorted({e.category for e in fails + warns if e.category})
    lead = (fails or warns or [None])[0]
    summary = lead.message if lead else "Looks like genuine lossless audio."
    return Result(verdict, categories, summary, ev, audio_md5, metrics)


def analyze_file(path: str | Path) -> Result:
    info = read_flac_info(path)
    return evaluate(info, measure(path, info))


# Album coherence. A track that differs from the rest of its album points to a
# release assembled from several sources, even when each track alone passes.
OUTLIER_CUTOFF_HZ = 2_000


def album_coherence(tracks: list[dict]) -> dict[int, Evidence]:
    """tracks: dicts with "format" ("44100/16") and "cutoff_hz" (None = no
    hard cutoff). Returns evidence for the tracks that stand out, by index."""
    if len(tracks) < 3:
        return {}
    out: dict[int, Evidence] = {}
    formats = [t["format"] for t in tracks]
    common = max(set(formats), key=formats.count)
    # Only a clear majority defines what is normal: a 50/50 split has no outlier.
    if formats.count(common) > len(formats) / 2:
        for i, fmt in enumerate(formats):
            if fmt != common:
                out[i] = Evidence("album-format", "warn", f"This track is {fmt}, the rest of the album is {common}.",
                                  "mixed-sources", fmt, common)

    def effective(t: dict) -> float:
        rate = int(t["format"].split("/")[0])
        return t["cutoff_hz"] if t["cutoff_hz"] is not None else rate / 2

    cutoffs = [effective(t) for t in tracks]
    median = float(np.median(cutoffs))
    for i, c in enumerate(cutoffs):
        if median - c >= OUTLIER_CUTOFF_HZ and i not in out:
            out[i] = Evidence("album-cutoff", "warn", f"This track stops at {c / 1000:.1f} kHz, the album "
                              f"typically at {median / 1000:.1f} kHz.", "mixed-sources", c, f"{median:.0f} Hz")
    return out
