"""OCR / CAJ / 人声分离 / Web SSE 测试。

OCR 与 Demucs 在这台机器上未安装，用 monkeypatch 打桩验证全流程逻辑；
真实后端存在时自动走真跑分支（本机不会触发，但 CI 装了就覆盖）。
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import uniconvert  # noqa: F401
from uniconvert import convert
from uniconvert.core.deps import has_dep
from uniconvert.core.job import Status

try:
    import pypdf

    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


# =========================================================================== #
# OCR
# =========================================================================== #
class TestOcr(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-ocr-"))

    def _fake_run(self, text: str):
        def fake(args, **kw):
            return subprocess.CompletedProcess(args, 0, stdout=text.encode("utf-8"),
                                               stderr=b"")
        return fake

    def test_ocr_image_writes_text(self):
        from uniconvert.converters import ocr as ocr_mod

        src = self.tmp / "shot.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        dst = self.tmp / "shot.txt"
        with mock.patch.object(ocr_mod, "_tesseract", return_value="tesseract"), \
             mock.patch.object(ocr_mod, "run_tool",
                               side_effect=self._fake_run("发票金额 100 元\n")):
            result = ocr_mod.ocr_image(src, dst, None)
        self.assertEqual(result, dst)
        self.assertIn("发票金额", dst.read_text(encoding="utf-8"))

    def test_ocr_print_mode(self):
        from uniconvert.converters import ocr as ocr_mod

        src = self.tmp / "a.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with mock.patch.object(ocr_mod, "_tesseract", return_value="tesseract"), \
             mock.patch.object(ocr_mod, "run_tool",
                               side_effect=self._fake_run("hello")):
            text = ocr_mod.ocr(src)  # 不传 dst → 返回文本
        self.assertIn("hello", text)

    def test_ocr_empty_result_fails_clean(self):
        from uniconvert.converters import ocr as ocr_mod

        src = self.tmp / "b.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with mock.patch.object(ocr_mod, "_tesseract", return_value="tesseract"), \
             mock.patch.object(ocr_mod, "run_tool",
                               side_effect=self._fake_run("")):
            with self.assertRaises(Exception) as ctx:
                ocr_mod.ocr_image(src, None, None)
        self.assertIn("没有识别出任何文本", str(ctx.exception))

    @unittest.skipUnless(HAS_PYPDF, "pypdf 未安装")
    def test_ocr_pdf_pages(self):
        from uniconvert.converters import ocr as ocr_mod

        writer = pypdf.PdfWriter()
        for _ in range(2):
            writer.add_blank_page(width=200, height=200)
        src = self.tmp / "doc.pdf"
        with src.open("wb") as fh:
            writer.write(fh)

        calls = {"n": 0}

        def fake(args, **kw):
            calls["n"] += 1
            return subprocess.CompletedProcess(args, 0,
                                               stdout=f"第{calls['n']}页文字".encode(),
                                               stderr=b"")

        with mock.patch.object(ocr_mod, "_tesseract", return_value="tesseract"), \
             mock.patch.object(ocr_mod, "run_tool", side_effect=fake):
            dst = self.tmp / "doc.txt"
            ocr_mod.ocr_pdf(src, dst, None)
        text = dst.read_text(encoding="utf-8")
        self.assertIn("=== 第 1 页 ===", text)
        self.assertIn("=== 第 2 页 ===", text)
        self.assertIn("第2页文字", text)

    def test_dependency_error_when_missing(self):
        """真的没装 Tesseract 时给出带安装提示的依赖错误。"""
        if has_dep("tesseract"):
            self.skipTest("本机装了 tesseract")
        from uniconvert.errors import DependencyMissingError

        src = self.tmp / "c.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with self.assertRaises(DependencyMissingError) as ctx:
            uniconvert.ocr(src)
        self.assertIn("Tesseract", str(ctx.exception))

    def test_png_txt_route_not_offered_without_tesseract(self):
        from uniconvert.core.graph import find_route

        r = find_route("png", "txt")
        if has_dep("tesseract"):
            self.assertIsNotNone(r)
            self.assertEqual(r.steps[0].group, "ocr")
        else:
            # 没有 tesseract：允许 gz 伪路径存在（运行时会校验报错），
            # 但绝不能是 OCR 直达
            if r is not None:
                self.assertNotEqual(r.steps[0].group, "ocr")


# =========================================================================== #
# CAJ → PDF
# =========================================================================== #
@unittest.skipUnless(HAS_PYPDF, "pypdf 未安装")
class TestCaj(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-caj-"))

    def _pdf_bytes(self) -> bytes:
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def test_caj_with_embedded_pdf(self):
        pdf = self._pdf_bytes()
        caj = self.tmp / "paper.caj"
        caj.write_bytes(b"CAJ\x00\x01\x02" + b"\x00" * 512 + pdf)
        dst = self.tmp / "paper.pdf"
        result = convert(caj, dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(len(pypdf.PdfReader(str(dst)).pages), 1)

    def test_renamed_pdf_as_caj(self):
        caj = self.tmp / "renamed.caj"
        caj.write_bytes(self._pdf_bytes())
        dst = self.tmp / "renamed.pdf"
        result = convert(caj, dst)
        self.assertEqual(result.status, Status.DONE, result.error)

    def test_encrypted_caj_fails_clean(self):
        caj = self.tmp / "weird.caj"
        caj.write_bytes(b"CAJH" + b"\x99" * 1024)  # 没有可提取 PDF
        dst = self.tmp / "weird.pdf"
        result = convert(caj, dst)
        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("caj2pdf", result.error)


# =========================================================================== #
# 人声分离（打桩）
# =========================================================================== #
class TestSeparate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-sep-"))

    def test_separate_flow(self):
        from uniconvert.converters import media as media_mod

        src = self.tmp / "song.mp3"
        src.write_bytes(b"ID3" + b"\x00" * 128)
        outdir = self.tmp / "stems"

        def fake_run(args, **kw):
            # 模拟 demucs 产出两个 wav
            stem = outdir / "htdemucs" / "song"
            stem.mkdir(parents=True, exist_ok=True)
            (stem / "vocals.wav").write_bytes(b"RIFFvocals")
            (stem / "no_vocals.wav").write_bytes(b"RIFFinstr")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        with mock.patch.object(media_mod, "_demucs_cmd", return_value=["demucs"]), \
             mock.patch.object(media_mod, "run_tool", side_effect=fake_run):
            result = media_mod.separate_audio(src, outdir, None)
        self.assertTrue(result["vocals"].exists())
        self.assertTrue(result["instrumental"].exists())

    def test_demucs_missing_message(self):
        from uniconvert.converters import media as media_mod
        from uniconvert.errors import ConversionError

        import importlib.util

        if importlib.util.find_spec("demucs") or has_dep("demucs"):
            self.skipTest("本机装了 demucs")
        with self.assertRaises(ConversionError) as ctx:
            media_mod._demucs_cmd()
        self.assertIn("pip install demucs", str(ctx.exception))


# =========================================================================== #
# Web：异步任务 + SSE + 历史
# =========================================================================== #
try:
    import flask  # noqa: F401

    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "Flask 未安装")
class TestWebTasks(unittest.TestCase):
    def setUp(self):
        from uniconvert.web import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-web-"))

    def _post_task(self):
        data = {
            "files": [(io.BytesIO(b'{"name": "\\u6d4b\\u8bd5"}'), "t.json")],
            "target": "yaml",
            "on_exists": "rename",
            "options": "",
        }
        resp = self.client.post("/api/tasks", data=data,
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["tid"]

    def test_task_flow_and_sse(self):
        tid = self._post_task()
        # 等任务完成
        deadline = time.time() + 30
        status = None
        while time.time() < deadline:
            status = self.client.get(f"/api/tasks/{tid}").get_json()
            if status["status"] == "done":
                break
            time.sleep(0.05)
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(len(status["results"]), 1)
        self.assertEqual(status["results"][0]["status"], "done", status)

        # SSE 事件流（任务已完成 → 事件队列里应包含 done 哨兵）
        sse = self.client.get(f"/api/tasks/{tid}/events")
        self.assertEqual(sse.status_code, 200)
        body = sse.get_data(as_text=True)
        self.assertIn("event: progress", body)
        self.assertIn("event: done", body)

        # 下载
        dl = self.client.get(f"/api/download/{tid}/t.yaml")
        self.assertEqual(dl.status_code, 200)
        self.assertIn("name", dl.get_data(as_text=True))

    def test_history(self):
        tid = self._post_task()
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.client.get(f"/api/tasks/{tid}").get_json()["status"] == "done":
                break
            time.sleep(0.05)
        history = self.client.get("/api/history").get_json()
        self.assertTrue(any(h["tid"] == tid for h in history), history)

    def test_sync_convert_still_works(self):
        data = {
            "files": [(io.BytesIO(b'{"a": 1}'), "s.json")],
            "target": "yaml",
        }
        resp = self.client.post("/api/convert", data=data,
                                content_type="multipart/form-data")
        payload = resp.get_json()
        self.assertEqual(payload["results"][0]["status"], "done")

    def test_page_contains_sse_js(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("EventSource", page)
        self.assertIn("/api/tasks/", page)


if __name__ == "__main__":
    unittest.main()
