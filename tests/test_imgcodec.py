"""Tests for mailfilter.imgcodec: the bytes<->PNG template-image codec.

The codec must round-trip arbitrary bytes through a real PNG (valid signature,
viewable image) and reject anything that isn't an image it produced.
"""

import struct
import unittest
import zlib
from binascii import crc32

from mailfilter import imgcodec

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _make_foreign_png(width, height):
    """A valid 8-bit RGB PNG of all-zero pixels, NOT produced by imgcodec — i.e. a
    real image a user might try to import. Its pixels carry no template MAGIC."""
    def chunk(ctype, body):
        return (struct.pack(">I", len(body)) + ctype + body
                + struct.pack(">I", crc32(ctype + body) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (_PNG_SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class RoundTripTests(unittest.TestCase):
    def _round_trip(self, payload):
        self.assertEqual(imgcodec.decode(imgcodec.encode(payload)), payload)

    def test_empty_payload(self):
        self._round_trip(b"")

    def test_small_payload(self):
        self._round_trip(b"hello")

    def test_payload_not_a_multiple_of_three(self):
        # Exercises the per-pixel padding for every residue (0, 1, 2 bytes over).
        for n in (3, 4, 5, 6, 7):
            self._round_trip(b"x" * n)

    def test_all_byte_values(self):
        self._round_trip(bytes(range(256)))

    def test_large_payload_spans_many_rows(self):
        self._round_trip(b"A" * 50000)

    def test_utf8_json_like_payload(self):
        self._round_trip('{"name": "wîth ünïcode"}'.encode("utf-8"))


class FormatTests(unittest.TestCase):
    def test_output_is_a_real_png(self):
        png = imgcodec.encode(b"data")
        self.assertTrue(png.startswith(_PNG_SIGNATURE))
        self.assertIn(b"IHDR", png)
        self.assertIn(b"IEND", png)

    def test_payload_is_not_legible_in_the_image(self):
        png = imgcodec.encode(b"secret-keyword-1234567890")
        # The raw bytes are zlib-compressed into IDAT, so the plaintext does not
        # appear verbatim in the file.
        self.assertNotIn(b"secret-keyword-1234567890", png)


class RejectionTests(unittest.TestCase):
    def test_non_png_rejected(self):
        with self.assertRaises(imgcodec.TemplateImageError):
            imgcodec.decode(b"not a png at all")

    def test_png_signature_then_garbage_rejected(self):
        # Right signature, no valid IHDR/IDAT after it.
        with self.assertRaises(imgcodec.TemplateImageError):
            imgcodec.decode(_PNG_SIGNATURE + b"\x00\x00\x00\x00garbage")

    def test_foreign_png_rejected(self):
        # A structurally valid PNG that this codec didn't produce: its pixels
        # carry no template MAGIC, so it is rejected rather than mis-decoded.
        with self.assertRaises(imgcodec.TemplateImageError):
            imgcodec.decode(_make_foreign_png(8, 8))

    def test_empty_bytes_rejected(self):
        with self.assertRaises(imgcodec.TemplateImageError):
            imgcodec.decode(b"")


if __name__ == "__main__":
    unittest.main()
