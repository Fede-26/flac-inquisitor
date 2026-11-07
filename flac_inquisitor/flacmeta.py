"""Read the metadata of a FLAC file without decoding any audio.

Only the parts the detector needs are parsed: STREAMINFO (format and audio MD5),
VORBIS_COMMENT (vendor string and tags) and the presence of ID3 tags, which a
spec-compliant FLAC encoder never writes.
"""

from dataclasses import dataclass, field
from pathlib import Path

STREAMINFO = 0
VORBIS_COMMENT = 4


class NotFlacError(ValueError):
    pass


@dataclass
class FlacInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    # 0 means "unknown": the encoder did not know the length in advance.
    total_samples: int
    # 16 zero bytes means the encoder did not compute it.
    md5: bytes
    vendor: str | None = None
    tags: dict[str, list[str]] = field(default_factory=dict)
    has_id3v2: bool = False
    has_id3v1: bool = False
    audio_offset: int = 0
    file_size: int = 0

    @property
    def md5_hex(self) -> str | None:
        return self.md5.hex() if any(self.md5) else None

    @property
    def duration(self) -> float:
        return self.total_samples / self.sample_rate if self.sample_rate else 0.0


def _id3v2_size(header: bytes) -> int:
    # ID3v2 sizes are "syncsafe": 7 significant bits per byte.
    size = 0
    for byte in header[6:10]:
        size = (size << 7) | (byte & 0x7F)
    has_footer = header[5] & 0x10
    return 10 + size + (10 if has_footer else 0)


def _parse_vorbis_comment(data: bytes) -> tuple[str, dict[str, list[str]]]:
    # Unlike the rest of FLAC, Vorbis comment lengths are little-endian.
    pos = 0

    def read_u32() -> int:
        nonlocal pos
        value = int.from_bytes(data[pos : pos + 4], "little")
        pos += 4
        return value

    vendor_len = read_u32()
    vendor = data[pos : pos + vendor_len].decode("utf-8", "replace")
    pos += vendor_len
    tags: dict[str, list[str]] = {}
    for _ in range(read_u32()):
        length = read_u32()
        entry = data[pos : pos + length].decode("utf-8", "replace")
        pos += length
        key, sep, value = entry.partition("=")
        if sep:
            tags.setdefault(key.upper(), []).append(value)
    return vendor, tags


def read_flac_info(path: str | Path) -> FlacInfo:
    path = Path(path)
    with path.open("rb") as f:
        head = f.read(10)
        has_id3v2 = head[:3] == b"ID3"
        start = _id3v2_size(head) if has_id3v2 else 0
        f.seek(start)
        if f.read(4) != b"fLaC":
            raise NotFlacError(f"{path}: no fLaC marker")

        info: FlacInfo | None = None
        vendor, tags = None, {}
        while True:
            header = f.read(4)
            if len(header) < 4:
                raise NotFlacError(f"{path}: truncated metadata")
            is_last = header[0] & 0x80
            block_type = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            if block_type == STREAMINFO:
                data = f.read(length)
                # Bytes 10..17 pack: sample rate (20 bits), channels - 1 (3),
                # bits per sample - 1 (5), total samples (36).
                packed = int.from_bytes(data[10:18], "big")
                info = FlacInfo(
                    sample_rate=packed >> 44,
                    channels=((packed >> 41) & 0x7) + 1,
                    bits_per_sample=((packed >> 36) & 0x1F) + 1,
                    total_samples=packed & 0xFFFFFFFFF,
                    md5=data[18:34],
                )
            elif block_type == VORBIS_COMMENT:
                vendor, tags = _parse_vorbis_comment(f.read(length))
            else:
                f.seek(length, 1)
            if is_last:
                break

        if info is None:
            raise NotFlacError(f"{path}: no STREAMINFO block")
        info.vendor = vendor
        info.tags = tags
        info.has_id3v2 = has_id3v2
        info.audio_offset = f.tell()
        info.file_size = f.seek(0, 2)
        if info.file_size >= 128:
            f.seek(-128, 2)
            info.has_id3v1 = f.read(3) == b"TAG"
        return info
