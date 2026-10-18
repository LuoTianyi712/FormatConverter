"""图片工具测试（水印/拼接/压到指定大小/自环重压缩。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image, ImageDraw

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.core.job import Options, Status


@unittest.skipUnless(HAS_PIL, "Pillow 未安装")
class TestImageTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-img-"))

    def _photo(self, name: str, size=(400, 300), color=(120, 180, 90)) -> Path:
        img = Image.new("RGB", size, color)
        d = ImageDraw.Draw(img)
        margin = max(2, min(size) // 10)
        d.rectangle([margin, margin, size[0] - margin, size[1] - margin],
                    outline=(255, 255, 255), width=max(1, margin // 4))
        p = self.tmp / name
        img.save(p, quality=95)
        return p

    # ---------------- 水印 ----------------
    def test_watermark_text(self):
        from uniconvert import image_watermark

        src = self._photo("wm.jpg")
        out = image_watermark(src, self.tmp / "wm-out.jpg", Options({"text": "测试水印", "opacity": 50}))
        self.assertTrue(out.exists())
        im = Image.open(out)
        self.assertEqual(im.size, (400, 300))

    def test_watermark_tile_and_rotate(self):
        from uniconvert import image_watermark

        src = self._photo("tile.jpg")
        out = image_watermark(src, self.tmp / "tile-out.jpg",
                              Options({"text": "样本", "tile": True, "rotate": -30, "opacity": 30}))
        self.assertTrue(out.exists())

    def test_watermark_image(self):
        from uniconvert import image_watermark

        logo = Image.new("RGBA", (60, 60), (255, 0, 0, 200))
        logo_path = self.tmp / "logo.png"
        logo.save(logo_path)
        src = self._photo("with-logo.jpg")
        out = image_watermark(src, self.tmp / "logo-out.png",
                              Options({"image": str(logo_path), "scale": 25}))
        self.assertTrue(out.exists())

    def test_watermark_needs_text_or_image(self):
        from uniconvert import image_watermark
        from uniconvert.errors import ConversionError

        src = self._photo("none.jpg")
        with self.assertRaises(ConversionError):
            image_watermark(src, self.tmp / "none-out.jpg", Options({}))

    # ---------------- 拼接 ----------------
    def test_collage_grid(self):
        from uniconvert import image_collage

        files = [self._photo(f"c{i}.jpg", (200, 150)) for i in range(4)]
        out = image_collage(files, self.tmp / "grid.jpg", Options({"mode": "grid", "cols": 2}))
        im = Image.open(out)
        # cell=200x150、gap=8、2 列 2 行 → 424 x 324
        self.assertEqual(im.width, 2 * 200 + 3 * 8)
        self.assertEqual(im.height, 2 * 150 + 3 * 8)

    def test_collage_vertical_strip(self):
        from uniconvert import image_collage

        files = [self._photo(f"v{i}.jpg", (100, 80)) for i in range(3)]
        out = image_collage(files, self.tmp / "strip.jpg", Options({"mode": "v", "gap": 0}))
        im = Image.open(out)
        self.assertEqual(im.width, 100)   # 竖长图宽度 = 单图宽
        self.assertEqual(im.height, 240)  # 3 × 80，无间距
        im.close()

    # ---------------- 压到指定大小 ----------------
    def test_compress_to_size(self):
        from uniconvert import image_compress_to_size

        noise = Image.frombytes("RGB", (900, 700), os.urandom(900 * 700 * 3))
        src = self.tmp / "big.jpg"
        noise.save(src, quality=98)
        target = 120 * 1024
        out = src.with_name("small.jpg")
        image_compress_to_size(src, out, Options({"target": "120KB"}))
        self.assertLessEqual(out.stat().st_size, target)
        # 仍是一张可打开的图
        self.assertGreaterEqual(Image.open(out).width, 16)

    def test_compress_to_size_unreachable_message(self):
        from uniconvert import image_compress_to_size
        from uniconvert.errors import ConversionError

        noise = Image.frombytes("RGB", (900, 700), os.urandom(900 * 700 * 3))
        src = self.tmp / "big2.jpg"
        noise.save(src, quality=98)
        with self.assertRaises(ConversionError):
            image_compress_to_size(src, self.tmp / "tiny.jpg",
                                   Options({"target": "1KB", "min_quality": 60}))

    def test_compress_to_size_png_rejected(self):
        from uniconvert import image_compress_to_size
        from uniconvert.errors import ConversionError

        src = self._photo("p.png")
        with self.assertRaises(ConversionError):
            image_compress_to_size(src, self.tmp / "p2.png", Options({"target": "10KB"}))

    def test_parse_size(self):
        from uniconvert import parse_size_bytes

        self.assertEqual(parse_size_bytes("500KB"), 500 * 1024)
        self.assertEqual(parse_size_bytes("1.5MB"), int(1.5 * 1024 * 1024))
        self.assertEqual(parse_size_bytes("800"), 800)

    # ---------------- 自环重压缩 ----------------
    def test_jpg_recompress_self_edge(self):
        noise = Image.frombytes("RGB", (600, 450), os.urandom(600 * 450 * 3))
        src = self.tmp / "orig.jpg"
        noise.save(src, quality=95)
        dst = self.tmp / "recompressed.jpg"
        result = convert(src, dst, quality=40)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(len(result.route), 1)
        self.assertLess(dst.stat().st_size, src.stat().st_size)

    def test_gif_speed(self):
        frames = [Image.new("RGB", (40, 40), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        src = self.tmp / "anim.gif"
        frames[0].save(src, save_all=True, append_images=frames[1:], duration=200, loop=0)
        dst = self.tmp / "fast.gif"
        result = convert(src, dst, speed=2.0)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(Image.open(dst).info.get("duration"), 100)


if __name__ == "__main__":
    unittest.main()
