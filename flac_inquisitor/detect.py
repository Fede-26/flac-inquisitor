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
ALGO_VERSION = 1

# Thresholds. Provisional until the calibration step; see docs/reference.
CUTOFF_MIN_DROP_DB = 20.0  # level drop across the edge to call it a cutoff
CUTOFF_FAKE_HZ = 19_000  # below this, a hard cutoff means a lossy source
CUTOFF_SUSPECT_HZ = 20_500  # MP3 320 / V0 territory; real ADC filters sit higher
# Upsampled files: resamplers roll off gradually between 20 and 26 kHz, then
# leave only the noise floor. Compare 16-20 kHz with the loudest 1 kHz band
# from 26 kHz up.
HIRES_GAP_DB = 60.0
HOLE_DROP_DB = 25.0  # band this far under its usual level counts as a hole
HOLES_SUSPECT = 0.02
HOLES_FAKE = 0.08
SIDE_DROP_SUSPECT_DB = 15.0
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


def hires_gap(m: Measurements) -> float:
    low = _band_mean_db(m.freqs, m.mid_power, 16_000, 20_000)
    top = min(m.sample_rate / 2, 48_000)
    highs = [_band_mean_db(m.freqs, m.mid_power, f, f + 1_000) for f in range(26_000, int(top) - 999, 1_000)]
    return low - max(highs)


def hole_ratio(m: Measurements, cutoff_hz: float | None) -> tuple[float | None, int]:
    """Share of (frame, band) cells where a high band vanishes while the
    music around it is playing: the MP3 encoder switching bands off."""
    if m.band_db.size == 0:
        return None, 0
    top = (cutoff_hz or m.sample_rate / 2) - 500
    bands = m.band_edges + 500 <= top
    if not bands.any():
        return None, 0
    loud = m.frame_ref_db >= np.median(m.frame_ref_db) - 10
    cells = m.band_db[loud][:, bands]
    if len(cells) < 20:
        return None, 0
    typical = np.median(cells, axis=0)
    # Bands with nothing in them (silence, very dark recordings) say nothing.
    active = typical > -80
    if not active.any():
        return None, 0
    holes = cells[:, active] < typical[active] - HOLE_DROP_DB
    return float(holes.mean()), int(active.sum())


def side_drop(m: Measurements, cutoff_hz: float | None) -> float | None:
    """How much the stereo difference shrinks at high frequencies compared to
    the midrange. Joint-stereo MP3 throws most of it away above ~12 kHz."""
    if m.side_power is None:
        return None
    top = min((cutoff_hz or m.sample_rate / 2) - 500, 16_000)
    if top <= 12_500:
        return None
    ratio = m.side_power / (m.mid_power + 1e-30)
    low = _band_mean_db(m.freqs, ratio, 2_000, 8_000)
    high = _band_mean_db(m.freqs, ratio, 12_000, top)
    # Near-mono material has no side signal to lose.
    if low is None or high is None or low < -30:
        return None
    return low - high


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
        gap = hires_gap(m)
        metrics["hires_gap_db"] = gap
        if gap >= HIRES_GAP_DB:
            ev.append(Evidence("hires-content", "fail",
                               f"Above 26 kHz the level is {gap:.0f} dB under the 16-20 kHz range: this "
                               f"{m.sample_rate / 1000:g} kHz file was upsampled from CD-rate audio.",
                               "upsampled", gap, f">= {HIRES_GAP_DB:g} dB"))
        else:
            ev.append(Evidence("hires-content", "pass", f"There is real content above 26 kHz ({gap:.0f} dB under "
                               "the 16-20 kHz range).", value=gap))
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
        ev.append(Evidence("hf-holes", "fail", f"High bands drop out in {holes:.1%} of loud moments: MP3-style "
                           "band switching.", "lossy-transcode", holes, f">= {HOLES_FAKE:.0%}"))
    elif holes >= HOLES_SUSPECT:
        ev.append(Evidence("hf-holes", "warn", f"High bands drop out in {holes:.1%} of loud moments.",
                           "lossy-transcode", holes, f">= {HOLES_SUSPECT:.0%}"))
    else:
        ev.append(Evidence("hf-holes", "pass", f"High bands are continuous ({holes:.1%} dropouts over {n_bands} bands).",
                           value=holes))

    # Joint stereo.
    sd = side_drop(m, cutoff)
    metrics["side_drop_db"] = sd
    if sd is not None and sd >= SIDE_DROP_SUSPECT_DB:
        ev.append(Evidence("side-channel", "warn", f"Stereo difference is {sd:.0f} dB weaker above 12 kHz than in "
                           "the midrange: typical of joint-stereo MP3.", "lossy-transcode", sd,
                           f">= {SIDE_DROP_SUSPECT_DB:g} dB"))
    elif sd is not None:
        ev.append(Evidence("side-channel", "pass", f"Stereo image holds up at high frequencies ({sd:.0f} dB).", value=sd))

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
