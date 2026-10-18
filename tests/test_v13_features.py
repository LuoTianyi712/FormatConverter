"""v1.3 市场对标新功能测试：漫画 / 邮件 / DOCX 写出 / MOBI /
字幕新格式 / 图片增强 / PDF 组织 / 媒体参数 / TTS / URL。"""

from __future__ import annotations

import io
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.core.job import Options, Status

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


def run_cli(*argv):
    import contextlib

    from uniconvert.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main(list(argv))
    return code, buf.getvalue()


# =========================================================================== #
# 漫画
# =========================================================================== #
@unittest.skipUnless(HAS_PIL, "Pillow 未安装")
class TestComics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-comic-"))

    def _cbz(self, pages: int = 3) -> Path:
        p = self.tmp / "book.cbz"
        with zipfile.ZipFile(p, "w") as zf:
            for i in range(pages):
                buf = io.BytesIO()
                Image.new("RGB", (120, 160), (i * 60, 90, 120)).save(buf, "JPEG")
                zf.writestr(f"page{i + 1:02d}.jpg", buf.getvalue())
            zf.writestr("ComicInfo.xml", "<ComicInfo/>")
            zf.writestr("__MACOSX/junk", b"x")
            zf.writestr(".DS_Store", b"x")
        return p

    def test_cbz_to_pdf(self):
        src = self._cbz(3)
        dst = self.tmp / "book.pdf"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        if HAS_PYPDF:
            reader = pypdf.PdfReader(str(dst))
            self.assertEqual(len(reader.pages), 3)

    def test_cbz_to_pdf_rtl(self):
        src = self._cbz(2)
        dst = self.tmp / "rtl.pdf"
        r = convert(src, dst, rtl=True)
        self.assertEqual(r.status, Status.DONE, r.error)

    @unittest.skipUnless(HAS_PYPDF, "pypdf 未安装")
    def test_pdf_to_cbz(self):
        from uniconvert import pdf_merge
        # 用两张图片合成 PDF 再转回 CBZ
        img1 = self.tmp / "a.png"
        img2 = self.tmp / "b.png"
        Image.new("RGB", (80, 100), "red").save(img1)
        Image.new("RGB", (80, 100), "blue").save(img2)
        pdf_path = self.tmp / "two.pdf"
        im1, im2 = Image.open(img1), Image.open(img2)
        im1.save(pdf_path, "PDF", save_all=True, append_images=[im2])

        out, r = (self.tmp / "back.cbz"), None
        r = convert(pdf_path, out)
        self.assertEqual(r.status, Status.DONE, r.error)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("ComicInfo.xml", names)
            self.assertEqual(len([n for n in names if n.endswith(".jpg")]), 2)

    def test_route_exists(self):
        from uniconvert import route

        for sf in ("cbz", "cbr", "cbt", "cb7"):
            self.assertIsNotNone(route(sf, "pdf"), sf)


# =========================================================================== #
# 邮件
# =========================================================================== #
class TestEmail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-eml-"))

    def _eml(self) -> Path:
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = "张三 <zhangsan@example.com>"
        msg["To"] = "李四 <lisi@example.com>"
        msg["Subject"] = "季度报告"
        msg["Date"] = "Sun, 18 Oct 2026 10:00:00 +0800"
        msg["Message-ID"] = "<abc123@example.com>"
        msg["Received"] = "from mail.example.com by mx.example.com; Sun, 18 Oct 2026 09:59:58 +0800"
        msg.set_content("纯文本兜底内容")
        msg.add_alternative(
            "<html><body><p>这是 <b>HTML</b> 正文。</p>"
            '<p><img src="cid:logo1"></p></body></html>',
            subtype="html",
        )
        html_part = msg.get_payload()[1]
        img = Image.new("RGB", (10, 10), "green")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        html_part.add_related(buf.getvalue(), maintype="image", subtype="png",
                              cid="<logo1>", filename="logo.png")
        msg.add_attachment(b"ATTACH-DATA", maintype="application",
                           subtype="octet-stream", filename="数据.bin")
        p = self.tmp / "mail.eml"
        p.write_bytes(msg.as_bytes())
        return p

    def test_eml_to_html(self):
        src = self._eml()
        dst = self.tmp / "mail.html"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("季度报告", text)
        self.assertIn("data:image/png;base64,", text)  # cid 内联
        self.assertIn("数据.bin", text)               # 附件清单

    def test_eml_to_txt(self):
        src = self._eml()
        dst = self.tmp / "mail.txt"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("HTML", dst.read_text(encoding="utf-8"))

    def test_attachments_option(self):
        src = self._eml()
        att_dir = self.tmp / "atts"
        dst = self.tmp / "mail2.html"
        r = convert(src, dst, attachments=str(att_dir))
        self.assertEqual(r.status, Status.DONE, r.error)
        exported = list(att_dir.glob("*"))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0].read_bytes(), b"ATTACH-DATA")

    def test_extract_attachments_api(self):
        from uniconvert import extract_attachments

        files = extract_attachments(self._eml(), self.tmp / "atts2")
        self.assertEqual(len(files), 1)

    def test_eml_full_headers(self):
        src = self._eml()
        dst = self.tmp / "full.html"
        r = convert(src, dst, full_headers=True)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("Message-ID", text)
        self.assertIn("Received 链", text)


