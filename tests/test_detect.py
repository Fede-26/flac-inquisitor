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


@pytest.mark.xfail(reason="ffmpeg's LAME V0 keeps the full band on stationary noise; "
                   "band dropouts need real music to show up (calibration step)")
def test_mp3_v0_is_suspect(samples):
    assert verdict(samples, "mp3_v0").verdict != "ok"


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
