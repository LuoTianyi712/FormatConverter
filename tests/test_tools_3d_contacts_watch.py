"""3D 模型互转 / 通讯录转换 / watch 监控 / 媒体参数构造 测试。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.cli import main
from uniconvert.core.job import Options, Status
from uniconvert.core.registry import REGISTRY
from uniconvert.converters.three_d import (
    read_obj,
    read_ply,
    read_stl,
    write_obj,
    write_ply,
    write_stl,
)
from uniconvert.watch import Watcher


def run_cli(*argv: str):
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main(list(argv))
    return code, buf.getvalue()


# =========================================================================== #
# 3D
# =========================================================================== #
def _cube_tris():
    # 8 个顶点组成立方体 12 个三角面
    v = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
         (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    faces = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
             (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
             (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    return [(v[a], v[b], v[c]) for a, b, c in faces]


class TestThreeD(unittest.TestCase):
    def _make(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-3d-"))

    def test_stl_binary_roundtrip(self):
        tris = _cube_tris()
        data = write_stl(tris)
        self.assertEqual(len(read_stl(data)), 12)

    def test_stl_ascii_roundtrip(self):
        tris = _cube_tris()
        data = write_stl(tris, ascii_mode=True)
        self.assertTrue(data.startswith(b"solid"))
        back = read_stl(data)
        self.assertEqual(len(back), 12)
        self.assertAlmostEqual(back[0][0][0], 0.0)

    def test_obj_roundtrip(self):
        tris = _cube_tris()
        data = write_obj(tris)
        back = read_obj(data)
        self.assertEqual(len(back), 12)

    def test_ply_roundtrip(self):
        tris = _cube_tris()
        data = write_ply(tris)
        back = read_ply(data)
        self.assertEqual(len(back), 12)

    def test_engine_convert_stl_to_obj(self):
        src = self._make("cube.stl", write_stl(_cube_tris()))
        dst = self.tmp / "cube.obj"
        result = convert(src, dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("v 0 0 0", text)
        self.assertIn("f ", text)

    def test_route_3d(self):
        from uniconvert import route

        r = route("stl", "ply")
        self.assertIsNotNone(r)
        self.assertEqual(len(r), 1)

    def test_obj_quad_triangulated(self):
        obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n"
        tris = read_obj(obj)
        self.assertEqual(len(tris), 2)  # 四边形 → 两个三角面

    def test_garbage_fails_cleanly(self):
        p = self._make("bad.stl", b"this is not a mesh" * 10)
        result = convert(p, self.tmp / "bad.obj")
        self.assertEqual(result.status, Status.FAILED)


# =========================================================================== #
# 通讯录
# =========================================================================== #
VCF_SAMPLE = """BEGIN:VCARD
VERSION:3.0
FN:张三
NICKNAME:老三
TEL;TYPE=CELL:13800138000
TEL;TYPE=HOME:010-12345678
EMAIL:zhangsan@example.com
ORG:示例科技
TITLE:工程师
ADR:;;北京市朝阳区1号;;;;
NOTE:测试联系人\\n第二行
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:李四
TEL;TYPE=CELL:13900139000
END:VCARD
"""


class TestContacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-vcf-"))
        self.vcf = self.tmp / "contacts.vcf"
        self.vcf.write_text(VCF_SAMPLE, encoding="utf-8")

    def test_vcf_to_json(self):
        import json

        dst = self.tmp / "contacts.json"
        result = convert(self.vcf, dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        data = json.loads(dst.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["name"], "张三")
        self.assertEqual(data[0]["phone"][0], "13800138000")
        self.assertEqual(data[0]["email"], ["zhangsan@example.com"])
        self.assertEqual(data[1]["name"], "李四")

    def test_vcf_to_csv(self):
        dst = self.tmp / "contacts.csv"
        result = convert(self.vcf, dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("name,", text)
        self.assertIn("张三", text)
        self.assertIn("13800138000", text)

    def test_json_roundtrip_back_to_vcf(self):
        import json

        js = self.tmp / "c.json"
        convert(self.vcf, js)
        back = self.tmp / "back.vcf"
        result = convert(js, back)
        self.assertEqual(result.status, Status.DONE, result.error)
        text = back.read_text(encoding="utf-8")
        self.assertIn("FN:张三", text)
        self.assertIn("TEL;TYPE=CELL:13800138000", text)
        # 再转回 json 校验往返数据一致
        js2 = self.tmp / "c2.json"
        convert(back, js2)
        data = json.loads(js2.read_text(encoding="utf-8"))
        self.assertEqual([c["name"] for c in data], ["张三", "李四"])

    def test_csv_to_vcf_chinese_headers(self):
        csv_text = "姓名,手机,昵称\n王五,13700137000,小五\n"
        src = self.tmp / "in.csv"
        src.write_text(csv_text, encoding="utf-8")
        dst = self.tmp / "out.vcf"
        result = convert(src, dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("FN:王五", text)
        self.assertIn("13700137000", text)
        self.assertIn("NICKNAME:小五", text)

    def test_bad_vcf_fails(self):
        bad = self.tmp / "bad.vcf"
        bad.write_text("这不是通讯录", encoding="utf-8")
        result = convert(bad, self.tmp / "bad.json")
        self.assertEqual(result.status, Status.FAILED)


# =========================================================================== #
# watch 目录监控
# =========================================================================== #
class TestWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-watch-"))
        self.src = self.tmp / "src"
        self.out = self.tmp / "out"
        self.src.mkdir()
        self.out.mkdir()

    def _watcher(self, **kw):
        return Watcher(self.src, target="yaml", outdir=self.out,
                       engine_kwargs={"workers": 1, "on_exists": "overwrite"}, **kw)

    def _write_json(self, name: str, content: str = '{"a": 1}') -> Path:
        p = self.src / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_initial_scan_skips_existing(self):
        self._write_json("old.json")
        w = self._watcher()
        self.assertEqual(w.scan_once(), [])
        self.assertFalse((self.out / "old.yaml").exists())

    def test_new_file_converted(self):
        w = self._watcher()
        w.scan_once()  # 首扫（目录为空也要正确置位）
        self._write_json("new.json")
        results = w.scan_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, Status.DONE, results[0].error)
        self.assertTrue((self.out / "new.yaml").exists())

    def test_modified_file_reconverted(self):
        w = self._watcher()
        p = self._write_json("edit.json", '{"v": 1}')
        w.scan_once()
        results = w.scan_once()  # 首扫登记 + 转换（这里 initial 默认 False → 先登记）
        # 第一次 scan 只登记；再来一次同样结果为空
        self.assertEqual(results, [])
        # 修改文件
        import time

        time.sleep(0.01)
        p.write_text('{"v": 2}', encoding="utf-8")
        results = w.scan_once()
        self.assertEqual(len(results), 1)
        self.assertIn("v: 2", (self.out / "edit.yaml").read_text(encoding="utf-8"))

    def test_initial_true_converts_existing(self):
        self._write_json("exist.json")
        w = self._watcher(initial=True)
        results = w.scan_once()
        self.assertEqual(len(results), 1)
        self.assertTrue((self.out / "exist.yaml").exists())

    def test_only_filter_and_temp_ignored(self):
        w = self._watcher(initial=True, only=("yaml",))
        self._write_json("a.json")
        (self.src / "junk.tmp").write_text("x", encoding="utf-8")
        (self.src / "note.txt").write_text("x", encoding="utf-8")
        results = w.scan_once()
        # only=("yaml",)：junk.tmp 与 note.txt 都不处理；a.json 不是 yaml 也不处理
        self.assertEqual(len(results), 0)

    def test_output_loop_protection(self):
        # outdir 在 srcdir 里面时，转换产物不能被再次转换
        w = Watcher(self.src, target="yaml", outdir=self.src / "converted",
                    initial=True, engine_kwargs={"workers": 1})
        self._write_json("loop.json")
        first = w.scan_once()
        self.assertEqual(len(first), 1)
        second = w.scan_once()
        self.assertEqual(second, [])  # 产物不再触发

    def test_cli_watch_once(self):
        self._write_json("cli.json")
        code, out = run_cli("watch", str(self.src), "-t", "yaml",
                            "--out", str(self.out), "--once", "--initial")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.out / "cli.yaml").exists())

    def test_cli_watch_requires_target(self):
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with self.assertRaises(SystemExit):
                main(["watch", str(self.src)])  # 缺 -t → argparse 报错


# =========================================================================== #
# 媒体参数构造（不需要 ffmpeg 也能验证翻译逻辑）
# =========================================================================== #
class TestMediaArgs(unittest.TestCase):
    def _args(self, opts: dict, dst_fmt: str, src_name: str = "a.mp4"):
        from uniconvert.converters.media import build_media_args

        return build_media_args(Path(src_name), Path(f"out.{dst_fmt}"),
                                Options(opts), dst_fmt)

    def test_basic_video(self):
        args = self._args({}, "mkv")
        self.assertIn("-c:v", args)
        self.assertIn("libx264", args)
        self.assertIn("-progress", args)

    def test_audio_target_adds_vn(self):
        args = self._args({}, "mp3")
        self.assertIn("-vn", args)
        self.assertIn("libmp3lame", args)

    def test_gif_palette(self):
        args = self._args({}, "gif")
        joined = " ".join(args)
        self.assertIn("palettegen", joined)

    def test_profile_wechat(self):
        args = self._args({"profile": "wechat"}, "mp4")
        self.assertIn("1280x720", " ".join(args))
        self.assertIn("28", args)  # crf
        self.assertIn("+faststart", args)

    def test_profile_douyin_pad(self):
        args = self._args({"profile": "douyin"}, "mp4")
        joined = " ".join(args)
        self.assertIn("pad=1080:1920", joined)

    def test_profile_unknown(self):
        from uniconvert.errors import ConversionError

        with self.assertRaises(ConversionError):
            self._args({"profile": "nope"}, "mp4")

    def test_speed_filters(self):
        args = self._args({"speed": 1.5}, "mp4")
        joined = " ".join(args)
        self.assertIn("setpts=PTS/1.5", joined)
        self.assertIn("atempo", joined)

    def test_speed_beyond_tempo_range(self):
        args = self._args({"speed": 4.0}, "mp4")
        joined = " ".join(args)
        self.assertIn("atempo=2.0", joined)

    def test_volume_and_fade(self):
        args = self._args({"volume": -6, "fade_in": 1, "end": "30"}, "mp4")
        joined = " ".join(args)
        self.assertIn("volume=-6dB", joined)
        self.assertIn("fade=t=in", joined)

    def test_m3u8_hls(self):
        args = self._args({"hls_time": 4}, "m3u8")
        joined = " ".join(args)
        self.assertIn("-f hls", joined)
        self.assertIn("_%03d.ts", joined)

    def test_image_loop(self):
        args = self._args({"duration": 3}, "mp4", src_name="pic.png")
        joined = " ".join(args)
        self.assertIn("-loop 1", joined)
        self.assertIn("-t 3", joined)

    def test_subtitle_burn(self):
        args = self._args({"sub": "C:/tmp/字幕.srt"}, "mp4")
        self.assertIn("subtitles=", " ".join(args))

    # ---------------- 视频水印 ----------------
    def test_text_watermark_filter(self):
        args = self._args({"wm_text": "样张 Demo", "wm_pos": "br",
                           "wm_size": 30, "wm_opacity": 50}, "mp4")
        joined = " ".join(args)
        self.assertIn("drawtext", joined)
        self.assertIn("fontsize=30", joined)
        self.assertIn("@0.50", joined)

    def test_text_watermark_needs_cjk_font(self):
        """找不到中文字体且文字是中文 → 明确报错（本机 Windows 有字体，跳过）。"""
        from uniconvert.converters import media as media_mod

        with mock.patch.object(media_mod, "_find_font_path", return_value=None):
            with self.assertRaises(Exception) as ctx:
                self._args({"wm_text": "中文水印"}, "mp4")
            self.assertIn("中文字体", str(ctx.exception))

    def test_image_watermark_complex_graph(self):
        logo = Path(tempfile.mkdtemp(prefix="uc-wm-")) / "logo.png"
        logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        args = self._args({"wm_image": str(logo), "wm_pos": "tl",
                           "wm_scale": 20, "wm_opacity": 40}, "mp4")
        joined = " ".join(args)
        self.assertIn("-filter_complex", args)
        self.assertIn("colorchannelmixer=aa=0.40", joined)
        self.assertIn("overlay=", joined)
        self.assertIn("-map", args)

    def test_image_watermark_missing_file(self):
        from uniconvert.errors import ConversionError

        with self.assertRaises(ConversionError):
            self._args({"wm_image": "C:/nope/logo.png"}, "mp4")


if __name__ == "__main__":
    unittest.main()