# =========================================================================== #
# DOCX 写出
# =========================================================================== #
class TestDocxWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-docx-"))

    def _doc_xml(self, docx: Path) -> str:
        with zipfile.ZipFile(docx) as zf:
            return zf.read("word/document.xml").decode("utf-8")

    def test_md_to_docx(self):
        src = self.tmp / "a.md"
        src.write_text("# 大标题\n\n正文**加粗**一段。\n\n- 项目一\n- 项目二\n\n"
                       "```python\nprint(1)\n```\n", encoding="utf-8")
        dst = self.tmp / "a.docx"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        xml = self._doc_xml(dst)
        self.assertIn("大标题", xml)
        self.assertIn("Heading1", xml)
        self.assertIn("<w:b/>", xml)
        self.assertIn("项目一", xml)
        with zipfile.ZipFile(dst) as zf:
            self.assertIn("word/styles.xml", zf.namelist())

    def test_txt_to_docx(self):
        src = self.tmp / "b.txt"
        src.write_text("第一段\n\n第二段", encoding="utf-8")
        dst = self.tmp / "b.docx"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("第一段", self._doc_xml(dst))

    def test_srt_to_docx_chain(self):
        src = self.tmp / "c.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
        dst = self.tmp / "c.docx"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(len(r.route.steps), 2)  # srt → txt → docx
        self.assertIn("你好", self._doc_xml(dst))

    def test_html_to_docx(self):
        src = self.tmp / "d.html"
        src.write_text("<h2>二级标题</h2><p>段落</p>", encoding="utf-8")
        dst = self.tmp / "d.docx"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("二级标题", self._doc_xml(dst))


# =========================================================================== #
# MOBI
# =========================================================================== #
def _make_mobi(text: bytes, *, compression: int = 1, title: str = "测试书") -> bytes:
    title_bytes = title.encode("utf-8")
    header = bytearray(200)
    struct.pack_into(">H", header, 0, compression)
    struct.pack_into(">L", header, 4, len(text))
    struct.pack_into(">H", header, 8, 1)          # 1 个文本记录
    header[16:20] = b"MOBI"
    struct.pack_into(">L", header, 28, 65001)     # UTF-8
    struct.pack_into(">II", header, 0x54, 200, len(title_bytes))
    rec0 = bytes(header) + title_bytes

    records = [rec0, text]
    pdb = bytearray(78)
    name = b"TestBook"
    pdb[0:len(name)] = name
    struct.pack_into(">H", pdb, 76, len(records))
    # 注意：偏移表每条目 4 字节（大端 long）
    offsets = []
    pos = 78 + 4 * len(records)
    for rec in records:
        offsets.append(pos)
        pos += len(rec)
    return (bytes(pdb)
            + b"".join(struct.pack(">L", o) for o in offsets)
            + b"".join(records))


