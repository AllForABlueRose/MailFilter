"""Encode arbitrary bytes as a PNG image, and back.

Used to export/import search-setting templates as image files: a template's
serialized bytes are packed straight into the pixels of a truecolor PNG, so the
file is a real, viewable image (it renders as colored noise) yet carries no JSON
text and nothing human-legible. This is deliberately **not** encryption — anyone
who knows the scheme can recover the bytes; it only keeps the payload from being
read at a glance, grepped out of the file, or hand-edited. See
docs/system-design.md for why this format was chosen.

Only images produced by :func:`encode` are guaranteed to decode: every scanline
is written with PNG filter type 0 (None) at color type 2 (8-bit RGB), and the
payload is framed with a small magic+length header so the exact byte count is
recovered after the pixel grid is padded out to whole rows. A PNG re-saved by an
image editor (which may re-filter scanlines or change the pixel format) is
rejected with :class:`TemplateImageError` rather than silently mis-decoded.
"""

import struct
import zlib
from binascii import crc32

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Frames the payload inside the pixel bytes so decode recovers the exact length
# after the grid is padded to whole rows. Layout: MAGIC(4) version(1) length(4 BE).
_MAGIC = b"MFT1"
_VERSION = 1
_HEADER = struct.Struct(">4sBI")


class TemplateImageError(ValueError):
    """The bytes are not a template image produced by :func:`encode`."""


def encode(payload):
    """Pack ``payload`` (bytes) into a truecolor PNG and return the PNG bytes."""
    blob = _HEADER.pack(_MAGIC, _VERSION, len(payload)) + payload
    # Three bytes per RGB pixel; pad the run up to whole pixels.
    if len(blob) % 3:
        blob += b"\x00" * (3 - len(blob) % 3)
    pixel_count = len(blob) // 3
    width = max(1, _isqrt_ceil(pixel_count))
    height = max(1, -(-pixel_count // width))  # ceil division
    blob += b"\x00" * (width * height * 3 - len(blob))  # pad grid to width*height

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # per-scanline filter type: 0 = None
        raw.extend(blob[y * stride:(y + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join([
        _PNG_SIGNATURE,
        _chunk(b"IHDR", ihdr),
        _chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
        _chunk(b"IEND", b""),
    ])


def decode(data):
    """Recover the payload bytes from a PNG produced by :func:`encode`.

    Raises :class:`TemplateImageError` for anything that is not such an image.
    """
    if not data.startswith(_PNG_SIGNATURE):
        raise TemplateImageError("not a PNG file")
    width = height = None
    idat = bytearray()
    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # length(4) + type(4) + data + CRC(4)
        if ctype == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            if depth != 8 or color != 2:
                raise TemplateImageError("unsupported PNG pixel format")
        elif ctype == b"IDAT":
            idat.extend(body)
        elif ctype == b"IEND":
            break
    if width is None:
        raise TemplateImageError("PNG has no IHDR")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as e:
        raise TemplateImageError("corrupt PNG image data") from e

    stride = width * 3
    pixels = bytearray()
    for y in range(height):
        row = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        if not row:
            break
        if row[0] != 0:
            raise TemplateImageError("unsupported PNG scanline filter")
        pixels.extend(row[1:])

    if len(pixels) < _HEADER.size:
        raise TemplateImageError("image too small to hold a template")
    magic, _version, length = _HEADER.unpack(pixels[:_HEADER.size])
    if magic != _MAGIC:
        raise TemplateImageError("not a MailFilter template image")
    start, end = _HEADER.size, _HEADER.size + length
    if end > len(pixels):
        raise TemplateImageError("declared payload exceeds image data")
    return bytes(pixels[start:end])


def _chunk(ctype, body):
    crc = crc32(ctype + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)


def _isqrt_ceil(n):
    """Smallest integer r with r*r >= n (keeps the image roughly square)."""
    if n <= 1:
        return n
    r = int(n ** 0.5)
    while r * r < n:
        r += 1
    return r
