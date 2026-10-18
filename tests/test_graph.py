"""转换图与寻路测试（用独立 Registry，不碰全局状态）。"""

from __future__ import annotations

import unittest

from uniconvert.core.graph import (
    Route,
    build_graph,
    find_route,
    missing_for,
    reachable_from,
)
from uniconvert.core.registry import Registry


def _noop(src, dst, opts):
    return dst


class TestGraph(unittest.TestCase):
    def setUp(self):
        self.reg = Registry()

    def test_direct(self):
        self.reg.add("a", "b", _noop)
        r = find_route("a", "b", registry=self.reg)
        self.assertIsNotNone(r)
        self.assertEqual(len(r), 1)
        self.assertEqual(r.nodes, ["a", "b"])

    def test_shortest_wins_by_cost(self):
        self.reg.add("a", "b", _noop)
        self.reg.add("b", "c", _noop)
        self.reg.add("a", "c", _noop, weight=10.0)  # 直连但昂贵
        r = find_route("a", "c", registry=self.reg)
        self.assertEqual(r.nodes, ["a", "b", "c"])

    def test_quality_factor(self):
        self.reg.add("a", "b", _noop)
        self.reg.add("b", "c", _noop)
        # 直连次质量、两跳高成本 → 应当选直连
        self.reg.add("a", "c", _noop, weight=0.5, quality=1.0)
        self.reg.add("x", "y", _noop)
        r = find_route("a", "c", registry=self.reg)
        self.assertEqual(len(r), 1)

    def test_no_route(self):
        self.reg.add("a", "b", _noop)
        self.assertIsNone(find_route("a", "z", registry=self.reg))

    def test_identity(self):
        r = find_route("q", "q", registry=self.reg)
        self.assertIsNotNone(r)
        self.assertTrue(r.identity)
        self.assertEqual(len(r), 0)

    def test_self_edge_preferred(self):
        self.reg.add("q", "q", _noop, allow_self=True)
        r = find_route("q", "q", registry=self.reg)
        self.assertEqual(len(r), 1)

    def test_deps_filtering(self):
        self.reg.add("a", "b", _noop, deps=("unlikely_dep_xyz123",))
        self.assertIsNone(find_route("a", "b", registry=self.reg))
        r = find_route("a", "b", registry=self.reg, available_only=False)
        self.assertEqual(len(r), 1)
        # 缺依赖诊断
        self.assertEqual(missing_for("a", "b", registry=self.reg),
                         ["unlikely_dep_xyz123"])

    def test_fallback_route_when_dep_missing(self):
        self.reg.add("a", "b", _noop, deps=("unlikely_dep_xyz123",))
        self.reg.add("a", "c", _noop)
        self.reg.add("c", "b", _noop)
        r = find_route("a", "b", registry=self.reg)
        self.assertEqual(r.nodes, ["a", "c", "b"])

    def test_reachable(self):
        self.reg.add("a", "b", _noop)
        self.reg.add("b", "c", _noop)
        targets = reachable_from("a", registry=self.reg)
        self.assertEqual(set(targets), {"b", "c"})
        self.assertEqual(targets["c"].nodes, ["a", "b", "c"])

    def test_build_graph(self):
        self.reg.add("a", ["b", "c"], _noop)
        adj = build_graph(self.reg)
        self.assertEqual({e.dst for e in adj["a"]}, {"b", "c"})

    def test_cycle_safe(self):
        self.reg.add("a", "b", _noop)
        self.reg.add("b", "a", _noop)
        self.reg.add("b", "c", _noop)
        r = find_route("a", "c", registry=self.reg)
        self.assertEqual(r.nodes, ["a", "b", "c"])

    def test_route_str(self):
        self.reg.add("json", "yaml", _noop)
        r = find_route("json", "yaml", registry=self.reg)
        self.assertEqual(str(r), "json → yaml")


class TestGlobalRoutes(unittest.TestCase):
    """对真实注册表做冒烟断言（内置转换器已导入）。"""

    def test_builtin_routes(self):
        import uniconvert  # noqa: F401

        self.assertIsNotNone(find_route("json", "yaml"))
        self.assertIsNotNone(find_route("srt", "vtt"))
        self.assertIsNotNone(find_route("md", "html"))
        self.assertIsNotNone(find_route("csv", "xml"))

    def test_media_route_without_ffmpeg(self):
        """没有 ffmpeg 时不允许出现真正的转码路径。

        若存在 mp4→mkv 的路径，只能是压缩流中转（运行时会被文件头校验拦截），
        这在设计上是刻意允许的降级提示路径。
        """
        from uniconvert.core.deps import has_dep

        import uniconvert  # noqa: F401

        r = find_route("mp4", "mkv")
        if has_dep("ffmpeg"):
            self.assertIsNotNone(r)
            self.assertNotIn("gz", r.nodes)
        else:
            if r is not None:
                self.assertIn("gz", r.nodes)


if __name__ == "__main__":
    unittest.main()
