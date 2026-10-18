"""CLI 与公共 API 测试。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert import can_convert, convert_batch, list_formats, options_for, route
from uniconvert.cli import main
from uniconvert.core.job import Status
from uniconvert.plugins import load_plugins


def run_cli(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(list(argv))
    except SystemExit as exc:  # argparse --version/--help
        code = int(exc.code or 0)
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-cli-"))

    def test_version(self):
        code, out = run_cli("--version")
        # argparse version 直接 SystemExit(0)
        self.assertEqual(code, 0)

    def test_formats(self):
        code, out = run_cli("formats", "--category", "data")
        self.assertEqual(code, 0)
        self.assertIn("json", out)

    def test_formats_json(self):
        import contextlib as c

        buf = io.StringIO()
        with c.redirect_stdout(buf):
            code = main(["formats", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertTrue(any(f["name"] == "yaml" for f in data))

    def test_route(self):
        code, out = run_cli("route", "docx", "epub")
        self.assertEqual(code, 0)
        self.assertIn("html", out)

    def test_route_missing(self):
        code, out = run_cli("route", "midi", "mxf")
        self.assertEqual(code, 1)

    def test_can(self):
        code, _ = run_cli("can", "json", "yaml")
        self.assertEqual(code, 0)
        code2, _ = run_cli("can", "midi", "mxf")
        self.assertEqual(code2, 1)

    def test_info(self):
        p = self.tmp / "a.json"
        p.write_text("{}", encoding="utf-8")
        code, out = run_cli("info", str(p))
        self.assertEqual(code, 0)
        self.assertIn("json", out)

    def test_doctor(self):
        code, out = run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("ffmpeg", out)

    def test_convert_single(self):
        src = self.tmp / "a.json"
        src.write_text('{"k": "v"}', encoding="utf-8")
        dst = self.tmp / "a.toml"
        code, out = run_cli("convert", str(src), str(dst))
        self.assertEqual(code, 0, out)
        self.assertTrue(dst.exists())

    def test_convert_to_flag(self):
        src = self.tmp / "b.json"
        src.write_text('{"k": 1}', encoding="utf-8")
        code, out = run_cli("convert", str(src), "--to", "xml")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tmp / "b.xml").exists())

    def test_convert_options(self):
        src = self.tmp / "c.json"
        src.write_text('{"b": 1, "a": 2}', encoding="utf-8")
        dst = self.tmp / "c2.json"
        code, _ = run_cli("convert", str(src), str(dst), "-o", "sort_keys=true")
        self.assertEqual(code, 0)
        text = dst.read_text(encoding="utf-8")
        self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_batch_with_outdir(self):
        for i in range(3):
            (self.tmp / f"f{i}.json").write_text('{"n": %d}' % i, encoding="utf-8")
        outdir = self.tmp / "out"
        code, out = run_cli("batch", str(self.tmp / "f*.json"), "-t", "yaml", "--out", str(outdir))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(list(outdir.glob("*.yaml"))), 3)

    def test_convert_dry_run(self):
        src = self.tmp / "d.json"
        src.write_text("{}", encoding="utf-8")
        dst = self.tmp / "d.xml"
        code, out = run_cli("convert", str(src), str(dst), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(dst.exists())

    def test_stats(self):
        code, out = run_cli("stats")
        self.assertEqual(code, 0)
        self.assertIn("节点", out)

    def test_options_cmd(self):
        code, out = run_cli("options", "mp4")
        self.assertEqual(code, 0)
        self.assertIn("crf", out)

    def test_plugins_cmd(self):
        code, out = run_cli("plugins")
        self.assertEqual(code, 0)


class TestApi(unittest.TestCase):
    def test_list_formats(self):
        formats = list_formats()
        self.assertGreater(len(formats), 100)
        images = list_formats("image")
        self.assertTrue(all(f["category"] == "image" for f in images))

    def test_can_convert(self):
        self.assertTrue(can_convert("json", "yaml"))
        self.assertTrue(can_convert("a.json", "a.yaml"))
        self.assertFalse(can_convert("midi", "mxf"))

    def test_route_obj(self):
        r = route("srt", "vtt")
        self.assertEqual(len(r), 1)
        self.assertEqual(str(r), "srt → vtt")
        self.assertIn("路径", "\n".join(r.describe()))

    def test_options_for(self):
        info = options_for("mp4")
        self.assertEqual(info["format"], "mp4")

    def test_convert_batch_dicts(self):
        tmp = Path(tempfile.mkdtemp(prefix="uc-api-"))
        results = []
        for i in range(3):
            src = tmp / f"x{i}.json"
            src.write_text('{"i": %d}' % i, encoding="utf-8")
            results.append({"src": str(src), "dst": str(tmp / f"x{i}.yaml")})
        out = convert_batch(results, workers=3)
        self.assertTrue(all(r.status is Status.DONE for r in out), [r.error for r in out])

    def test_load_plugins_empty(self):
        empty = Path(tempfile.mkdtemp(prefix="uc-plugins-"))
        infos = load_plugins(extra_dirs=[empty], entry_points=False)
        self.assertEqual(infos, [])

    def test_load_plugins_broken_collected(self):
        d = Path(tempfile.mkdtemp(prefix="uc-plugins2-"))
        (d / "good.py").write_text(
            "from uniconvert.core import register\n"
            "from pathlib import Path\n"
            "@register('plug_alpha', 'plug_beta', group='test')\n"
            "def conv(src, dst, opts):\n"
            "    Path(dst).write_bytes(Path(src).read_bytes())\n"
            "    return dst\n",
            encoding="utf-8",
        )
        (d / "bad.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        infos = load_plugins(extra_dirs=[d], entry_points=False)
        self.assertEqual(len(infos), 2)
        ok = [i for i in infos if i.ok]
        bad = [i for i in infos if not i.ok]
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("boom", bad[0].error)
        # 插件注册的边生效了
        self.assertTrue(can_convert("plug_alpha", "plug_beta"))


if __name__ == "__main__":
    unittest.main()
