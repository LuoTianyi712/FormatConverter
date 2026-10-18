"""引擎测试：覆盖策略、干跑、重试、取消、缺依赖、批量并发。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import uniconvert  # noqa: F401
from uniconvert.core.engine import Engine
from uniconvert.core.job import Job, Options, Status
from uniconvert.core.registry import Registry


def _copy(src, dst, opts):
    dst.write_bytes(Path(src).read_bytes())
    return dst


class TestEnginePolicies(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-eng-"))
        self.src = self.tmp / "a.json"
        self.src.write_text('{"x": 1}', encoding="utf-8")
        self.dst = self.tmp / "a.yaml"

    def test_basic_convert(self):
        engine = Engine()
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertTrue(self.dst.exists())
        self.assertGreater(result.dst_size, 0)
        self.assertEqual(len(result.steps), 1)

    def test_on_exists_rename(self):
        self.dst.write_text("occupied", encoding="utf-8")
        engine = Engine(on_exists="rename")
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertNotEqual(result.output, self.dst)
        self.assertTrue(result.output.exists())
        self.assertEqual(self.dst.read_text(encoding="utf-8"), "occupied")

    def test_on_exists_skip(self):
        self.dst.write_text("occupied", encoding="utf-8")
        engine = Engine(on_exists="skip")
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.SKIPPED)

    def test_on_exists_overwrite(self):
        self.dst.write_text("old", encoding="utf-8")
        engine = Engine(on_exists="overwrite")
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertNotEqual(self.dst.read_text(encoding="utf-8"), "old")

    def test_on_exists_error(self):
        self.dst.write_text("occupied", encoding="utf-8")
        engine = Engine(on_exists="error")
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("已存在", result.error)

    def test_dry_run(self):
        engine = Engine(dry_run=True)
        result = engine.convert(self.src, self.dst)
        self.assertEqual(result.status, Status.SKIPPED)
        self.assertTrue(result.dry_run)
        self.assertFalse(self.dst.exists())
        self.assertIsNotNone(result.route)

    def test_missing_source(self):
        engine = Engine()
        result = engine.convert(self.tmp / "nope.json", self.dst)
        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("不存在", result.error)

    def test_unknown_target(self):
        engine = Engine()
        result = engine.convert(self.src, self.tmp / "a.zzz")
        self.assertEqual(result.status, Status.FAILED)

    def test_auto_suffix(self):
        engine = Engine()
        result = engine.convert(self.src, to="yaml")
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(result.output.suffix, ".yaml")

    def test_dir_target(self):
        outdir = self.tmp / "out"
        outdir.mkdir()
        engine = Engine()
        result = engine.convert(self.src, outdir, to="yaml")
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(result.output.parent, outdir)


class TestEngineDepsAndRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-eng2-"))
        self.reg = Registry()
        self.src = self.tmp / "x.foo"
        self.src.write_text("data", encoding="utf-8")
        self.reg.add("foo", "bar", _copy)
        self.reg.add("bar", "baz", _copy)

    def _engine(self, **kw):
        return Engine(registry=self.reg, **kw)

    def test_route_through_registry(self):
        result = self._engine().convert(self.src, self.tmp / "x.baz")
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(result.steps, ("foo→bar", "bar→baz"))

    def test_dependency_missing_error(self):
        self.reg.add("foo", "qux", _copy, deps=("definitely_missing_tool_xyz",))
        result = self._engine().convert(self.src, self.tmp / "x.qux")
        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("缺少依赖", result.error)
        self.assertIn("definitely_missing_tool_xyz", result.error)

    def test_no_route(self):
        result = self._engine().convert(self.src, self.tmp / "x.zzz")
        self.assertEqual(result.status, Status.FAILED)

    def test_retry_succeeds(self):
        calls = {"n": 0}

        def flaky(src, dst, opts):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("首次失败")
            return _copy(src, dst, opts)

        self.reg.add("foo", "flaky", flaky)
        result = self._engine(retry=1).convert(self.src, self.tmp / "x.flaky")
        self.assertEqual(result.status, Status.DONE, result.error)
        self.assertEqual(calls["n"], 2)

    def test_no_retry_when_disabled(self):
        def always_fail(src, dst, opts):
            raise RuntimeError("永远失败")

        self.reg.add("foo", "fail", always_fail)
        result = self._engine().convert(self.src, self.tmp / "x.fail")
        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("永远失败", result.error)

    def test_cancel(self):
        engine = self._engine()
        engine.request_cancel()
        result = engine.convert(self.src, self.tmp / "x.bar")
        self.assertEqual(result.status, Status.CANCELLED)

    def test_batch(self):
        jobs = [
            Job(src=self.src, dst=self.tmp / f"out{i}.bar", dst_fmt="bar")
            for i in range(8)
        ]
        results = self._engine(workers=4).run(jobs)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r.status is Status.DONE for r in results),
                        [r.error for r in results])
        self.assertEqual(len({r.output for r in results}), 8)

    def test_progress_events(self):
        events = []
        engine = self._engine(on_progress=events.append)
        engine.convert(self.src, self.tmp / "x.bar")
        stages = [e.stage for e in events]
        self.assertIn("plan", stages)
        self.assertIn("step", stages)
        self.assertIn("done", stages)

    def test_temp_cleanup(self):
        import glob

        before = set(glob.glob(str(Path(tempfile.gettempdir()) / "uniconvert-*")))
        self._engine().convert(self.src, self.tmp / "x.baz")
        after = set(glob.glob(str(Path(tempfile.gettempdir()) / "uniconvert-*")))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
