import pytest

from flac_inquisitor.flacmeta import NotFlacError, read_flac_info


def test_streaminfo(samples):
    info = read_flac_info(samples["real"])
    assert (info.sample_rate, info.channels, info.bits_per_sample) == (44_100, 2, 16)
    assert info.total_samples == 44_100 * 8
    assert info.md5_hex is not None
    assert info.vendor.startswith("Lavf")
    assert not info.has_id3v2 and not info.has_id3v1


def test_hires_streaminfo(samples):
    info = read_flac_info(samples["upsampled"])
    assert (info.sample_rate, info.bits_per_sample) == (96_000, 24)


def test_id3v2_prefix_is_skipped(samples):
    info = read_flac_info(samples["id3"])
    assert info.has_id3v2
    assert info.md5 == read_flac_info(samples["real"]).md5


def test_not_flac(tmp_path):
    path = tmp_path / "x.flac"
    path.write_bytes(b"RIFF" + b"\x00" * 100)
    with pytest.raises(NotFlacError):
        read_flac_info(path)
