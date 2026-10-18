"""可选依赖探测。

:class:`~uniconvert.core.registry.Converter` 的 ``deps`` 字段里可以写：

* 外部命令名（如 ``ffmpeg``、``7z``）—— 用 ``shutil.which`` 探测；
* Python 模块名（如 ``PIL``、``fontTools``）—— 用 ``importlib.util.find_spec`` 探测。

默认两者都试；可用 ``exe:`` / ``py:`` 前缀强制指定探测方式。
探测结果带缓存（同进程内只探一次），CLI ``doctor`` 子命令也复用这里。
"""

from __future__ import annotations

import importlib.util
import shutil
from functools import lru_cache

__all__ = ["has_dep", "deps_ok", "missing_deps", "install_hint", "known_optional"]

#: 常见可选依赖 -> 安装提示（doctor / 报错信息里展示）
INSTALL_HINTS: dict[str, str] = {
    "ffmpeg": "winget install Gyan.FFmpeg（或 choco install ffmpeg / 官网下载加入 PATH）",
    "ffprobe": "随 ffmpeg 一起安装",
    "7z": "winget install 7zip.7zip（或官网下载 7z.exe 加入 PATH）",
    "magick": "winget install ImageMagick.ImageMagick",
    "soffice": "安装 LibreOffice（soffice.exe 加入 PATH）",
    "pandoc": "winget install JohnMacFarlane.Pandoc",
    "pdftoppm": "安装 Poppler for Windows 并把 bin 加入 PATH",
    "pdftotext": "安装 Poppler for Windows 并把 bin 加入 PATH",
    "unrar": "安装 WinRAR / UnRAR 并把 unrar.exe 加入 PATH",
    "rsvg-convert": "安装 librsvg（或改用 cairosvg）",
    "inkscape": "winget install Inkscape.Inkscape",
    "PIL": "pip install Pillow",
    "yaml": "pip install PyYAML",
    "fontTools": "pip install fonttools",
    "brotli": "pip install brotli",
    "zstandard": "pip install zstandard",
    "cairosvg": "pip install CairoSVG",
    "openpyxl": "pip install openpyxl",
    "pypdf": "pip install pypdf",
    "pypdfium2": "pip install pypdfium2",
    "fitz": "pip install PyMuPDF",
    "py7zr": "pip install py7zr",
    "rarfile": "pip install rarfile",
    "ebooklib": "pip install EbookLib",
    "markdown": "pip install Markdown",
    "markdownify": "pip install markdownify",
    "bs4": "pip install beautifulsoup4",
    "pytesseract": "pip install pytesseract（另需安装 Tesseract-OCR）",
    "tesseract": "winget install UB-Mannheim.TesseractOCR",
}


@lru_cache(maxsize=None)
def has_dep(name: str) -> bool:
    """探测单个依赖是否存在。

    支持 ``exe:xxx`` / ``py:xxx`` 前缀强制探测方式；
    无前缀时先当外部命令找，再当 Python 模块找。
    """
    name = name.strip()
    if not name:
        return True
    if name.startswith("exe:"):
        return shutil.which(name[4:].strip()) is not None
    if name.startswith("py:"):
        return _has_module(name[3:].strip())
    return shutil.which(name) is not None or _has_module(name)


@lru_cache(maxsize=None)
def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def missing_deps(deps: tuple[str, ...] | list[str]) -> list[str]:
    """返回缺掉的依赖名列表（空 deps 视为「无需检查」，永远可用）。"""
    return [d for d in deps if not has_dep(d)]


def deps_ok(deps: tuple[str, ...] | list[str]) -> bool:
    """全部依赖可用才为真；``deps`` 为空表示无需检查。"""
    return all(has_dep(d) for d in deps)


def install_hint(name: str) -> str:
    """给某个依赖生成安装提示。"""
    hint = INSTALL_HINTS.get(name.rstrip())
    if hint:
        return hint
    if name.startswith("exe:") or (not name.startswith("py:") and shutil.which(name)):
        return f"把 {name} 加入 PATH，或安装对应软件"
    return f"pip install {name} 或安装对应软件"


def known_optional() -> dict[str, str]:
    """已知可选依赖及安装提示（doctor 用）。"""
    return dict(INSTALL_HINTS)


def clear_cache() -> None:
    """清空探测缓存（测试用：模拟依赖安装前后）。"""
    has_dep.cache_clear()
    _has_module.cache_clear()
