"""GUI 冒烟测试：真实驱动 tkinter 界面走完「添加 → 转换 → 移除 → 清空」。

无图形环境（纯 CI/无桌面）时自动跳过。
回归背景：v1.0 曾因 ``_load_file_list`` 未定义导致双击启动崩溃、
``remove_selected`` 删除后残留失效行导致 TclError——
这两个问题只有"真正跑一遍界面"才能拦住，故补上本测试。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    HAS_DISPLAY = True
except Exception:  # noqa: BLE001 - 无显示器 / 无 tkinter
    HAS_DISPLAY = False

from uniconvert.core.job import Status


@unittest.skipUnless(HAS_DISPLAY, "无图形环境，跳过 GUI 测试")
class TestGuiSmoke(unittest.TestCase):
    def setUp(self):
        from uniconvert.gui import ConverterApp

        self.tmp = Path(tempfile.mkdtemp(prefix="uc-gui-"))
        self.root = tk.Tk()
        self.root.withdraw()  # 测试期间不弹窗
        self.app = ConverterApp(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    def _make_files(self, n: int = 2):
        paths = []
        for i in range(n):
            p = self.tmp / f"g{i}.json"
            p.write_text('{"i": %d}' % i, encoding="utf-8")
            paths.append(p)
        return paths

    def _pump(self, seconds: float = 30.0) -> None:
        """驱动 tkinter 事件循环直到转换线程结束。"""
        deadline = time.time() + seconds
        while self.app.running and time.time() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()

    # ------------------------------------------------------------------ #
    def test_app_constructs(self):
        """构造即回归：曾因未定义方法在此处 AttributeError。"""
        self.assertEqual(len(self.app.rows), 0)

    def test_convert_flow(self):
        paths = self._make_files(2)
        self.app._add_paths(paths)
        self.assertEqual(len(self.app.rows), 2)
        self.assertEqual(len(self.app.tree.get_children()), 2)

        self.app.target_var.set("yaml")
        self.app.start()
        self._pump()

        self.assertFalse(self.app.running)
        outs = sorted(self.tmp.glob("*.yaml"))
        log = self.app.log.get("1.0", "end")
        self.assertEqual(len(outs), 2, f"应有 2 个输出\n{log}")
        self.assertTrue(all(r.status == "完成" for r in self.app.rows), log)

    def test_remove_selected_then_convert(self):
        """回归：删除选中后立即转换，旧代码会因失效行炸 TclError。"""
        paths = self._make_files(3)
        self.app._add_paths(paths)
        self.app.tree.selection_set(self.app.rows[0].iid)
        self.app.remove_selected()

        self.assertEqual(len(self.app.rows), 2)
        self.assertEqual(len(self.app.tree.get_children()), 2)

        self.app.target_var.set("yaml")
        self.app.start()
        self._pump()

        log = self.app.log.get("1.0", "end")
        self.assertEqual(len(sorted(self.tmp.glob("*.yaml"))), 2, log)

    def test_clear_all(self):
        self.app._add_paths(self._make_files(2))
        self.app.clear_all()
        self.assertEqual(len(self.app.rows), 0)
        self.assertEqual(len(self.app.tree.get_children()), 0)

    def test_failed_conversion_marks_row(self):
        bad = self.tmp / "broken.json"
        bad.write_text("{不是合法 JSON", encoding="utf-8")
        self.app._add_paths([bad])
        self.app.target_var.set("yaml")
        self.app.start()
        self._pump()
        self.assertEqual(self.app.rows[0].status, "失败")


if __name__ == "__main__":
    unittest.main()