class TestMobi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-mobi-"))

    def test_uncompressed_roundtrip(self):
        data = _make_mobi("<html><body><p>Hello MOBI</p></body></html>".encode())
        src = self.tmp / "a.mobi"
        src.write_bytes(data)
        dst = self.tmp / "a.txt"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("Hello MOBI", dst.read_text(encoding="utf-8"))

    def test_palmdoc_decompress(self):
        from uniconvert.converters.document import _palmdoc_decompress

        # 0x05 + 5 字面量；0xC1 → 空格 + 'A'
        packed = bytes([0x05]) + b"hello" + bytes([0xC1])
        self.assertEqual(_palmdoc_decompress(packed), b"hello A")
        # 距离 5、长度 3：pair14 = (5 << 3) | 0 = 0x28（从 "hello" 复制 "hel"）
        out = _palmdoc_decompress(b"hello" + bytes([0x80, 0x28]))
        self.assertEqual(out, b"hellohel")

    def test_compressed_text(self):
        plain = b"<p>AAAAAAAA</p>"
        packed = bytes([8]) + plain          # 简单字面量块
        data = _make_mobi(packed, compression=2)
        src = self.tmp / "c.mobi"
        src.write_bytes(data)
        r = convert(src, self.tmp / "c.md")
        self.assertEqual(r.status, Status.DONE, r.error)

    def test_huffman_rejected(self):
        data = _make_mobi(b"x", compression=0x4448)
        src = self.tmp / "h.mobi"
        src.write_bytes(data)
        r = convert(src, self.tmp / "h.txt")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("Huffman", r.error)


# =========================================================================== #
# 字幕新格式 / 缩放 / HI / 合并
# =========================================================================== #
class TestSubtitleExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-subx-"))
        self.srt = self.tmp / "a.srt"
        self.srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\n你好\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\n[door slams]第二句\n",
            encoding="utf-8",
        )

    def _conv(self, dst_name, **opts):
        dst = self.tmp / dst_name
        r = convert(self.srt, dst, **opts)
        self.assertEqual(r.status, Status.DONE, r.error)
        return dst

    def test_srt_to_microdvd_and_back(self):
        md = self.tmp / "a.sub"
        r = convert(self.srt, md, to="microdvd")
        self.assertEqual(r.status, Status.DONE, r.error)
        text = md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("{1}{1}"))
        back = self.tmp / "back.srt"
        r = convert(md, back)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("00:00:01,000 --> 00:00:03,000",
                      back.read_text(encoding="utf-8"))

    def test_srt_to_ttml(self):
        out = self._conv("a.ttml")
        text = out.read_text(encoding="utf-8")
        self.assertIn("<tt", text)
        self.assertIn("你好", text)

    def test_srt_to_sbv(self):
        out = self._conv("a.sbv")
        self.assertIn("00:00:01.000,00:00:03.000", out.read_text(encoding="utf-8"))

    def test_srt_to_smi(self):
        out = self._conv("a.smi")
        text = out.read_text(encoding="utf-8")
        self.assertIn("<SAMI>", text)
        self.assertIn("你好", text)

    def test_scale(self):
        out = self._conv("scaled.srt", scale=2.0)
        self.assertIn("00:00:02,000 --> 00:00:06,000",
                      out.read_text(encoding="utf-8"))

    def test_remove_hi(self):
        out = self._conv("clean.srt", remove_hi=True)
        text = out.read_text(encoding="utf-8")
        self.assertNotIn("door slams", text)
        self.assertIn("第二句", text)

    def test_merge_bilingual(self):
        from uniconvert import merge_subtitles

        other = self.tmp / "en.srt"
        other.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nHello\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nSecond\n",
            encoding="utf-8",
        )
        out = merge_subtitles(self.srt, other, self.tmp / "bi.srt")
        text = out.read_text(encoding="utf-8")
        self.assertIn("你好\nHello", text)
        self.assertIn("第二句\nSecond", text)

    def test_merge_concat(self):
        from uniconvert import merge_subtitles

        other = self.tmp / "c.srt"
        other.write_text("1\n00:00:01,000 --> 00:00:02,000\n后段\n", encoding="utf-8")
        out = merge_subtitles(self.srt, other, self.tmp / "merged.srt",
                              mode="concat")
        text = out.read_text(encoding="utf-8")
        # 第二文件整体后移到首文件结尾之后
        self.assertIn("后段", text)
        self.assertRegex(text, r"00:00:0[7-9],\d{3} --> ")

    def test_cli_merge(self):
        other = self.tmp / "en2.srt"
        other.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")
        code, out = run_cli("subtitle", "merge", str(self.srt), str(other),
                            "-o", str(self.tmp / "cli.srt"))
        self.assertEqual(code, 0, out)


