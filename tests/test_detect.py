import numpy as np
import pytest

from flac_inquisitor.detect import analyze_file


def verdict(samples, name):
    return analyze_file(samples[name])


@pytest.mark.parametrize("name", ["real", "tape", "id3"])
def test_genuine_files_pass(samples, name):
    result = verdict(samples, name)
    assert result.verdict == "ok", result.summary


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("mp3_128", "lossy-transcode"),
        ("aac_128", "lossy-transcode"),
        ("upsampled", "upsampled"),
        ("padded", "padded-bits"),
        ("lossywav", "lossywav"),
        ("corrupt", "corrupt"),
    ],
)
def test_fakes_are_caught(samples, name, category):
    result = verdict(samples, name)
    assert result.verdict == "fake", result.summary
    assert category in result.categories


@pytest.mark.parametrize("name", ["mp3_320", "opus_128"])
def test_high_bitrate_lossy_is_at_least_suspect(samples, name):
    result = verdict(samples, name)
    assert result.verdict in ("suspect", "fake"), result.summary
    assert "lossy-transcode" in result.categories


def test_mp3_v0_is_caught(samples):
    # LAME V0 keeps the full band on stationary noise; only the short frames
    # of the side channel show its dropped bands.
    result = verdict(samples, "mp3_v0")
    assert result.verdict != "ok", result.summary
    assert "lossy-transcode" in result.categories


def test_audio_md5_matches_streaminfo(samples):
    from flac_inquisitor.flacmeta import read_flac_info

    result = verdict(samples, "real")
    assert result.audio_md5 == read_flac_info(samples["real"]).md5_hex


def test_padded_file_reports_effective_bits(samples):
    assert verdict(samples, "padded").metrics["effective_bits"] == 16


def test_evidence_is_serializable(samples):
    import json

    json.dumps(verdict(samples, "mp3_128").to_dict())


def test_album_coherence_flags_the_odd_track():
    from flac_inquisitor.detect import album_coherence

    tracks = [{"format": "44100/16", "cutoff_hz": None} for _ in range(5)]
    tracks[2] = {"format": "44100/16", "cutoff_hz": 16_000.0}
    tracks[4] = {"format": "48000/24", "cutoff_hz": None}
    flagged = album_coherence(tracks)
    assert set(flagged) == {2, 4}
    assert flagged[2].check == "album-cutoff"
    assert flagged[4].check == "album-format"


def test_album_coherence_needs_a_majority():
    from flac_inquisitor.detect import album_coherence

    tracks = [{"format": f, "cutoff_hz": None} for f in ("44100/16", "44100/16", "48000/24", "48000/24")]
    assert album_coherence(tracks) == {}


def test_mono_stored_as_stereo_is_only_a_hint(samples):
    result = verdict(samples, "mono")
    assert result.metrics["mono"] is True
    assert result.verdict == "ok", result.summary
    assert verdict(samples, "real").metrics["mono"] is False


def test_album_coherence_flags_a_mono_track_in_a_stereo_album():
    from flac_inquisitor.detect import album_coherence

    tracks = [{"format": "44100/16", "cutoff_hz": None, "mono": False} for _ in range(4)]
    tracks[1]["mono"] = True
    flagged = album_coherence(tracks)
    assert set(flagged) == {1} and flagged[1].category == "fake-stereo"
    # A mono album is a mono recording, not a fake.
    assert album_coherence([{**t, "mono": True} for t in tracks]) == {}


def test_soft_categories_make_a_file_suspect_at_most():
    from flac_inquisitor.detect import grade

    upsampled = [("fail", "upsampled"), ("pass", None)]
    assert grade(upsampled) == "fake"
    assert grade(upsampled, frozenset({"upsampled"})) == "suspect"
    # Another cause still counts in full.
    assert grade(upsampled + [("fail", "lossy-transcode")], frozenset({"upsampled"})) == "fake"


def _holes_measurements(hole_frames, n_frames=2_000):
    """Flat high bands with band 5 dropping out in the given frames, as
    hole_ratio sees them. 2,000 frames are about 93 seconds at 44.1 kHz."""
    from flac_inquisitor.analyzer import HOLE_BAND_START, HOLE_BAND_WIDTH, Measurements

    edges = np.arange(HOLE_BAND_START, 21_000, HOLE_BAND_WIDTH, dtype=float)
    band_db = np.zeros((n_frames, len(edges)))
    band_db[hole_frames, 5] = -40.0
    return Measurements(
        sample_rate=44_100, channels=2, bits_per_sample=16, decoded_samples=n_frames * 2048,
        audio_md5="", decode_errors="", leading_zero_samples=0, freqs=np.zeros(1), mid_power=np.zeros(1),
        band_edges=edges, band_db=band_db, frame_ref_db=np.zeros(n_frames),
        block_trailing_zeros=np.zeros(0), block_count=0,
    )


def test_band_holes_spread_over_the_track_count():
    from flac_inquisitor.detect import HOLES_MIN_COVER, hole_ratio

    found = hole_ratio(_holes_measurements(np.arange(0, 2_000, 50)), None)
    assert found["ratio"] == pytest.approx(0.02)
    assert found["cover"] >= HOLES_MIN_COVER and found["burst"] < 0.9


def test_band_holes_in_one_passage_do_not_count():
    from flac_inquisitor.detect import HOLE_BURST_SHARE, HOLES_MIN_COVER, hole_ratio

    # The same 40 holes, all within about 4 seconds: one tonal passage.
    found = hole_ratio(_holes_measurements(np.arange(1_000, 1_080, 2)), None)
    assert found["ratio"] == pytest.approx(0.02)
    assert found["cover"] < HOLES_MIN_COVER

    # In a 70-second track a 20-second passage covers 3 of 7 stretches, but
    # all of its holes still fall within 30 seconds.
    found = hole_ratio(_holes_measurements(np.arange(100, 520, 10), n_frames=1_500), None)
    assert found["cover"] >= HOLES_MIN_COVER and found["burst"] >= HOLE_BURST_SHARE


def test_short_frames_look_at_the_side_channel():
    from flac_inquisitor.detect import SHORT_HOLES_MIN_COVER, short_holes

    # Short frames of 512 samples: 8,000 are about 93 seconds. Holes only in
    # the side channel, spread over the whole track.
    m = _holes_measurements([])
    m.short_hop = 512
    m.short_ref_db = np.zeros(8_000)
    m.short_band_db = np.zeros((8_000, len(m.band_edges)))
    m.short_side_db = np.zeros((8_000, len(m.band_edges)))
    m.short_side_db[np.arange(0, 8_000, 100), 5] = -40.0
    found = short_holes(m)
    assert found["mid"]["ratio"] == 0
    assert found["side"]["ratio"] == pytest.approx(0.01)
    assert found["side"]["cover"] >= SHORT_HOLES_MIN_COVER
