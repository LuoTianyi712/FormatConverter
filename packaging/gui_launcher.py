"""PyInstaller 独立 GUI 入口：UniConvert.exe。

打包后双击即为图形界面（无控制台窗口）。
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path


def _ensure_source_root() -> None:
    """从源码直接运行时把仓库根目录加入 sys.path（打包后无需）。"""
    try:
        import uniconvert  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    # PyInstaller + 多进程（ThreadPool 不用，但保险起见防止二次启动）
    multiprocessing.freeze_support()
    _ensure_source_root()
    try:
        from uniconvert.gui import launch

        launch()
        return 0
    except Exception as exc:  # noqa: BLE001 - 冻结环境里至少给用户一个可读错误
        try:
            import tkinter.messagebox as messagebox

            messagebox.showerror("UniConvert 启动失败", f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            print(f"UniConvert 启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
