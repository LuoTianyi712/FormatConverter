"""PyInstaller 独立 GUI 入口：UniConvert.exe。

打包后双击即为图形界面（无控制台窗口）。
启动失败时：先把完整堆栈写进 ``%TEMP%/uniconvert-crash.log``，
再弹错误框（用户可直接把日志发来定位）。
"""

from __future__ import annotations

import multiprocessing
import sys
import tempfile
import traceback
from pathlib import Path


def _ensure_source_root() -> None:
    """从源码直接运行时把仓库根目录加入 sys.path（打包后无需）。"""
    try:
        import uniconvert  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_crash_log(exc: BaseException) -> Path | None:
    try:
        log_path = Path(tempfile.gettempdir()) / "uniconvert-crash.log"
        log_path.write_text(
            "UniConvert GUI 启动失败\n" + traceback.format_exc(),
            encoding="utf-8",
        )
        return log_path
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    # PyInstaller + 多进程（ThreadPool 不用，但保险起见防止二次启动）
    multiprocessing.freeze_support()
    _ensure_source_root()
    try:
        from uniconvert.gui import launch

        launch()
        return 0
    except Exception as exc:  # noqa: BLE001 - 冻结环境里至少给用户一个可读错误
        log_path = _write_crash_log(exc)
        detail = f"{type(exc).__name__}: {exc}"
        if log_path is not None:
            detail += f"\n\n详细日志：{log_path}"
        try:
            import tkinter.messagebox as messagebox

            messagebox.showerror("UniConvert 启动失败", detail)
        except Exception:  # noqa: BLE001
            print(f"UniConvert 启动失败：{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
