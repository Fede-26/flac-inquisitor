"""Decode a FLAC file once and collect the measurements the detector needs.

The audio is streamed from ffmpeg in chunks, so memory use does not depend on
the length of the track: a ten-minute 24/192 file would need over 1 GB as
float32. In the same pass the decoded samples are hashed, which both checks the
STREAMINFO MD5 (corruption) and gives an audio fingerprint for files whose
encoder left the MD5 empty.
"""

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from flac_inquisitor.flacmeta import FlacInfo

FFT_SIZE = 4096
HOP = FFT_SIZE // 2
BIT_BLOCK = 4096
CHUNK_FRAMES = FFT_SIZE * 64

# High-frequency bands watched for MP3-style dropouts: 500 Hz wide from 11 kHz.
# Below 11 kHz MP3 encoders rarely drop whole bands at usual bitrates.
HOLE_BAND_START = 11_000
HOLE_BAND_WIDTH = 500
HOLE_BAND_STOP = 24_000

REF_BAND = (1_000, 10_000)


@dataclass
class Measurements:
    sample_rate: int
    channels: int
    bits_per_sample: int
    decoded_samples: int
    audio_md5: str
    decode_errors: str
    leading_zero_samples: int
    freqs: np.ndarray  # FFT bin centres, Hz
    mid_power: np.ndarray  # mean power per bin of (L+R)/2
    band_edges: np.ndarray  # lower edges of the hole bands, Hz
    band_db: np.ndarray  # frames x bands, band energy relative to the frame's reference band, dB
    frame_ref_db: np.ndarray  # per frame, mean power in REF_BAND, dB
    block_trailing_zeros: np.ndarray  # per loud block, unused low bits
    block_count: int  # non-silent blocks, loud or not


class DecodeError(RuntimeError):
    pass


def _pack_for_md5(samples: np.ndarray, bits: int) -> bytes:
    """Serialize samples the way FLAC computes its MD5: interleaved, signed,
    little-endian, ceil(bits / 8) bytes per sample."""
    width = (bits + 7) // 8
    as_bytes = samples.astype("<i4", copy=False).reshape(-1).view(np.uint8).reshape(-1, 4)
    return as_bytes[:, :width].tobytes()


def _trailing_zeros(value: int) -> int:
    return (value & -value).bit_length() - 1


def decode_chunks(path: Path, channels: int, stderr_file):
    """Yield int32 arrays (frames x channels), samples left-aligned in 32 bits."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0", "-f", "s32le", "-acodec", "pcm_s32le", "-",
    ]  # fmt: skip
    # stderr goes to a file: a badly corrupt file can print more errors than a
    # pipe buffer holds, and nobody reads the pipe until decoding ends.
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file) as proc:
        frame_bytes = 4 * channels
        while True:
            data = proc.stdout.read(CHUNK_FRAMES * frame_bytes)
            if not data:
                break
            usable = len(data) - len(data) % frame_bytes
            yield np.frombuffer(data[:usable], dtype="<i4").reshape(-1, channels)
        proc.wait()


def measure(path: str | Path, info: FlacInfo) -> Measurements:
    path = Path(path)
    sr, channels, bits = info.sample_rate, info.channels, info.bits_per_sample
    shift = 32 - bits

    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / sr)
    window = np.hanning(FFT_SIZE)
    # Window power normalisation, so levels do not depend on FFT_SIZE.
    norm = 1.0 / (np.sum(window**2) * (2**31) ** 2)

    ref_bins = (freqs >= REF_BAND[0]) & (freqs < REF_BAND[1])
    band_edges = np.arange(HOLE_BAND_START, min(HOLE_BAND_STOP, sr / 2), HOLE_BAND_WIDTH)
    # Matrix that averages FFT bins into bands with one multiplication per chunk.
    band_matrix = np.zeros((len(freqs), len(band_edges)))
    for b, edge in enumerate(band_edges):
        band_matrix[(freqs >= edge) & (freqs < edge + HOLE_BAND_WIDTH), b] = 1.0
    band_matrix /= np.maximum(band_matrix.sum(axis=0), 1)

    mid_sum = np.zeros(len(freqs))
    frame_count = 0
    band_rows, ref_rows = [], []
    trailing = []
    block_count = 0
    md5 = hashlib.md5()
    decoded = 0
    leading_zero = 0
    seen_signal = False
    carry = np.zeros((0, channels), dtype=np.int32)

    with tempfile.TemporaryFile() as err:
        for chunk in decode_chunks(path, channels, err):
            decoded += len(chunk)
            native = chunk >> shift
            md5.update(_pack_for_md5(native, bits))

            if not seen_signal:
                nonzero = np.flatnonzero(np.any(native != 0, axis=1))
                if len(nonzero):
                    leading_zero += int(nonzero[0])
                    seen_signal = True
                else:
                    leading_zero += len(chunk)

            # Unused low bits per block: OR of all samples keeps a low bit set
            # if any sample uses it (two's complement keeps trailing zeros of
            # negative values). Only full blocks with real signal count:
            # near-silent fades legitimately use few bits.
            usable = len(native) - len(native) % BIT_BLOCK
            if usable:
                blocks = native[:usable].reshape(-1, BIT_BLOCK * channels)
                ors = np.bitwise_or.reduce(blocks, axis=1)
                peaks = np.abs(blocks).max(axis=1)
                loud_level = 1 << max(bits - 8, 1)  # about -42 dBFS
                for value, peak in zip(ors.tolist(), peaks.tolist()):
                    if value == 0:
                        continue
                    block_count += 1
                    if peak >= loud_level:
                        trailing.append(_trailing_zeros(value))

            # Spectral frames with 50% overlap across chunk boundaries.
            data = np.concatenate([carry, chunk]) if len(carry) else chunk
            if len(data) >= FFT_SIZE:
                as_float = data.astype(np.float64)
                left = as_float[:, 0]
                right = as_float[:, 1] if channels >= 2 else left
                n_frames = (len(data) - FFT_SIZE) // HOP + 1
                idx = np.arange(FFT_SIZE)[None, :] + HOP * np.arange(n_frames)[:, None]
                mid_spec = np.abs(np.fft.rfft(((left + right) / 2)[idx] * window, axis=1)) ** 2 * norm
                mid_sum += mid_spec.sum(axis=0)
                frame_count += n_frames
                ref = mid_spec[:, ref_bins].mean(axis=1)
                ref_db = 10 * np.log10(ref + 1e-30)
                band_db = 10 * np.log10(mid_spec @ band_matrix + 1e-30) - ref_db[:, None]
                ref_rows.append(ref_db)
                band_rows.append(band_db)
                carry = data[n_frames * HOP :]
            else:
                carry = data

        err.seek(0)
        errors = err.read().decode("utf-8", "replace").strip()

    if decoded == 0:
        raise DecodeError(errors or "no audio decoded")

    frames = max(frame_count, 1)
    return Measurements(
        sample_rate=sr,
        channels=channels,
        bits_per_sample=bits,
        decoded_samples=decoded,
        audio_md5=md5.hexdigest(),
        decode_errors=errors,
        leading_zero_samples=leading_zero,
        freqs=freqs,
        mid_power=mid_sum / frames,
        band_edges=band_edges,
        band_db=np.concatenate(band_rows) if band_rows else np.zeros((0, len(band_edges))),
        frame_ref_db=np.concatenate(ref_rows) if ref_rows else np.zeros(0),
        block_trailing_zeros=np.array(trailing, dtype=np.int64),
        block_count=block_count,
    )
