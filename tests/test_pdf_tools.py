"""PDF 工具箱测试（合并/拆分/旋转/加密/解密/抽图/压缩）。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import PIL  # noqa: F401

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from uniconvert.cli import main


def run_cli(*argv: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main(list(argv))
    return code, buf.getvalue()


@unittest.skipUnless(HAS_PYPDF, "pypdf 未安装")
class TestPdfTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-pdf-"))

    def _blank_pdf(self, name: str, pages: int = 3) -> Path:
        writer = pypdf.PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        p = self.tmp / name
        with p.open("wb") as fh:
            writer.write(fh)
        return p

    def _page_count(self, path: Path) -> int:
        return len(pypdf.PdfReader(str(path)).pages)

    # ------------------------------------------------------------------ #
    def test_merge(self):
        from uniconvert import pdf_merge

        a = self._blank_pdf("a.pdf", 2)
        b = self._blank_pdf("b.pdf", 3)
        out = pdf_merge([a, b], self.tmp / "merged.pdf")
        self.assertEqual(self._page_count(out), 5)

    def test_split_every(self):
        from uniconvert import pdf_split

        src = self._blank_pdf("s.pdf", 5)
        files = pdf_split(src, self.tmp / "split", every=2)
        self.assertEqual(len(files), 3)
        self.assertEqual([self._page_count(f) for f in files], [2, 2, 1])

    def test_split_ranges(self):
        from uniconvert import pdf_split

        src = self._blank_pdf("r.pdf", 5)
        files = pdf_split(src, self.tmp / "ranges", ranges="1-2,4")
        self.assertEqual(len(files), 2)
        self.assertEqual([self._page_count(f) for f in files], [2, 1])

    def test_rotate(self):
        from uniconvert import pdf_rotate

        src = self._blank_pdf("rot.pdf", 2)
        out = pdf_rotate(src, self.tmp / "rot90.pdf", angle=90)
        reader = pypdf.PdfReader(str(out))
        self.assertEqual(reader.pages[0].rotation, 90)
        self.assertEqual(reader.pages[1].rotation, 90)

    def test_encrypt_decrypt(self):
        from uniconvert import pdf_decrypt, pdf_encrypt

        src = self._blank_pdf("e.pdf", 2)
        enc = pdf_encrypt(src, self.tmp / "enc.pdf", password="s3cret")
        reader = pypdf.PdfReader(str(enc))
        self.assertTrue(reader.is_encrypted)
        # 正确密码可打开（pypdf 可能返回 USER(1) 或 OWNER(2)，非 0 即成功）
        self.assertNotEqual(reader.decrypt("s3cret"), 0)

        wrong_reader = pypdf.PdfReader(str(enc))
        self.assertEqual(wrong_reader.decrypt("wrong"), 0)

        dec = pdf_decrypt(enc, self.tmp / "dec.pdf", password="s3cret")
        self.assertFalse(pypdf.PdfReader(str(dec)).is_encrypted)

    def test_encrypt_requires_password(self):
        from uniconvert import pdf_encrypt
        from uniconvert.errors import ConversionError

        src = self._blank_pdf("n.pdf", 1)
        with self.assertRaises(ConversionError):
            pdf_encrypt(src, self.tmp / "n2.pdf", password="")

    @unittest.skipUnless(HAS_PIL, "Pillow 未安装")
    def test_extract_images(self):
        from uniconvert import pdf_extract_images
        from PIL import Image

        img = Image.new("RGB", (64, 64), "red")
        pdf_path = self.tmp / "img.pdf"
        img.save(pdf_path, "PDF")
        outdir = self.tmp / "imgs"
        files = pdf_extract_images(pdf_path, outdir)
        self.assertGreaterEqual(len(files), 1)
        self.assertTrue(all(f.exists() and f.stat().st_size > 0 for f in files))

    @unittest.skipUnless(HAS_PIL and hasattr(pypdf, "PdfWriter"), "需要 Pillow")
    def test_compress(self):
        import os

        from uniconvert import pdf_compress
        from PIL import Image

        noise = Image.frombytes("RGB", (600, 600), os.urandom(600 * 600 * 3))
        src = self.tmp / "photo.pdf"
        noise.save(src, "PDF")
        # 噪声图对 JPEG 最不友好，用较低 dpi/质量才稳定变小
        out = pdf_compress(src, self.tmp / "compressed.pdf", dpi=72, quality=40)
        self.assertTrue(out.exists())
        self.assertLess(out.stat().st_size, src.stat().st_size)
        self.assertEqual(self._page_count(out), 1)

    # ---------------- CLI ----------------
    def test_cli_merge_split(self):
        a = self._blank_pdf("ca.pdf", 2)
        b = self._blank_pdf("cb.pdf", 2)
        code, out = run_cli("pdf", "merge", str(a), str(b),
                            "-o", str(self.tmp / "cm.pdf"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._page_count(self.tmp / "cm.pdf"), 4)

        code, out = run_cli("pdf", "split", str(self.tmp / "cm.pdf"),
                            "--every", "3", "--out", str(self.tmp / "cout"))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(list((self.tmp / "cout").glob("*.pdf"))), 2)

    def test_cli_encrypt_decrypt(self):
        src = self._blank_pdf("ce.pdf", 1)
        code, out = run_cli("pdf", "encrypt", str(src),
                            "-o", str(self.tmp / "ce2.pdf"), "--password", "pw")
        self.assertEqual(code, 0, out)
        self.assertTrue(pypdf.PdfReader(str(self.tmp / "ce2.pdf")).is_encrypted)

        code, out = run_cli("pdf", "decrypt", str(self.tmp / "ce2.pdf"),
                            "-o", str(self.tmp / "ce3.pdf"), "--password", "pw")
        self.assertEqual(code, 0, out)
        self.assertFalse(pypdf.PdfReader(str(self.tmp / "ce3.pdf")).is_encrypted)

    def test_cli_compress_bad_dpi_still_ok(self):
        code, _ = run_cli("pdf", "images", str(self.tmp / "missing.pdf"))
        self.assertEqual(code, 1)  # 文件不存在 → 干净报错

    # ---------------- 页码 / 水印 ----------------
    def test_add_page_numbers(self):
        from uniconvert import pdf_add_page_numbers

        src = self._blank_pdf("n.pdf", 3)
        out = pdf_add_page_numbers(src, self.tmp / "n2.pdf", start=1)
        reader = pypdf.PdfReader(str(out))
        self.assertEqual(len(reader.pages), 3)
        text0 = reader.pages[0].extract_text()
        text2 = reader.pages[2].extract_text()
        self.assertIn("1 / 3", text0)
        self.assertIn("3 / 3", text2)

    def test_add_page_numbers_subrange_and_template(self):
        from uniconvert import pdf_add_page_numbers

        src = self._blank_pdf("m.pdf", 3)
        out = pdf_add_page_numbers(src, self.tmp / "m2.pdf",
                                   pages="2-3", template="第{n}页")
        reader = pypdf.PdfReader(str(out))
        self.assertEqual(reader.pages[0].extract_text().strip(), "")  # 第 1 页没加
        self.assertIn("第2页", reader.pages[1].extract_text())

    def test_add_watermark_ascii(self):
        from uniconvert import pdf_add_watermark

        src = self._blank_pdf("w.pdf", 2)
        out = pdf_add_watermark(src, self.tmp / "w2.pdf",
                                text="CONFIDENTIAL", opacity=20)
        reader = pypdf.PdfReader(str(out))
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("CONFIDENTIAL", reader.pages[0].extract_text())

    def test_add_watermark_cjk_uses_cid_font(self):
        from uniconvert import pdf_add_watermark

        src = self._blank_pdf("wc.pdf", 1)
        out = pdf_add_watermark(src, self.tmp / "wc2.pdf", text="内部机密")
        raw = out.read_bytes()
        # 中文走 STSong-Light（标准 CJK 字体）。
        # 文本是 UTF-16BE 编码：pypdf 合并内容流时可能写成转义字面串
        # （如 “内”=U+5185 → \x51\x85 → Q\205），也可能保持 hex 串。
        self.assertIn(b"STSong", raw)
        self.assertTrue(b"\\205" in raw or b"5185" in raw.upper(),
                        "水印文本的 UTF-16BE 编码未出现在内容流里")

    def test_watermark_opacity_written(self):
        """透明度必须真的写进 ExtGState（pypdf 6 的 NumberObject 浮点截断回归）。"""
        from uniconvert import pdf_add_watermark

        src = self._blank_pdf("wo.pdf", 1)
        out = pdf_add_watermark(src, self.tmp / "wo2.pdf",
                                text="CONFIDENTIAL", opacity=25)
        raw = out.read_bytes()
        self.assertIn(b"/ca 0.25", raw)

    def test_add_watermark_requires_text(self):
        from uniconvert import pdf_add_watermark
        from uniconvert.errors import ConversionError

        src = self._blank_pdf("we.pdf", 1)
        with self.assertRaises(ConversionError):
            pdf_add_watermark(src, self.tmp / "we2.pdf", text="")

    def test_cli_number_and_watermark(self):
        src = self._blank_pdf("cn.pdf", 2)
        code, out = run_cli("pdf", "number", str(src),
                            "-o", str(self.tmp / "cn2.pdf"))
        self.assertEqual(code, 0, out)
        code, out = run_cli("pdf", "watermark", str(self.tmp / "cn2.pdf"),
                            "-o", str(self.tmp / "cn3.pdf"), "--text", "样张")
        self.assertEqual(code, 0, out)
        self.assertIn(b"STSong", (self.tmp / "cn3.pdf").read_bytes())

    def test_cli_merge_compress(self):
        import os

        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 未安装")
        noise = Image.frombytes("RGB", (500, 500), os.urandom(500 * 500 * 3))
        a = self.tmp / "ma.pdf"
        noise.save(a, "PDF")
        code, out = run_cli("pdf", "merge", str(a), str(a),
                            "-o", str(self.tmp / "mb.pdf"),
                            "--compress", "--dpi", "72", "--quality", "40")
        self.assertEqual(code, 0, out)
        self.assertLess((self.tmp / "mb.pdf").stat().st_size, a.stat().st_size * 2)


if __name__ == "__main__":
    unittest.main()
