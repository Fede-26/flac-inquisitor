"""Synthetic test material.

No real music is used in the test suite. Pink noise stands in for a full-band
recording; the fakes are made from it with the same tools a real transcoder
would use (ffmpeg with LAME, AAC, Opus).
"""

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

RATE = 44_100
SECONDS = 8


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n)
    freqs[0] = freqs[1]
    noise = np.fft.irfft(spectrum / np.sqrt(freqs), n)
    return noise / np.abs(noise).max()


def stereo_music_like(n: int, rng: np.random.Generator) -> np.ndarray:
    """Partly correlated channels with a slow loudness envelope."""
    common, left, right = (pink_noise(n, rng) for _ in range(3))
    envelope = 0.6 + 0.4 * np.sin(np.linspace(0, 6 * np.pi, n)) ** 2
    stereo = np.stack([common + 0.5 * left, common + 0.5 * right], axis=1) * envelope[:, None]
    return stereo / np.abs(stereo).max() * 0.7


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = np.round(samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def to_flac(src: Path, dst: Path, *extra: str) -> Path:
    ffmpeg("-i", str(src), *extra, "-c:a", "flac", str(dst))
    return dst


def transcode(src: Path, dst: Path, codec_args: list[str], ext: str) -> Path:
    lossy = dst.with_suffix(ext)
    ffmpeg("-i", str(src), *codec_args, str(lossy))
    return to_flac(lossy, dst, "-sample_fmt", "s16")


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("samples")
    rng = np.random.default_rng(1)
    music = stereo_music_like(RATE * SECONDS, rng)

    write_wav(d / "real.wav", music)
    out = {"real": to_flac(d / "real.wav", d / "real.flac")}

    # Tape-like: a gentle 12 dB/octave roll-off from 9 kHz, no hard edge.
    b, a = signal.butter(2, 9_000, fs=RATE)
    tape = signal.lfilter(b, a, music, axis=0)
    write_wav(d / "tape.wav", tape / np.abs(tape).max() * 0.7)
    out["tape"] = to_flac(d / "tape.wav", d / "tape.flac")

    real = out["real"]
    out["mp3_128"] = transcode(real, d / "mp3_128.flac", ["-c:a", "libmp3lame", "-b:a", "128k"], ".mp3")
    out["mp3_320"] = transcode(real, d / "mp3_320.flac", ["-c:a", "libmp3lame", "-b:a", "320k"], ".mp3")
    out["mp3_v0"] = transcode(real, d / "mp3_v0.flac", ["-c:a", "libmp3lame", "-q:a", "0"], ".mp3")
    out["aac_128"] = transcode(real, d / "aac_128.flac", ["-c:a", "aac", "-b:a", "128k"], ".m4a")
    out["opus_128"] = transcode(real, d / "opus_128.flac", ["-c:a", "libopus", "-b:a", "128k"], ".opus")

    out["upsampled"] = to_flac(real, d / "upsampled.flac", "-ar", "96000", "-sample_fmt", "s32",
                               "-bits_per_raw_sample", "24")
    out["padded"] = to_flac(real, d / "padded.flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24")

    # lossyWAV-like: each 512-sample block rounded to a random number of bits.
    pcm = np.round(music * 32767).astype(np.int32)
    for start in range(0, len(pcm), 512):
        drop = int(rng.integers(1, 4))
        block = pcm[start : start + 512]
        pcm[start : start + 512] = (block >> drop) << drop
    write_wav(d / "lossywav.wav", pcm / 32767)
    out["lossywav"] = to_flac(d / "lossywav.wav", d / "lossywav.flac")

    data = real.read_bytes()
    corrupt = d / "corrupt.flac"
    corrupt.write_bytes(data[: len(data) // 2])
    out["corrupt"] = corrupt

    id3 = d / "id3.flac"
    # Minimal ID3v2.4 header with a 10-byte padding body, then the FLAC stream.
    id3.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + data)
    out["id3"] = id3
    return out
