"""Byte-level image identity: size, EXIF capture time, and a date carried in the image."""
from __future__ import annotations

import re
import struct

_EXIF_ORIGINAL = 0x9003
_EXIF_IFD = 0x8769
_DEPICTED_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EXIF_CLOCK = re.compile(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")


def read_image_identity(mime: str, raw: bytes) -> dict:
    width, height = _dimensions(mime, raw)
    exif_time = _exif_time(raw)
    return {
        "width": width,
        "height": height,
        "exif_time": exif_time,
        "captured_at": exif_time,
        "depicted_date": _depicted_date(raw),
    }


def _dimensions(mime: str, raw: bytes) -> tuple[int | None, int | None]:
    if mime == "image/png":
        return _png_size(raw)
    if mime == "image/jpeg":
        return _jpeg_size(raw)
    if mime == "image/webp":
        return _webp_size(raw)
    return None, None


def _png_size(raw: bytes) -> tuple[int | None, int | None]:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None, None
    pos = 8
    while pos + 8 <= len(raw):
        length = struct.unpack_from(">I", raw, pos)[0]
        kind = raw[pos + 4:pos + 8]
        start = pos + 8
        if start + length > len(raw):
            return None, None
        if kind == b"IHDR" and length >= 8:
            return struct.unpack_from(">II", raw, start)
        pos = start + length + 4
    return None, None


def _jpeg_size(raw: bytes) -> tuple[int | None, int | None]:
    for marker, segment in _jpeg_segments(raw):
        if marker in range(0xC0, 0xD0) and marker not in {0xC4, 0xC8, 0xCC} and len(segment) >= 5:
            height, width = struct.unpack_from(">HH", segment, 1)
            return width, height
    return None, None


def _webp_size(raw: bytes) -> tuple[int | None, int | None]:
    if len(raw) < 30 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None, None
    if raw[12:16] == b"VP8X" and len(raw) >= 30:
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        return width, height
    return None, None


def _exif_time(raw: bytes) -> str | None:
    for marker, segment in _jpeg_segments(raw):
        if marker != 0xE1 or not segment.startswith(b"Exif\x00\x00"):
            continue
        found = _tiff_datetime_original(segment[6:])
        if found:
            return found
    return None


def _depicted_date(raw: bytes) -> str | None:
    """A date carried in the image outside EXIF. Message time is not a substitute."""
    for marker, segment in _jpeg_segments(raw):
        if marker != 0xFE:
            continue
        match = _DEPICTED_DATE.search(segment.decode("latin-1", errors="ignore"))
        if match:
            return match.group(0)
    return _png_text_date(raw)


def _png_text_date(raw: bytes) -> str | None:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    while pos + 8 <= len(raw):
        length = struct.unpack_from(">I", raw, pos)[0]
        kind = raw[pos + 4:pos + 8]
        start = pos + 8
        if start + length > len(raw):
            return None
        if kind == b"tEXt":
            text = raw[start:start + length].split(b"\x00", 1)
            value = text[1].decode("latin-1", errors="ignore") if len(text) == 2 else ""
            match = _DEPICTED_DATE.search(value)
            if match:
                return match.group(0)
        pos = start + length + 4
    return None


def _jpeg_segments(raw: bytes):
    if len(raw) < 4 or raw[:2] != b"\xff\xd8":
        return
    pos = 2
    while pos + 1 < len(raw):
        if raw[pos] != 0xFF:
            return
        while pos < len(raw) and raw[pos] == 0xFF:
            pos += 1
        if pos >= len(raw):
            return
        marker = raw[pos]
        pos += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or pos + 2 > len(raw):
            return
        seg_len = struct.unpack_from(">H", raw, pos)[0]
        if seg_len < 2 or pos + seg_len > len(raw):
            return
        yield marker, raw[pos + 2:pos + seg_len]
        pos += seg_len


def _tiff_datetime_original(tiff: bytes) -> str | None:
    if len(tiff) < 8 or tiff[:2] not in {b"II", b"MM"}:
        return None
    endian = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack_from(endian + "H", tiff, 2)[0] != 42:
        return None
    offset = struct.unpack_from(endian + "I", tiff, 4)[0]
    return _ifd_datetime(tiff, offset, endian, follow_exif=True)


def _ifd_datetime(tiff: bytes, offset: int, endian: str, *, follow_exif: bool) -> str | None:
    if offset < 0 or offset + 2 > len(tiff):
        return None
    count = struct.unpack_from(endian + "H", tiff, offset)[0]
    cursor = offset + 2
    found = None
    nested = None
    for _ in range(count):
        if cursor + 12 > len(tiff):
            return found
        tag, kind, number, value = struct.unpack_from(endian + "HHII", tiff, cursor)
        cursor += 12
        if tag == _EXIF_ORIGINAL and kind == 2:
            found = _ascii_at(tiff, number, value, endian)
        elif follow_exif and tag == _EXIF_IFD and kind == 4 and number == 1:
            nested = value
    if found:
        return found
    if nested is not None:
        return _ifd_datetime(tiff, nested, endian, follow_exif=False)
    return None


def _ascii_at(tiff: bytes, count: int, value: int, endian: str) -> str | None:
    if count <= 4:
        raw = struct.pack(endian + "I", value)[:count]
    else:
        if value < 0 or value + count > len(tiff):
            return None
        raw = tiff[value:value + count]
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    match = _EXIF_CLOCK.fullmatch(text)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"
