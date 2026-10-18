"""OCR：图片 / 扫描版 PDF → 文本（Tesseract 后端，零 Python 依赖）。

用 Tesseract **命令行**而不是 pytesseract —— 少一层依赖，
且方便打包 exe 的用户单独安装引擎。

```bash
uniconvert ocr 截图.png                          # → 截图.txt
uniconvert ocr 扫描件.pdf 扫描件.txt -o dpi=300   # 逐页渲染再识别
uniconvert convert 图片.png 图片.txt              # 走转换图（ocr 组边）
```

选项：``lang``（默认 ``chi_sim+eng``，即中英混排）、``psm``（版面模式）、
``dpi``（PDF 渲染精度，默认 200）、``pages``（PDF 页范围）。

安装引擎：``winget install UB-Mannheim.TesseractOCR``，
中文包（chi_sim）安装时勾选，或另装 chi_sim.traineddata。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.deps import has_dep
from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import need_any, opt_int, opt_str, read_bytes, run_tool, write_text

#: 默认识别语言：中文简体 + 英文混排
DEFAULT_LANG = "chi_sim+eng"


def _tesseract() -> str:
    need_any(["tesseract", "exe:tesseract"],
             why="OCR 需要 Tesseract 引擎",
             feature="OCR")
    exe = shutil.which("tesseract")
    if exe:
        return exe
    raise ConversionError("找不到 tesseract 可执行文件")  # pragma: no cover


def _run_tesseract(image_path: Path, *, lang: str, psm: int, timeout: float = 600) -> str:
    args = [_tesseract(), str(image_path), "stdout", "-l", lang or DEFAULT_LANG]
    if psm:
        args += ["--psm", str(psm)]
    proc = run_tool(args, timeout=timeout)
    text = proc.stdout.decode("utf-8", "replace")
    if not text.strip():
        # 有的构架 stdout 编码不同 / 空图，给个可读提示
        raise ConversionError(
            "OCR 没有识别出任何文本（图片可能太模糊，或缺少对应语言包 "
            f"{lang}）"
        )
    return text


def ocr_image(src: Path, dst: Path | None, opts) -> Path | None:
    """识别单张图片；``dst`` 为 None 时只返回文本、不落盘。"""
    lang = opt_str(opts, "lang", DEFAULT_LANG) or DEFAULT_LANG
    psm = opt_int(opts, "psm", 0)
    text = _run_tesseract(Path(src), lang=lang, psm=psm)
    if dst is None:
        return None
    return write_text(dst, text, opts)


def _render_pdf_pages(data: bytes, dpi: int) -> list[bytes]:
    """PDF → 页图片（复用 pdf 模块的多后端渲染，返回 PNG 字节列表）。"""
    from .pdf import _render_fitz, _render_pypdfium2

    if has_dep("pypdfium2"):
        return _render_pypdfium2(data, dpi)
    if has_dep("fitz"):
        return _render_fitz(data, dpi)
    need_any(["pypdfium2", "fitz", "pdftoppm"],
             why="OCR PDF 需要先把页面渲染成图片",
             feature="PDF OCR")


def ocr_pdf(src: Path, dst: Path | None, opts) -> Path | None:
    """扫描版 PDF 逐页渲染后 OCR，页间用 ``=== 第 N 页 ===`` 分隔。"""
    import tempfile

    from .pdf import _parse_pages

    data = read_bytes(src)
    dpi = opt_int(opts, "dpi", 200)
    pages_spec = opt_str(opts, "pages", "")
    pngs = _render_pdf_pages(data, dpi)
    wanted = _parse_pages(pages_spec, len(pngs))

    chunks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="uniconvert-ocr-") as tmp:
        for page_no in wanted:
            png_path = Path(tmp) / f"page-{page_no + 1:04d}.png"
            png_path.write_bytes(pngs[page_no])
            text = _run_tesseract(
                png_path,
                lang=opt_str(opts, "lang", DEFAULT_LANG) or DEFAULT_LANG,
                psm=opt_int(opts, "psm", 0),
            )
            chunks.append(f"=== 第 {page_no + 1} 页 ===\n{text.strip()}")
    result = "\n\n".join(chunks) + "\n"
    if dst is None:
        return None
    return write_text(dst, result, opts)


def ocr(src: str | Path, dst: str | Path | None = None, **opts) -> str:
    """统一的 OCR 入口：图片直接识别，PDF 逐页识别。

    不传 ``dst`` 时返回识别文本；传了则写入文件并返回路径字符串。
    """
    import tempfile

    from ..core.job import Options

    options = Options.from_any(opts)
    src_p = Path(src)
    is_pdf = src_p.suffix.lower() == ".pdf"
    if not src_p.is_file():
        raise ConversionError(f"文件不存在：{src_p}")

    if dst is not None:
        dst_p = Path(dst)
        if is_pdf:
            ocr_pdf(src_p, dst_p, options)
        else:
            ocr_image(src_p, dst_p, options)
        return str(dst_p)

    # 只取文本：写到临时文件再读回（识别只跑一次）
    with tempfile.TemporaryDirectory(prefix="uniconvert-ocr-") as tmp:
        tmp_out = Path(tmp) / "out.txt"
        if is_pdf:
            ocr_pdf(src_p, tmp_out, options)
        else:
            ocr_image(src_p, tmp_out, options)
        return tmp_out.read_text(encoding="utf-8")


def ocr_convert(src: Path, dst: Path, opts) -> Path:
    """转换图里的边实现：图片 → txt。"""
    text = _run_tesseract(
        src,
        lang=opt_str(opts, "lang", DEFAULT_LANG) or DEFAULT_LANG,
        psm=opt_int(opts, "psm", 0),
    )
    return write_text(dst, text, opts)


# --------------------------------------------------------------------------- #
# 注册（图片 → 文本）
# --------------------------------------------------------------------------- #
def register_ocr_converters() -> int:
    count = 0
    for sf in ("png", "jpg", "webp", "bmp", "tiff", "gif"):
        REGISTRY.add(
            sf, "txt", ocr_convert,
            deps=("tesseract",),
            group="ocr",
            weight=1.5, quality=0.8,
            note="OCR 识别文字（Tesseract）",
        )
        count += 1
    return count
