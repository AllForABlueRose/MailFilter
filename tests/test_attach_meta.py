"""Tests for mailfilter.attach_meta: format-native org-metadata embedding.

Each writer is exercised against a real container built with stdlib (or imgcodec),
and the key is read back out of that container. Graceful passthrough (unknown
extension, malformed input) must return the original bytes. The PDF path needs the
optional ``pikepdf`` wheel and is skipped when it isn't importable.
"""

import io
import json
import struct
import unittest
import zipfile

from mailfilter import attach_meta, imgcodec

try:  # optional heavy dep; PDF embedding is best-effort
    import pikepdf
except Exception:  # pragma: no cover - depends on the environment
    pikepdf = None

META = {"org_id": "o1", "org_name": "Acmé Corp ünïcode", "mail_id": "m123"}
CANON = json.dumps(META, ensure_ascii=False, sort_keys=True)


def _png_chunk_types(data):
    pos, types = len(imgcodec._PNG_SIGNATURE), []
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        types.append(data[pos + 4:pos + 8])
        pos += 12 + length
    return types


def _minimal_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://ct">'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://rel">'
                   '<Relationship Id="rId1" Type="t" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", "<document/>")
        z.writestr("word/vbaProject.bin", b"macro-bytes")  # a macro part to preserve
    return buf.getvalue()


class PngTests(unittest.TestCase):
    def test_inserts_itxt_chunk_before_iend_and_stays_valid(self):
        png = imgcodec.encode(b"some source image payload bytes here!!")
        out = attach_meta.embed_org_metadata("photo.png", png, META)
        self.assertNotEqual(out, png)
        self.assertTrue(out.startswith(imgcodec._PNG_SIGNATURE))
        types = _png_chunk_types(out)
        self.assertIn(b"iTXt", types)
        self.assertEqual(types[-1], b"IEND")           # IEND stays last
        self.assertIn(attach_meta.META_KEY.encode(), out)
        self.assertIn(CANON.encode("utf-8"), out)      # UTF-8 org name survives


class ZipTests(unittest.TestCase):
    def test_writes_archive_comment_and_keeps_entries(self):
        src = io.BytesIO()
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("drawing.dwg", b"cad-bytes")
        out = attach_meta.embed_org_metadata("Orion.zip", src.getvalue(), META)
        with zipfile.ZipFile(io.BytesIO(out)) as z:
            self.assertEqual(z.comment.decode("utf-8"), CANON)
            self.assertEqual(z.read("drawing.dwg"), b"cad-bytes")


class OoxmlTests(unittest.TestCase):
    def test_adds_custom_props_wired_and_keeps_macros(self):
        out = attach_meta.embed_org_metadata("Invoice.docx", _minimal_docx(), META)
        with zipfile.ZipFile(io.BytesIO(out)) as z:
            names = z.namelist()
            self.assertIn("docProps/custom.xml", names)
            self.assertIn("word/vbaProject.bin", names)  # macro part preserved
            custom = z.read("docProps/custom.xml").decode("utf-8")
            self.assertIn(attach_meta.META_KEY, custom)
            self.assertIn("Acm", custom)
            self.assertIn("/docProps/custom.xml", z.read("[Content_Types].xml").decode("utf-8"))
            self.assertIn("custom-properties", z.read("_rels/.rels").decode("utf-8"))

    def test_existing_custom_props_left_untouched(self):
        src = io.BytesIO()
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("[Content_Types].xml", "<Types></Types>")
            z.writestr("_rels/.rels", "<Relationships></Relationships>")
            z.writestr("docProps/custom.xml", "<Properties/>")
        blob = src.getvalue()
        self.assertEqual(attach_meta.embed_org_metadata("x.xlsx", blob, META), blob)


class PassthroughTests(unittest.TestCase):
    def test_unknown_extension_returns_original(self):
        self.assertEqual(attach_meta.embed_org_metadata("data.bin", b"\x00\x01", META),
                         b"\x00\x01")

    def test_no_extension_returns_original(self):
        self.assertEqual(attach_meta.embed_org_metadata("README", b"abc", META), b"abc")

    def test_malformed_containers_return_original(self):
        self.assertEqual(attach_meta.embed_org_metadata("bad.png", b"not-a-png", META),
                         b"not-a-png")
        self.assertEqual(attach_meta.embed_org_metadata("bad.zip", b"not-a-zip", META),
                         b"not-a-zip")
        self.assertEqual(attach_meta.embed_org_metadata("bad.docx", b"not-a-zip", META),
                         b"not-a-zip")


@unittest.skipUnless(pikepdf is not None, "pikepdf not installed")
class PdfTests(unittest.TestCase):
    def _blank_pdf(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 200))
        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()

    def test_writes_docinfo_key_readable_back(self):
        out = attach_meta.embed_org_metadata("Invoice.pdf", self._blank_pdf(), META)
        with pikepdf.open(io.BytesIO(out)) as pdf:
            value = str(pdf.docinfo[pikepdf.Name("/" + attach_meta.META_KEY)])
        self.assertEqual(json.loads(value), META)

    def test_malformed_pdf_passes_through(self):
        self.assertEqual(attach_meta.embed_org_metadata("bad.pdf", b"%PDF-nope", META),
                         b"%PDF-nope")

    def test_read_round_trip(self):
        out = attach_meta.embed_org_metadata("Invoice.pdf", self._blank_pdf(), META)
        self.assertEqual(attach_meta.read_org_metadata("Invoice.pdf", out), META)


class ReadTests(unittest.TestCase):
    """read_org_metadata / has_org_metadata: the inverse of the writers."""

    def test_png_round_trip(self):
        out = attach_meta.embed_org_metadata("a.png", imgcodec.encode(b"src-img-bytes!!"), META)
        self.assertEqual(attach_meta.read_org_metadata("a.png", out), META)
        self.assertTrue(attach_meta.has_org_metadata("a.png", out))

    def test_zip_round_trip(self):
        src = io.BytesIO()
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("x.dwg", b"cad")
        out = attach_meta.embed_org_metadata("a.zip", src.getvalue(), META)
        self.assertEqual(attach_meta.read_org_metadata("a.zip", out), META)

    def test_ooxml_round_trip(self):
        out = attach_meta.embed_org_metadata("a.docx", _minimal_docx(), META)
        self.assertEqual(attach_meta.read_org_metadata("a.docx", out), META)

    def test_unmarked_file_reads_none(self):
        plain = imgcodec.encode(b"no-marker-in-here")
        self.assertIsNone(attach_meta.read_org_metadata("a.png", plain))
        self.assertFalse(attach_meta.has_org_metadata("a.png", plain))

    def test_unknown_extension_and_malformed_read_none(self):
        self.assertIsNone(attach_meta.read_org_metadata("data.bin", b"\x00\x01"))
        self.assertIsNone(attach_meta.read_org_metadata("bad.zip", b"not-a-zip"))
        self.assertIsNone(attach_meta.read_org_metadata("bad.png", b"not-a-png"))


if __name__ == "__main__":
    unittest.main()
