"""字幕与归档测试。"""

from __future__ import annotations

import gzip
import io
import tempfile
import tarfile
import unittest
import zipfile
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.api import extract, pack
from uniconvert.core.job import Status
from tests._fixtures import make_srt_text


class TestSubtitles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-sub-"))
        self.srt = self.tmp / "a.srt"
        self.srt.write_text(make_srt_text(), encoding="utf-8")

    def test_srt_to_vtt(self):
        dst = self.tmp / "a.vtt"
        r = convert(self.srt, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("WEBVTT"))
        self.assertIn("00:00:01.000 --> 00:00:03.500", text)
        self.assertIn("你好，世界", text)

    def test_srt_to_ass(self):
        dst = self.tmp / "a.ass"
        r = convert(self.srt, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("[Events]", text)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.50,Default", text)

    def test_srt_to_lrc(self):
        dst = self.tmp / "a.lrc"
        r = convert(self.srt, dst)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("[00:01.00]你好，世界", text)

    def test_vtt_roundtrip_via_srt(self):
        vtt = self.tmp / "b.vtt"
        convert(self.srt, vtt)
        back = self.tmp / "b.srt"
        r = convert(vtt, back)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = back.read_text(encoding="utf-8")
        self.assertIn("00:00:01,000 --> 00:00:03,500", text)
        self.assertIn("有第二行", text)

    def test_shift(self):
        dst = self.tmp / "c.srt"
        r = convert(self.srt, dst, shift=1.5)
        self.assertEqual(r.status, Status.DONE, r.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("00:00:02,500 --> 00:00:05,000", text)

    def test_negative_shift_clamps(self):
        dst = self.tmp / "d.srt"
        r = convert(self.srt, dst, shift=-10)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertIn("00:00:00,000", dst.read_text(encoding="utf-8"))

    def test_broken_srt_fails_cleanly(self):
        bad = self.tmp / "bad.srt"
        bad.write_text("this is not a subtitle at all", encoding="utf-8")
        r = convert(bad, self.tmp / "bad.vtt")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("解析", r.error)


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-arc-"))

    def _zip(self, name: str, members: dict) -> Path:
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as zf:
            for k, v in members.items():
                zf.writestr(k, v)
        return p

    def test_zip_to_tar_roundtrip(self):
        src = self._zip("a.zip", {"x.txt": "hello", "dir/y.json": "{}"})
        tarp = self.tmp / "a.tar"
        r = convert(src, tarp)
        self.assertEqual(r.status, Status.DONE, r.error)
        with tarfile.open(tarp) as tf:
            names = tf.getnames()
        self.assertIn("x.txt", names)
        self.assertIn("dir/y.json", names)

        back = self.tmp / "back.zip"
        r2 = convert(tarp, back)
        self.assertEqual(r2.status, Status.DONE, r2.error)
        with zipfile.ZipFile(back) as zf:
            self.assertEqual(zf.read("x.txt"), b"hello")

    def test_gz_compress_decompress(self):
        src = self.tmp / "data.json"
        src.write_text('{"a": 1}', encoding="utf-8")
        gz = self.tmp / "data.json.gz"
        r = convert(src, gz)
        self.assertEqual(r.status, Status.DONE, r.error)
        self.assertEqual(gzip.decompress(gz.read_bytes()), b'{"a": 1}')

        dst = self.tmp / "out.json"
        r2 = convert(gz, dst)
        self.assertEqual(r2.status, Status.DONE, r2.error)
        self.assertEqual(dst.read_text(encoding="utf-8"), '{"a": 1}')

    def test_xz_roundtrip(self):
        src = self.tmp / "x.bin"
        src.write_bytes(bytes(range(256)))
        xz = self.tmp / "x.bin.xz"
        r = convert(src, xz)
        self.assertEqual(r.status, Status.DONE, r.error)
        dst = self.tmp / "x2.bin"
        r2 = convert(xz, dst)
        self.assertEqual(r2.status, Status.DONE, r2.error)
        self.assertEqual(dst.read_bytes(), bytes(range(256)))

    def test_zstd_available(self):
        try:
            import zstandard  # noqa: F401
        except ImportError:
            self.skipTest("zstandard 未安装")
        src = self.tmp / "z.txt"
        src.write_text("压压压", encoding="utf-8")
        zst = self.tmp / "z.txt.zst"
        r = convert(src, zst)
        self.assertEqual(r.status, Status.DONE, r.error)
        dst = self.tmp / "z2.txt"
        r2 = convert(zst, dst)
        self.assertEqual(r2.status, Status.DONE, r2.error)
        self.assertEqual(dst.read_text(encoding="utf-8"), "压压压")

    def test_decompress_validation_catches_mismatch(self):
        # 把 mp4 片段压成 gz，再试图“解压成 gif” → 必须报错而不是产出损坏文件
        mp4_like = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
        src = self.tmp / "fake.gz"
        src.write_bytes(gzip.compress(mp4_like))
        dst = self.tmp / "fake.gif"
        r = convert(src, dst)
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("不是有效的 GIF", r.error)
        self.assertFalse(dst.exists(), "失败时不应留下半成品")

    def test_extract_zip(self):
        src = self._zip("e.zip", {"a.txt": "A", "sub/b.txt": "B"})
        out = self.tmp / "out"
        files = extract(src, out)
        self.assertEqual({f.relative_to(out).as_posix() for f in files},
                         {"a.txt", "sub/b.txt"})
        self.assertEqual((out / "sub" / "b.txt").read_text(), "B")

    def test_extract_zip_slip_blocked(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "boo")
        src = self.tmp / "evil.zip"
        src.write_bytes(buf.getvalue())
        with self.assertRaises(Exception):
            extract(src, self.tmp / "safe")

    def test_pack(self):
        f1 = self.tmp / "p1.txt"
        f2 = self.tmp / "p2.txt"
        f1.write_text("one", encoding="utf-8")
        f2.write_text("two", encoding="utf-8")
        out = pack([f1, f2], self.tmp / "packed.zip")
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(set(zf.namelist()), {"p1.txt", "p2.txt"})

    def test_pack_tar(self):
        f1 = self.tmp / "q1.txt"
        f1.write_text("x", encoding="utf-8")
        out = pack([f1], self.tmp / "packed.tar")
        with tarfile.open(out) as tf:
            self.assertIn("q1.txt", tf.getnames())

    def test_missing_7z_backend_gives_clear_error(self):
        from uniconvert.core.deps import has_dep

        if has_dep("py7zr") or has_dep("7z"):
            self.skipTest("本机有 7z 后端")
        src = self._zip("f.zip", {"x.txt": "x"})
        r = convert(src, self.tmp / "f.7z")
        self.assertEqual(r.status, Status.FAILED)
        self.assertIn("7z", r.error)


if __name__ == "__main__":
    unittest.main()
