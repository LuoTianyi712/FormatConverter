"""文本世界测试：编码、哈希、Markdown/HTML、RTF、换编码。"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.core.job import Status


class TestCodecs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-text-"))

    def _file(self, name: str, data, binary: bool = False) -> Path:
        p = self.tmp / name
        if binary:
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")
        return p

    def test_base64_roundtrip(self):
        src = self._file("a.txt", "你好 UniConvert")
        r1 = convert(src, self.tmp / "a.base64")
        self.assertEqual(r1.status, Status.DONE, r1.error)
        encoded = (self.tmp / "a.base64").read_text().strip()
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), "你好 UniConvert")
        r2 = convert(self.tmp / "a.base64", self.tmp / "b.txt")
        self.assertEqual(r2.status, Status.DONE, r2.error)
        self.assertEqual((self.tmp / "b.txt").read_text(encoding="utf-8"), "你好 UniConvert")

    def test_hex_roundtrip(self):
        src = self._file("h.bin", bytes(range(32)), binary=True)
        convert(src, self.tmp / "h.hex")
        r = convert(self.tmp / "h.hex", self.tmp / "h2.bin")
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual((self.tmp / "h2.bin").read_bytes(), bytes(range(32)))

    def test_urlencode(self):
        src = self._file("u.txt", "a b&c=中")
        convert(src, self.tmp / "u.urlenc")
        text = (self.tmp / "u.urlenc").read_text().strip()
        self.assertNotIn(" ", text)
        convert(self.tmp / "u.urlenc", self.tmp / "u2.txt")
        self.assertEqual((self.tmp / "u2.txt").read_text(encoding="utf-8"), "a b&c=中")

    def test_rot13(self):
        src = self._file("r.txt", "Hello World")
        convert(src, self.tmp / "r.rot13")
        self.assertEqual((self.tmp / "r.rot13").read_text(), "Uryyb Jbeyq")
        convert(self.tmp / "r.rot13", self.tmp / "r2.txt")
        self.assertEqual((self.tmp / "r2.txt").read_text(encoding="utf-8"), "Hello World")

    def test_quoted_printable(self):
        src = self._file("q.txt", "café 中文")
        convert(src, self.tmp / "q.qp")
        convert(self.tmp / "q.qp", self.tmp / "q2.txt")
        self.assertEqual((self.tmp / "q2.txt").read_text(encoding="utf-8"), "café 中文")

    def test_hash(self):
        src = self._file("d.bin", b"hello uniconvert", binary=True)
        r = convert(src, self.tmp / "d.hash")
        self.assertEqual(r.status, Status.DONE, r.error)
        text = (self.tmp / "d.hash").read_text()
        expect = hashlib.sha256(b"hello uniconvert").hexdigest()
        self.assertIn(expect, text)

    def test_hash_algorithms(self):
        src = self._file("e.bin", b"x", binary=True)
        r = convert(src, self.tmp / "e.hash", algorithm="md5")
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn(hashlib.md5(b"x").hexdigest(), (self.tmp / "e.hash").read_text())

    def test_hash_bad_algorithm(self):
        src = self._file("f.bin", b"x", binary=True)
        r = convert(src, self.tmp / "f.hash", algorithm="superhash9000")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("未知摘要算法", r.error)

    def test_bin_to_txt_rejects_binary(self):
        src = self._file("g.bin", b"\x00\x01\x02\xff\xfe", binary=True)
        r = convert(src, self.tmp / "g.txt")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("不是文本", r.error)

    def test_base64_decode_invalid(self):
        src = self._file("bad.b64", "!!!!not-base64!!!!")
        r = convert(src, self.tmp / "bad.bin")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("Base64 解码失败", r.error)

    def test_base64_data_uri(self):
        """data URI（data:...;base64,）直接解码为文件。"""
        payload = "你好，世界".encode("utf-8")
        uri = "data:text/plain;charset=utf-8;base64," + base64.b64encode(payload).decode()
        src = self._file("uri.txt", uri)
        dst = self.tmp / "uri-decoded.bin"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(dst.read_bytes(), payload)

    def test_transcode_gbk_to_utf8(self):
        src = self.tmp / "cn.txt"
        src.write_bytes("中文测试内容".encode("gb18030"))
        dst = self.tmp / "cn2.txt"
        r = convert(src, dst, from_encoding="gb18030", to_encoding="utf-8")
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(dst.read_text(encoding="utf-8"), "中文测试内容")

    def test_transcode_no_opts_is_copy(self):
        src = self._file("same.txt", "abc")
        dst = self.tmp / "same2.txt"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(dst.read_text(encoding="utf-8"), "abc")


class TestMarkdownHtml(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-md-"))

    def test_md_to_html(self):
        src = self.tmp / "a.md"
        src.write_text(
            "# 标题\n\n这是**粗体**与*斜体*以及 `代码`。\n\n- 项目一\n- 项目二\n\n"
            "```python\nprint('hi')\n```\n\n> 引用行\n",
            encoding="utf-8",
        )
        dst = self.tmp / "a.html"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        html = dst.read_text(encoding="utf-8")
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn("<strong>粗体</strong>", html)
        self.assertIn("<code>代码</code>", html)
        self.assertIn("<li>项目一</li>", html)
        self.assertIn("language-python", html)
        self.assertIn("<blockquote>", html)

    def test_html_to_md(self):
        src = self.tmp / "b.html"
        src.write_text(
            "<html><body><h2>二级</h2><p>段落 <a href='https://x.com'>链接</a>。</p>"
            "<ul><li>一</li><li>二</li></ul></body></html>",
            encoding="utf-8",
        )
        dst = self.tmp / "b.md"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        md = dst.read_text(encoding="utf-8")
        self.assertIn("## 二级", md)
        self.assertIn("[链接](https://x.com)", md)
        self.assertIn("- 一", md)

    def test_html_to_txt(self):
        src = self.tmp / "c.html"
        src.write_text(
            "<html><head><title>t</title><style>p{}</style></head>"
            "<body><p>第一段</p><p>第二段</p><script>alert(1)</script></body></html>",
            encoding="utf-8",
        )
        dst = self.tmp / "c.txt"
        convert(src, dst)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("第一段", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("p{}", text)

    def test_md_to_txt(self):
        src = self.tmp / "d.md"
        src.write_text("# 标题\n\n**粗** 和 [链接](http://x)\n", encoding="utf-8")
        dst = self.tmp / "d.txt"
        convert(src, dst)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("标题", text)
        self.assertNotIn("**", text)
        self.assertNotIn("](http", text)

    def test_rtf_to_txt(self):
        rtf = (
            r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}"
            r"\f0 Hello \b World\b0!\par Second line.\par}"
        )
        src = self.tmp / "e.rtf"
        src.write_bytes(rtf.encode("ascii"))
        dst = self.tmp / "e.txt"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("Hello", text)
        self.assertIn("World!", text)
        self.assertIn("Second line.", text)

    def test_rtf_unicode_escape(self):
        rtf = r"{\rtf1 Unicode: \u20013?\u25991? end\par}"
        src = self.tmp / "f.rtf"
        src.write_bytes(rtf.encode("ascii"))
        dst = self.tmp / "f.txt"
        convert(src, dst)
        self.assertIn("中文", dst.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