# =========================================================================== #
# 图片增强 / favicon / 矢量化
# =========================================================================== #
@unittest.skipUnless(HAS_PIL, "Pillow 未安装")
class TestImageExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-imgx-"))
        self.photo = self.tmp / "photo.jpg"
        im = Image.new("RGB", (200, 120), (100, 100, 100))
        # 白色方块靠左（非对称），用于验证水平翻转
        for x in range(60):
            for y in range(30, 90):
                im.putpixel((10 + x, y), (255, 255, 255))
        im.save(self.photo, quality=95)

    def test_flip_horizontal(self):
        dst = self.tmp / "flip.jpg"
        r = convert(self.photo, dst, flip="h")
        self.assertEqual(r.status, Status.DONE, r.error)
        a = Image.open(self.photo).convert("L")
        b = Image.open(dst).convert("L")
        self.assertGreater(a.getpixel((20, 60)), b.getpixel((20, 60)))

    def test_dpi_option(self):
        dst = self.tmp / "dpi.png"
        r = convert(self.photo, dst, dpi="300")
        self.assertEqual(r.status, Status.DONE, r.error)
        info = Image.open(dst).info
        self.assertIn("dpi", info)

    def test_brightness(self):
        dst = self.tmp / "bright.jpg"
        r = convert(self.photo, dst, brightness=1.5)
        self.assertEqual(r.status, Status.DONE, r.error)
        before = sum(Image.open(self.photo).convert("L").getdata()) / (200 * 120)
        after = sum(Image.open(dst).convert("L").getdata()) / (200 * 120)
        self.assertGreater(after, before)

    def test_favicon_set(self):
        from uniconvert import image_favicon

        src = self.tmp / "logo.png"
        Image.new("RGBA", (512, 512), (30, 120, 200, 255)).save(src)
        outdir = self.tmp / "site"
        files = image_favicon(src, outdir, Options({"sizes": "16,32,48"}))
        names = {f.name for f in files}
        self.assertIn("favicon.ico", names)
        self.assertIn("favicon-16x16.png", names)
        self.assertIn("apple-touch-icon.png", names)
        self.assertIn("site.webmanifest", names)
        self.assertIn("favicon-snippet.html", names)
        manifest = json.loads((outdir / "site.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["icons"]), 2)

    def test_svg_trace_fallback_embed(self):
        """没装 potrace 时，图片→SVG 走内嵌兜底；trace=force 则明确报依赖。"""
        from uniconvert.core.deps import has_dep
        from uniconvert.errors import DependencyMissingError

        dst = self.tmp / "out.svg"
        r = convert(self.photo, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        if has_dep("potrace"):
            self.assertIn("<svg", text)  # 真矢量化输出
        else:
            self.assertIn("base64", text)  # 内嵌兜底
            with self.assertRaises(DependencyMissingError):
                from uniconvert.converters.image import raster_to_svg

                raster_to_svg(self.photo, self.tmp / "x.svg",
                              Options({"trace": "force"}))


# =========================================================================== #
# PDF 组织 / 元数据 / 长图 / 目标压缩 / 裁剪
# =========================================================================== #
@unittest.skipUnless(HAS_PYPDF, "pypdf 未安装")
class TestPdfExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-pdfx-"))

    def _pdf(self, name: str, pages: int = 4, with_text_on: int = 0) -> Path:
        writer = pypdf.PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=300, height=200)
        p = self.tmp / name
        with p.open("wb") as fh:
            writer.write(fh)
        if with_text_on:
            from uniconvert import pdf_add_page_numbers

            out = self.tmp / f"{name}-text.pdf"
            pdf_add_page_numbers(p, out, template="X", size=20)
            p = out
        return p

    def _count(self, path: Path) -> int:
        return len(pypdf.PdfReader(str(path)).pages)

    def test_pages_order(self):
        from uniconvert import pdf_pages_organize

        src = self._pdf("a.pdf", 4)
        out = pdf_pages_organize(src, self.tmp / "a2.pdf", order="4,2,1")
        self.assertEqual(self._count(out), 3)

    def test_pages_reverse_and_odd_even(self):
        from uniconvert import pdf_pages_organize

        src = self._pdf("b.pdf", 5)
        out = pdf_pages_organize(src, self.tmp / "b2.pdf", odd_even="odd")
        self.assertEqual(self._count(out), 3)
        out2 = pdf_pages_organize(src, self.tmp / "b3.pdf", delete="1-3")
        self.assertEqual(self._count(out2), 2)

    def test_remove_blank(self):
        from uniconvert import pdf_remove_blank

        # 仅第 1 页有文字，其余两页空白
        src = self._pdf("c.pdf", 3)
        from uniconvert import pdf_add_page_numbers

        with_text = self.tmp / "c-text.pdf"
        pdf_add_page_numbers(src, with_text, pages="1", template="X", size=20)
        out = pdf_remove_blank(with_text, self.tmp / "c2.pdf")
        self.assertEqual(self._count(out), 1)
        self.assertLess(self._count(out), self._count(with_text))

    def test_add_cover_text(self):
        from uniconvert import pdf_add_cover

        src = self._pdf("d.pdf", 2)
        out = pdf_add_cover(src, self.tmp / "d2.pdf", cover_text="封面标题")
        self.assertEqual(self._count(out), 3)

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_add_cover_image(self):
        from uniconvert import pdf_add_cover

        img = self.tmp / "cover.png"
        Image.new("RGB", (200, 300), "navy").save(img)
        src = self._pdf("e.pdf", 1)
        out = pdf_add_cover(src, self.tmp / "e2.pdf", cover_image=str(img))
        self.assertEqual(self._count(out), 2)

    def test_metadata_set_get_clean(self):
        from uniconvert import pdf_metadata

        src = self._pdf("f.pdf", 1)
        out = pdf_metadata(src, self.tmp / "f2.pdf",
                           set_values={"Title": "年度报告", "Author": "张三"})
        info = pdf_metadata(out)
        self.assertEqual(info.get("Title"), "年度报告")
        cleaned = pdf_metadata(out, self.tmp / "f3.pdf", clean=True)
        self.assertFalse((pdf_metadata(cleaned) or {}).get("Title"))

    def test_crop(self):
        from uniconvert import pdf_crop

        src = self._pdf("g.pdf", 1)
        out = pdf_crop(src, self.tmp / "g2.pdf", margins="10")
        page = pypdf.PdfReader(str(out)).pages[0]
        box = page.cropbox
        self.assertLess(float(box.width), 300)
        self.assertLess(float(box.height), 200)

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_longpic(self):
        from uniconvert import pdf_longpic

        src = self._pdf("h.pdf", 2)
        out = pdf_longpic(src, self.tmp / "h.jpg", dpi=60)
        im = Image.open(out)
        self.assertGreater(im.height, im.width)

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_compress_target(self):
        import os

        from uniconvert import pdf_compress

        noise = Image.frombytes("RGB", (500, 500), os.urandom(500 * 500 * 3))
        src = self.tmp / "noise.pdf"
        noise.save(src, "PDF")
        out = pdf_compress(src, self.tmp / "small.pdf", dpi=72,
                           quality=40, target="200KB")
        self.assertLessEqual(out.stat().st_size, 200 * 1024)

    def test_cli_pages(self):
        src = self._pdf("cli.pdf", 4)
        code, out = run_cli("pdf", "pages", str(src),
                            "-o", str(self.tmp / "cli2.pdf"), "--reverse")
        self.assertEqual(code, 0, out)


# =========================================================================== #
# 媒体参数（不需要 ffmpeg）
# =========================================================================== #
class TestMediaExtra(unittest.TestCase):
    def _args(self, opts, dst_fmt="mp4", src="a.mp4"):
        from uniconvert.converters.media import build_media_args

        return build_media_args(Path(src), Path(f"out.{dst_fmt}"),
                                Options(opts), dst_fmt)

    def test_karaoke_pan(self):
        joined = " ".join(self._args({"karaoke": True}))
        self.assertIn("pan=stereo|c0=c0-c1|c1=c1-c0", joined)

    def test_loudnorm(self):
        joined = " ".join(self._args({"loudnorm": True}))
        self.assertIn("loudnorm=I=-16", joined)

    def test_pitch(self):
        joined = " ".join(self._args({"pitch": 2}))
        self.assertIn("asetrate=", joined)

    def test_channels_mono_and_side(self):
        joined = " ".join(self._args({"channels": "mono"}))
        self.assertIn("pan=mono", joined)
        joined = " ".join(self._args({"channels": "left"}))
        self.assertIn("pan=mono|c0=c0", joined)

    def test_reverse_denoise_bass(self):
        joined = " ".join(self._args({"reverse": True, "denoise": True,
                                      "bass": 6, "treble": 3}))
        self.assertIn("areverse", joined)
        self.assertIn("afftdn", joined)
        self.assertIn("bass=g=6", joined)
        self.assertIn("treble=g=3", joined)

    def test_rewrap_copy(self):
        args = self._args({"rewrap": True}, "mkv")
        self.assertIn("copy", args)

    def test_mux_sub(self):
        sub = Path(tempfile.mkdtemp(prefix="uc-mux-")) / "s.srt"
        sub.write_text("1\n", encoding="utf-8")
        args = self._args({"mux_sub": str(sub)}, "mkv")
        self.assertIn("-c:s", args)
        self.assertIn("srt", args)
        args_mp4 = self._args({"mux_sub": str(sub)}, "mp4")
        self.assertIn("mov_text", args_mp4)

    def test_mux_sub_wrong_container(self):
        from uniconvert.errors import ConversionError

        sub = Path(tempfile.mkdtemp(prefix="uc-mux2-")) / "s.srt"
        sub.write_text("1\n", encoding="utf-8")
        with self.assertRaises(ConversionError):
            self._args({"mux_sub": str(sub)}, "webm")

    def test_replace_audio_mapping(self):
        audio = Path(tempfile.mkdtemp(prefix="uc-ra-")) / "a.mp3"
        audio.write_bytes(b"ID3" + b"\x00" * 32)
        args = self._args({"replace_audio": str(audio)}, "mp4")
        self.assertIn("0:v:0", args)
        self.assertIn("1:a:0", args)

    def test_m4r_container(self):
        args = self._args({"duration": 30}, "m4r", src="song.mp3")
        self.assertIn("ipod", args)

    def test_crop_mirror(self):
        joined = " ".join(self._args({"crop": "1280:720:0:0", "mirror": "h"}))
        self.assertIn("crop=1280:720:0:0", joined)
        self.assertIn("hflip", joined)

    def test_new_format_routes(self):
        from uniconvert import route
        from uniconvert.core.deps import has_dep

        r = route("flac", "wv")
        if has_dep("ffmpeg"):
            self.assertIsNotNone(r)
        # 只读格式不作为转码目标：最多允许 gz 压缩流的伪路径
        r2 = route("mp3", "rm")
        if r2 is not None:
            self.assertIn("gz", r2.nodes)


# =========================================================================== #
# TTS / URL / 字体子集命令冒烟
# =========================================================================== #
class TestTtsAndUrl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-tts-"))

    @unittest.skipUnless(__import__("sys").platform == "win32", "仅 Windows")
    def test_tts_wav(self):
        from uniconvert import tts

        out = tts("测试语音合成", self.tmp / "speech.wav")
        self.assertTrue(Path(out).exists())
        self.assertGreater(Path(out).stat().st_size, 1000)

    def test_url_download_and_convert(self):
        import http.server
        import socketserver

        serve_dir = Path(tempfile.mkdtemp(prefix="uc-url-srv-"))
        (serve_dir / "data.json").write_text('{"city": "北京"}', encoding="utf-8")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(serve_dir), **kwargs)

            def log_message(self, *args):  # 静默
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                from uniconvert import convert, download

                url = f"http://127.0.0.1:{port}/data.json"
                local = download(url)
                self.assertTrue(local.exists())
                self.assertIn("北京", local.read_text(encoding="utf-8"))

                dst = self.tmp / "data.yaml"
                r = convert(url, dst)
                self.assertEqual(r.status, Status.DONE, r.error)
                self.assertIn("北京", dst.read_text(encoding="utf-8"))
            finally:
                httpd.shutdown()

    def test_font_subset_command_without_fonttools(self):
        from uniconvert.core.deps import has_dep

        if has_dep("fontTools"):
            self.skipTest("本机装了 fontTools")
        code, out = run_cli("font-subset", str(self.tmp / "x.ttf"),
                            "-o", str(self.tmp / "y.woff2"))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
