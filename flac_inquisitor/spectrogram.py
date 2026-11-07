"""Spectrogram images, rendered by ffmpeg and cached on disk.

The image has no legend: the page draws the axes itself, so it can mark the
measured cutoff and follow the theme. Frequency is linear from 0 Hz at the
bottom edge to Nyquist at the top edge; time runs left to right.
"""

import re
import subprocess
from pathlib import Path

from flac_inquisitor import db


class SpectrogramError(RuntimeError):
    pass


def render(path: Path, key: str, width: int, height: int) -> Path:
    cache = db.DATA_PATH / "spectrograms"
    cache.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", key)[-80:]
    out = cache / f"{safe}-{width}x{height}.png"
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.png")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
           "-lavfi", f"showspectrumpic=s={width}x{height}:legend=0:mode=combined:color=magma:scale=log:drange=120",
           "-frames:v", "1", str(tmp)]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise SpectrogramError(f"ffmpeg could not draw the spectrogram: {proc.stderr.strip()[:300]}")
    tmp.replace(out)
    return out
