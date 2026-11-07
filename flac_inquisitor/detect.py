"""Turn measurements into evidence and a verdict.

Every check reports what it measured, the threshold it compared against and a
one-line explanation, so the user can judge a verdict instead of trusting it.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from flac_inquisitor.analyzer import (
    HOLE_BAND_WIDTH,
    HOP,
    REF_BAND,
    Measurements,
    measure,
)
from flac_inquisitor.flacmeta import FlacInfo, read_flac_info

# Bump whenever a threshold or a check changes: stored results with an older
# version are re-analyzed on the next scan.
ALGO_VERSION = 5

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
# Upsampled masters with noise added after the upsampling step only 6-20 dB:
# content fades into a floor that stays flat from about 23 kHz up. Spread of
# the 500 Hz bands from 26 kHz up: 46 of 47 such tracks 0.2-4.4 dB, real
# hi-res 6.6-16.7 dB (6 tracks, the only ground truth so far). The floor must
# also sit well below the 16-20 kHz content (those tracks: 12-31 dB), or a
# flat full-band recording would pass for one.
PLATEAU_SPREAD_DB = 4.5
PLATEAU_BELOW_DB = 10.0
PLATEAU_EDGE_MAX_HZ = 25_000  # content of 44.1 and 48 kHz audio ends below this
# Isolated band holes: one 500 Hz band vanishes while both neighbours keep
# playing, which music does not do but MP3 and Vorbis encoders do. Share of
# loud frames with at least one such hole: originals p90 0.08%, LAME V0
# median 1%.
HOLE_DROP_DB = 25.0
HOLE_NEIGHBOUR_DB = 10.0
HOLES_SUSPECT = 0.005
HOLES_FAKE = 0.04
# Encoder holes come and go all through a track. Synth leads and chiptune with
# sparse high harmonics leave empty bands between the harmonics, which look the
# same but only for as long as that passage lasts. Share of the 10-second
# stretches with loud audio that contain a hole, among flagged tracks: LAME V0
# and 320 and Vorbis q9 fakes 0.47-1.00 (one Vorbis fake at 0.18 with a ratio
# of 0.5%), tonal genuine tracks 0.06-0.10. Holes in fewer stretches than
# HOLES_MIN_COVER do not count.
HOLE_WINDOW_S = 10.0
HOLES_MIN_COVER = 0.30
# A short track has few stretches, so one tonal passage covers many of them.
# There the test is how much of the holes falls within 30 seconds: fakes at
# most 0.80, a 72-second tonal genuine track 1.00. Only for tracks with at
# least 6 stretches: in a shorter one, 30 seconds is most of the track.
HOLE_BURST_WINDOWS = 3
HOLE_BURST_SHARE = 0.90
HOLE_BURST_MIN_WINDOWS = 6
# Per-frame bandwidth: the highest band still above FRAME_TOP_FLOOR_DB
# (relative to the frame's 1-10 kHz level), at the 10th percentile of loud
# frames. High-bitrate MP3 and Vorbis switch the top bands off now and then,
# which the average spectrum smooths over. Calibration on 42 originals: 11 of
# the 18 LAME V0 and 13 of the 25 Vorbis q9 fakes missed by the other checks
# stop below 20.5 kHz (top edge of the band), but so do 4 originals (dark
# recordings). Measured and
# shown only, until the CD-verified tracks of a real library (Accuracy page)
# say where a threshold can go.
FRAME_TOP_FLOOR_DB = -70.0
FRAME_TOP_SHOWN_HZ = 20_500
LOSSYWAV_FAKE_RATIO = 0.25
LOSSYWAV_SUSPECT_RATIO = 0.05
MIN_LOUD_BLOCKS = 10
# Left and right identical in all but a few frames (a click, a fade) is mono
# audio stored as stereo.
MONO_MAX_DIFF = 0.001
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


def hires_floor(m: Measurements) -> dict:
    """The noise floor above the CD band of a hi-res file: how flat it is
    (spread) up to top, how far below the 16-20 kHz content (below), and
    where the spectrum reaches it (edge)."""
    ref = _band_mean_db(m.freqs, m.mid_power, *REF_BAND)
    level = lambda lo, hi: _band_mean_db(m.freqs, m.mid_power, lo, hi) - ref  # noqa: E731
    top = min(0.85 * m.sample_rate / 2, 40_000)  # below the final anti-alias roll-off
    floor = np.array([level(f, f + 500) for f in np.arange(26_000, top, 500)])
    median = float(np.median(floor))
    edge = next((float(f) for f in np.arange(18_000, 26_000, 250) if level(f, f + 250) <= median + 3), None)
    return {"spread": float(np.percentile(floor, 90) - np.percentile(floor, 10)),
            "below": float(np.mean([level(f, f + 500) for f in range(16_000, 20_000, 500)]) - median),
            "edge": edge, "top": top}


def hole_ratio(m: Measurements, cutoff_hz: float | None) -> dict | None:
    """Share of loud frames where a high band vanishes while the bands on both
    sides keep playing: the encoder giving up on that band for a moment.

    Also how the holes spread over the track: cover is the share of the
    HOLE_WINDOW_S stretches with loud frames that contain a hole, burst the
    largest share of the holes within HOLE_BURST_WINDOWS consecutive stretches
    (None for tracks too short to tell)."""
    if m.band_db.size == 0:
        return None
    top = (cutoff_hz or m.sample_rate / 2) - 500
    bands = m.band_edges + 500 <= top
    loud = m.frame_ref_db >= np.median(m.frame_ref_db) - 10
    cells = m.band_db[loud][:, bands]
    if len(cells) < 20:
        return None
    typical = np.median(cells, axis=0)
    # Bands with nothing in them (silence, very dark recordings) say nothing.
    active = typical > -80
    if active.sum() < 3:
        return None
    cells, typical = cells[:, active], typical[active]
    low = cells < typical - HOLE_DROP_DB
    playing = cells >= typical - HOLE_NEIGHBOUR_DB
    isolated = low[:, 1:-1] & playing[:, :-2] & playing[:, 2:]
    hit = isolated.any(axis=1)
    window = (np.flatnonzero(loud) * HOP / m.sample_rate // HOLE_WINDOW_S).astype(int)
    loud_windows = len(np.unique(window))
    cover = len(np.unique(window[hit])) / loud_windows
    burst = None
    if hit.any() and loud_windows >= HOLE_BURST_MIN_WINDOWS:
        counts = np.bincount(window[hit], minlength=window.max() + 1)
        burst = float(np.convolve(counts, np.ones(HOLE_BURST_WINDOWS), "valid").max() / hit.sum())
    return {"ratio": float(hit.mean()), "bands": int(active.sum()), "cover": cover, "burst": burst}


def frame_top(m: Measurements) -> float | None:
    """10th percentile, over loud frames, of the highest band with content."""
    if len(m.frame_ref_db) < 20 or m.band_db.shape[1] == 0:
        return None
    loud = m.frame_ref_db >= np.median(m.frame_ref_db) - 10
    over = m.band_db[loud] > FRAME_TOP_FLOOR_DB
    last = over.shape[1] - 1 - np.argmax(over[:, ::-1], axis=1)
    tops = np.where(over.any(axis=1), m.band_edges[last] + HOLE_BAND_WIDTH, m.band_edges[0])
    return float(np.percentile(tops, 10))


def evaluate(info: FlacInfo, m: Measurements, soft: frozenset[str] = frozenset()) -> Result:
    """soft: categories that make the file suspect at most (see grade)."""
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
        floor = hires_floor(m)
        metrics["hires_step_db"] = step
        metrics["hires_flat_db"] = flat
        metrics["hires_floor_spread_db"] = floor["spread"]
        metrics["hires_edge_hz"] = floor["edge"]
        rate = f"{m.sample_rate / 1000:g} kHz"
        if step >= HIRES_STEP_FAKE_DB or (step >= HIRES_STEP_SUSPECT_DB and flat <= HIRES_FLAT_DB):
            ev.append(Evidence("hires-content", "fail",
                               f"The spectrum falls {step:.0f} dB between 20 and 26 kHz and is flat above: this "
                               f"{rate} file was upsampled from 44.1 or 48 kHz audio.",
                               "upsampled", step, f">= {HIRES_STEP_FAKE_DB:g} dB"))
        elif (floor["spread"] <= PLATEAU_SPREAD_DB and floor["below"] >= PLATEAU_BELOW_DB
              and floor["edge"] is not None and floor["edge"] <= PLATEAU_EDGE_MAX_HZ):
            # The edge says where the content ends, not the source rate: in
            # calibration, files upsampled from 44.1 kHz also reached their
            # floor at 23-25 kHz, through the resampler's roll-off.
            ev.append(Evidence("hires-content", "fail",
                               f"Above {floor['edge'] / 1000:.1f} kHz this {rate} file holds only a flat noise "
                               f"floor (within {floor['spread']:.1f} dB up to {floor['top'] / 1000:.0f} kHz): no content "
                               "beyond what 44.1 or 48 kHz audio carries. It was upsampled.",
                               "upsampled", floor["spread"], f"<= {PLATEAU_SPREAD_DB:g} dB"))
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
    found = hole_ratio(m, cutoff)
    holes = found and found["ratio"]
    metrics["hole_ratio"] = holes
    metrics["hole_cover"] = found and found["cover"]
    metrics["hole_burst"] = found and found["burst"]
    if found is None:
        ev.append(Evidence("hf-holes", "info", "Not enough high-frequency content to look for dropouts."))
    elif holes >= HOLES_SUSPECT and found["cover"] < HOLES_MIN_COVER:
        ev.append(Evidence("hf-holes", "info", f"In {holes:.1%} of loud moments a high band vanishes, but only in "
                           f"{found['cover']:.0%} of the track's {HOLE_WINDOW_S:g}-second stretches: one tonal "
                           "passage, such as a synth lead with sparse harmonics, not an encoder. Not counted.",
                           value=holes))
    elif holes >= HOLES_SUSPECT and found["burst"] is not None and found["burst"] >= HOLE_BURST_SHARE:
        ev.append(Evidence("hf-holes", "info", f"In {holes:.1%} of loud moments a high band vanishes, but "
                           f"{found['burst']:.0%} of these moments fall within "
                           f"{HOLE_BURST_WINDOWS * HOLE_WINDOW_S:g} seconds: one tonal passage, such as a synth "
                           "lead with sparse harmonics, not an encoder. Not counted.", value=holes))
    elif holes >= HOLES_FAKE:
        ev.append(Evidence("hf-holes", "fail", f"In {holes:.1%} of loud moments a high band vanishes while its "
                           "neighbours play on: an encoder dropping bands.", "lossy-transcode", holes,
                           f">= {HOLES_FAKE:.1%}"))
    elif holes >= HOLES_SUSPECT:
        ev.append(Evidence("hf-holes", "warn", f"In {holes:.1%} of loud moments a high band vanishes while its "
                           "neighbours play on.", "lossy-transcode", holes, f">= {HOLES_SUSPECT:.1%}"))
    else:
        ev.append(Evidence("hf-holes", "pass", f"High bands are continuous ({holes:.2%} of loud moments with "
                           f"isolated dropouts, {found['bands']} bands checked).", value=holes))

    top = frame_top(m)
    metrics["frame_top_hz"] = top
    if top is not None and top < FRAME_TOP_SHOWN_HZ:
        ev.append(Evidence("frame-bandwidth", "info", f"In one loud moment in ten, nothing reaches above "
                           f"{top / 1000:.1f} kHz. High-bitrate MP3 and Vorbis do this, dark recordings too: "
                           "measured only, not part of the verdict yet.", value=top))

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

    # Mono stored as stereo. Mono recordings are released like this on CD, so
    # on its own it is only a hint; album coherence flags a mono track in a
    # stereo album.
    if m.channel_diff is not None:
        metrics["mono"] = m.channel_diff < MONO_MAX_DIFF
        if metrics["mono"]:
            ev.append(Evidence("channels", "info", "Left and right channels are identical: mono audio stored as "
                               "stereo.", value=m.channel_diff))

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

    return _verdict(ev, m.audio_md5, metrics, soft)


def grade(marks: list[tuple[str, str | None]], soft: frozenset[str] = frozenset()) -> str:
    """Verdict from the (result, category) of each piece of evidence. A fail
    in a soft category counts as a warning: such a file is suspect at most."""
    fails = [c for r, c in marks if r == "fail" and c not in soft]
    warns = [c for r, c in marks if r == "warn" or (r == "fail" and c in soft)]
    # Two independent warnings pointing to the same cause are as good as a fail.
    hard = [c for c in warns if c not in soft]
    if fails or any(hard.count(c) >= 2 for c in hard):
        return "fake"
    return "suspect" if warns else "ok"


def _verdict(ev: list[Evidence], audio_md5: str, metrics: dict, soft: frozenset[str]) -> Result:
    flagged = [e for e in ev if e.result == "fail"] + [e for e in ev if e.result == "warn"]
    categories = sorted({e.category for e in flagged if e.category})
    summary = flagged[0].message if flagged else "Looks like genuine lossless audio."
    return Result(grade([(e.result, e.category) for e in ev], soft), categories, summary, ev, audio_md5, metrics)


def analyze_file(path: str | Path, soft: frozenset[str] = frozenset()) -> Result:
    info = read_flac_info(path)
    return evaluate(info, measure(path, info), soft)


# Album coherence. A track that differs from the rest of its album points to a
# release assembled from several sources, even when each track alone passes.
OUTLIER_CUTOFF_HZ = 2_000


def album_coherence(tracks: list[dict]) -> dict[int, Evidence]:
    """tracks: dicts with "format" ("44100/16"), "cutoff_hz" (None = no hard
    cutoff) and "mono" (True, False, or None when not stereo). Returns
    evidence for the tracks that stand out, by index."""
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

    stereo = [t.get("mono") is False for t in tracks]
    if sum(stereo) > len(tracks) / 2:
        for i, t in enumerate(tracks):
            if t.get("mono") and i not in out:
                out[i] = Evidence("album-mono", "warn", "Left and right channels are identical, while the rest of "
                                  "the album is stereo: mono audio made to look like stereo.", "fake-stereo")

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
