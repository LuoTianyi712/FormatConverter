"""文档族测试：docx / odt / xlsx / epub / fb2 / html→epub。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.core.job import Status
from tests._fixtures import (
    make_docx_bytes,
    make_epub_bytes,
    make_fb2_bytes,
    make_odt_bytes,
    make_xlsx_bytes,
)


class TestDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-doc-"))

    def _write(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def _conv(self, src: Path, name: str, *, must_ok: bool = True, **opts):
        dst = self.tmp / name
        r = convert(src, dst, **opts)
        if must_ok:
            self.assertEqual(r.status, Status.DONE, r.error)
        return dst, r

    # ---- docx ----
    def test_docx_to_txt(self):
        src = self._write("a.docx", make_docx_bytes())
        dst, r = self._conv(src, "a.txt")
        text = dst.read_text(encoding="utf-8")
        self.assertIn("文档标题", text)
        self.assertIn("第一段 开头", text)
        self.assertIn("A1\tB1", text)

    def test_docx_to_html(self):
        src = self._write("b.docx", make_docx_bytes())
        dst, _ = self._conv(src, "b.html")
        html = dst.read_text(encoding="utf-8")
        self.assertIn("<h1>文档标题</h1>", html)
        self.assertIn("<table", html)
        self.assertIn("<td>A2</td>", html)

    def test_docx_to_md(self):
        src = self._write("c.docx", make_docx_bytes())
        dst, _ = self._conv(src, "c.md")
        md = dst.read_text(encoding="utf-8")
        self.assertIn("# 文档标题", md)

    # ---- odt ----
    def test_odt_to_txt(self):
        src = self._write("d.odt", make_odt_bytes())
        dst, _ = self._conv(src, "d.txt")
        text = dst.read_text(encoding="utf-8")
        self.assertIn("ODT 标题", text)
        self.assertIn("项目一", text)

    def test_odt_to_html(self):
        src = self._write("e.odt", make_odt_bytes())
        dst, _ = self._conv(src, "e.html")
        self.assertIn("<h1>ODT 标题</h1>", dst.read_text(encoding="utf-8"))

    # ---- xlsx ----
    def test_xlsx_to_csv(self):
        src = self._write("f.xlsx", make_xlsx_bytes())
        dst, _ = self._conv(src, "f.csv")
        text = dst.read_text(encoding="utf-8")
        self.assertIn("姓名,年龄", text)
        self.assertIn("张三,30", text)
        self.assertIn("李四,25.5", text)

    def test_xlsx_to_json(self):
        src = self._write("g.xlsx", make_xlsx_bytes())
        dst, _ = self._conv(src, "g.json")
        data = json.loads(dst.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["姓名"], "张三")
        self.assertEqual(data[1]["年龄"], "25.5")

    def test_xlsx_to_html(self):
        src = self._write("h.xlsx", make_xlsx_bytes())
        dst, _ = self._conv(src, "h.html")
        self.assertIn("<table>", dst.read_text(encoding="utf-8"))

    def test_xlsx_bad_sheet_name(self):
        src = self._write("i.xlsx", make_xlsx_bytes())
        _, r = self._conv(src, "i.csv", must_ok=False, sheet="不存在的表")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("找不到工作表", r.error)

    # ---- epub ----
    def test_epub_to_txt(self):
        src = self._write("j.epub", make_epub_bytes())
        dst, _ = self._conv(src, "j.txt")
        text = dst.read_text(encoding="utf-8")
        self.assertIn("测试电子书", text)
        self.assertIn("第一章正文内容", text)
        self.assertIn("第二章正文内容", text)

    def test_epub_to_html_order(self):
        src = self._write("k.epub", make_epub_bytes())
        dst, _ = self._conv(src, "k.html")
        html = dst.read_text(encoding="utf-8")
        self.assertLess(html.index("第一章正文内容"), html.index("第二章正文内容"))

    def test_epub_to_md(self):
        src = self._write("l.epub", make_epub_bytes())
        dst, _ = self._conv(src, "l.md")
        self.assertIn("第一章正文内容", dst.read_text(encoding="utf-8"))

    # ---- fb2 ----
    def test_fb2_to_txt(self):
        src = self._write("m.fb2", make_fb2_bytes())
        dst, _ = self._conv(src, "m.txt")
        text = dst.read_text(encoding="utf-8")
        self.assertIn("书名标题", text)
        self.assertIn("第一段内容", text)

    # ---- html -> epub ----
    def test_html_to_epub(self):
        html_src = self.tmp / "n.html"
        html_src.write_text(
            "<!DOCTYPE html><html><head><title>我的书</title></head>"
            "<body><h1>第一章</h1><p>正文内容</p></body></html>",
            encoding="utf-8",
        )
        dst, _ = self._conv(html_src, "n.epub")
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
            self.assertEqual(names[0], "mimetype")
            info = zf.getinfo("mimetype")
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
            self.assertIn("META-INF/container.xml", names)
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn("我的书", opf)

        # 再读回来验证成环
        back, _ = self._conv(dst, "n-back.txt")
        self.assertIn("正文内容", back.read_text(encoding="utf-8"))

    def test_docx_epub_pipeline(self):
        """docx → epub 零依赖链路（docx→html→epub）。"""
        src = self._write("o.docx", make_docx_bytes())
        dst, r = self._conv(src, "o.epub")
        self.assertEqual(len(r.route.steps), 2)
        with zipfile.ZipFile(dst) as zf:
            self.assertIn("OEBPS/chapter1.xhtml", zf.namelist())


if __name__ == "__main__":
    unittest.main()
