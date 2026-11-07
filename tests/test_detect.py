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
