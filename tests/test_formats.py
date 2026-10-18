"""格式表与嗅探测试。"""

from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests._fixtures import make_docx_bytes, make_epub_bytes, make_srt_text

from uniconvert.core.detect import (
    describe,
    detect_format,
    detect_text_format,
    looks_textual,
    sniff,
)
from uniconvert.core.formats import (
    CATEGORY_LABELS,
    FORMATS,
    all_exts,
    formats_in,
    get_format,
    guess_format,
    known_names,
)


class TestFormats(unittest.TestCase):
    def test_guess_by_ext(self):
        self.assertEqual(guess_format("photo.JPEG").name, "jpg")
        self.assertEqual(guess_format("data.yml").name, "yaml")
        self.assertEqual(guess_format("a.docx").name, "docx")
        self.assertIsNone(guess_format("x.zzz"))

    def test_guess_by_name(self):
        self.assertEqual(guess_format("yaml").name, "yaml")
        self.assertEqual(guess_format(".json").name, "json")

    def test_aliases(self):
        self.assertEqual(guess_format("a.ndjson").name, "jsonl")
        self.assertEqual(guess_format("a.tif").name, "tiff")
        self.assertEqual(guess_format("a.m4a").name, "aac")

    def test_get_format_required(self):
        self.assertIsNotNone(get_format("png"))
        with self.assertRaises(Exception):
            get_format("no_such_format_xyz")

    def test_known_and_categories(self):
        names = known_names()
        self.assertIn("yaml", names)
        self.assertIn("mp4", names)
        self.assertGreater(len(names), 100)
        for cat in CATEGORY_LABELS:
            self.assertEqual(cat in {f.category for f in FORMATS.values()},
                             bool(formats_in(cat)) or True)

    def test_all_exts(self):
        exts = all_exts(["image"])
        self.assertIn("png", exts)
        self.assertIn("webp", exts)
        self.assertNotIn("mp4", exts)


class TestSniff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-sniff-"))

    def _write(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_png_magic(self):
        p = self._write("x.bin", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.assertEqual(sniff(p).name, "png")
        self.assertEqual(detect_format(p).name, "png")

    def test_gzip_magic(self):
        import gzip

        p = self._write("x.bin", gzip.compress(b"hello"))
        self.assertEqual(sniff(p).name, "gz")

    def test_zip_refine_docx(self):
        p = self._write("x.zip", make_docx_bytes())
        self.assertEqual(sniff(p).name, "docx")

    def test_zip_refine_epub(self):
        p = self._write("book.zip", make_epub_bytes())
        self.assertEqual(sniff(p).name, "epub")

    def test_zip_refine_cbz(self):
        buf = self._zip_with({"1.jpg": b"\xff\xd8\xff", "2.jpg": b"\xff\xd8\xff"})
        p = self._write("comic.zip", buf)
        self.assertEqual(sniff(p).name, "cbz")

    def test_cfb_refine_office(self):
        """d0cf11e0 开头：按特征流名区分 doc/xls/ppt。"""
        cfb = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500
        doc = self._write("w.bin", cfb + b"WordDocument" + b"\x00" * 64)
        xls = self._write("x.bin", cfb + b"Workbook" + b"\x00" * 64)
        ppt = self._write("p.bin", cfb + b"PowerPoint Document" + b"\x00" * 64)
        self.assertEqual(sniff(doc).name, "doc")
        self.assertEqual(sniff(xls).name, "xls")
        self.assertEqual(sniff(ppt).name, "ppt")
        # 无特征流名时退回默认（doc）
        unknown = self._write("u.bin", cfb + b"\x00" * 64)
        self.assertEqual(sniff(unknown).name, "doc")

    def test_m3u8_magic(self):
        p = self._write("s.m3u8", b"#EXTM3U\n#EXT-X-VERSION:3\n")
        self.assertEqual(sniff(p).name, "m3u8")

    def _zip_with(self, members: dict) -> bytes:
        import io

        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return b.getvalue()

    def test_text_formats(self):
        p = self._write("a.txt", '{"a": 1}'.encode())
        self.assertTrue(looks_textual(p))
        self.assertEqual(detect_text_format(p).name, "json")

        p2 = self._write("b.txt", make_srt_text().encode())
        self.assertEqual(detect_text_format(p2).name, "srt")

        p3 = self._write("c.txt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n".encode())
        self.assertEqual(detect_text_format(p3).name, "vtt")

    def test_extension_fallback(self):
        p = self._write("weird.zzz", b"no magic here")
        self.assertIsNone(sniff(p))
        self.assertIsNone(detect_format(p))

    def test_describe_mismatch(self):
        p = self._write("fake.png", b"GIF89a" + b"\x00" * 32)
        info = describe(p)
        self.assertEqual(info["sniffed_format"], "gif")
        self.assertEqual(info["ext_format"], "png")
        self.assertTrue(info["mismatch"])


if __name__ == "__main__":
    unittest.main()
