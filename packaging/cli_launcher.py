"""PyInstaller 独立 CLI 入口：uniconvert.exe。

打包后为控制台程序，用法与 ``python -m uniconvert`` 完全一致：

    uniconvert.exe convert a.json a.yaml
    uniconvert.exe batch *.png -t webp --out out/ -j 8
    uniconvert.exe doctor
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_source_root() -> None:
    """从源码直接运行时把仓库根目录加入 sys.path（打包后无需）。"""
    try:
        import uniconvert  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    _ensure_source_root()
    from uniconvert.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
