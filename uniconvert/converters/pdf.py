"""PDF 相关转换（多后端自动降级）。

* ``pdf -> txt``：pypdf / PyMuPDF / pdftotext（poppler）；
* ``pdf -> png/jpg``：pypdfium2 / PyMuPDF / pdftoppm（poppler）/ magick；
* ``png/jpg -> pdf``：Pillow（多张图合并成一个 PDF 见 :func:`images_to_pdf`）。

后端全部运行时探测；一个都没有时给出明确的安装提示。
所有依赖探测只在真正执行转换时发生，路由规划阶段零开销。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core.deps import has_dep
from ..core.registry import REGISTRY
from ..errors import ConversionError, DependencyMissingError
from ._util import need_dep, opt_int, opt_str, read_bytes, run_tool

_RASTER_TARGETS = ("png", "jpg")


def _tmppng(dst: Path, page: int) -> Path:
    return dst.with_name(f"{dst.stem}-{page:04d}{dst.suffix}")


def _parse_pages(spec: str, total: int) -> list[int]:
    """``"1-3,5"`` → 0 基页号列表；空 → 全部。"""
    if not spec.strip():
        return list(range(total))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a = int(a or 1)
            b = int(b or total)
            pages.extend(range(a - 1, min(b, total)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < total]


# =========================================================================== #
# pdf → 文本
# =========================================================================== #
def _pdf_text_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(io_buffer(data))
    return [page.extract_text() or "" for page in reader.pages]


def io_buffer(data: bytes):
    import io

    return io.BytesIO(data)


def _pdf_text_fitz(data: bytes) -> list[str]:
    import fitz  # type: ignore  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def _pdf_text_poppler(src: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="uniconvert-pdf-") as tmp:
        out_txt = Path(tmp) / "out.txt"
        run_tool(["pdftotext", "-layout", str(src), str(out_txt)], timeout=600)
        return [out_txt.read_text("utf-8", "replace")]


def pdf_to_text(src: Path, dst: Path, opts) -> Path:
    data = read_bytes(src)
    pages_text: list[str] | None = None
    errors: list[str] = []

    if has_dep("pypdf"):
        try:
            pages_text = _pdf_text_pypdf(data)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pypdf: {exc}")
    if pages_text is None and has_dep("fitz"):
        try:
            pages_text = _pdf_text_fitz(data)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PyMuPDF: {exc}")
    if pages_text is None and has_dep("pdftotext"):
        try:
            pages_text = _pdf_text_poppler(src)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pdftotext: {exc}")

    if pages_text is None:
        raise DependencyMissingError(
            "pypdf / PyMuPDF / pdftotext",
            why="提取 PDF 文本需要任意一个后端",
            install="pip install pypdf　（或 pip install PyMuPDF，或安装 Poppler）",
        )

    pages = _parse_pages(opt_str(opts, "pages", ""), len(pages_text))
    text = "\n".join(pages_text[p].strip() for p in pages).strip() + "\n"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return dst


# =========================================================================== #
# pdf → 图片
# =========================================================================== #
def _render_pypdfium2(data: bytes, dpi: int) -> list[bytes]:
    import pypdfium2 as pymupdfium  # type: ignore

    pdf = pymupdfium.PdfDocument(io_buffer(data))
    scale = dpi / 72.0
    out: list[bytes] = []
    for page in pdf:
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        import io

        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _render_fitz(data: bytes, dpi: int) -> list[bytes]:
    import fitz  # type: ignore

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            out.append(pix.tobytes("png"))
        return out
    finally:
        doc.close()


def _render_poppler(src: Path, dpi: int, pages_spec: str) -> list[tuple[int, Path]]:
    with tempfile.TemporaryDirectory(prefix="uniconvert-pdf-") as tmp:
        template = Path(tmp) / "page"
        cmd = ["pdftoppm", "-png", "-r", str(dpi), str(src), str(template)]
        if pages_spec:
            parts = []
            for part in pages_spec.split(","):
                part = part.strip()
                if "-" in part:
                    first, _, last = part.partition("-")
                    cmd[1:1] = ["-f", first or "1", "-l", last or first]
                    parts.append(part)
                else:
                    parts.append(part)
            if parts and all("-" not in p for p in pages_spec.split(",")):
                first = int(parts[0])
                last = int(parts[-1])
                cmd[1:1] = ["-f", str(first), "-l", str(last)]
        run_tool(cmd, timeout=1800)
        out: list[tuple[int, Path]] = []
        for f in sorted(Path(tmp).glob("page*.png")):
            import re

            m = re.search(r"(\d+)\s*\.png$", f.name)
            page_no = int(m.group(1)) if m else len(out) + 1
            keep = f.with_name(f"keep{page_no}.png")
            shutil.copy2(f, keep)
            out.append((page_no - 1, keep))
        return out


def pdf_to_image(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    data = read_bytes(src)
    dpi = opt_int(opts, "dpi", 150)
    pages_spec = opt_str(opts, "pages", "")

    images: list[bytes] | None = None
    errors: list[str] = []

    if has_dep("pypdfium2"):
        try:
            images = _render_pypdfium2(data, dpi)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pypdfium2: {exc}")
    if images is None and has_dep("fitz"):
        try:
            images = _render_fitz(data, dpi)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"PyMuPDF: {exc}")
    if images is None and has_dep("pdftoppm"):
        try:
            rendered = _render_poppler(src, dpi, pages_spec)
            images = [p.read_bytes() for _, p in rendered]
            for _, p in rendered:
                p.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pdftoppm: {exc}")

    if images is None:
        detail = f"（已尝试：{'; '.join(errors)}）" if errors else ""
        raise DependencyMissingError(
            "pypdfium2 / PyMuPDF / pdftoppm",
            why="PDF 转图片需要任意一个渲染后端" + detail,
            install="pip install pypdfium2　（或 pip install PyMuPDF，或安装 Poppler）",
        )

    page_numbers = _parse_pages(pages_spec, len(images))
    written: list[Path] = []
    dst.parent.mkdir(parents=True, exist_ok=True)

    if len(page_numbers) <= 1:
        page = page_numbers[0] if page_numbers else 0
        _save_image(images[page], dst, dst_fmt, opts)
        return dst

    for page in page_numbers:
        out = _tmppng(dst, page + 1)
        _save_image(images[page], out, dst_fmt, opts)
        written.append(out)
    return written[0]


def _save_image(png_bytes: bytes, dst: Path, dst_fmt: str, opts) -> None:
    if dst_fmt == "png":
        dst.write_bytes(png_bytes)
        return
    need_dep("PIL", why=f"PDF → {dst_fmt} 需要 Pillow 做二次编码")
    from PIL import Image

    im = Image.open(io_buffer(png_bytes)).convert("RGB")
    im.save(dst, quality=opt_int(opts, "quality", 90))


# =========================================================================== #
# 图片 → PDF
# =========================================================================== #
def images_to_pdf(files: list[str | Path], dst: str | Path, *, quality: int = 90) -> Path:
    """一张或多张图片合成一个 PDF（多张 = 多页）。"""
    need_dep("PIL", why="图片转 PDF 需要 Pillow")
    from PIL import Image

    dst = Path(dst)
    images = []
    for f in files:
        im = Image.open(f)
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        images.append(im)
    if not images:
        raise ConversionError("没有可转换的图片")
    dst.parent.mkdir(parents=True, exist_ok=True)
    first, rest = images[0], images[1:]
    first.save(dst, "PDF", save_all=bool(rest), append_images=rest,
               quality=quality)
    return dst


def image_to_pdf(src: Path, dst: Path, opts) -> Path:
    return images_to_pdf([src], dst, quality=opt_int(opts, "quality", 90))


# =========================================================================== #
# 注册
# =========================================================================== #
def register_pdf_converters() -> int:
    count = 0

    REGISTRY.add("pdf", "txt", pdf_to_text, group="pdf",
                 note="多后端文本抽取（pypdf/PyMuPDF/poppler）")
    count += 1

    for df in _RASTER_TARGETS:
        def make(df=df):
            def conv(src: Path, dst: Path, opts) -> Path:
                return pdf_to_image(src, dst, opts, df)
            conv.__name__ = f"pdf_to_{df}"
            return conv

        REGISTRY.add("pdf", df, make(), group="pdf",
                     note="多后端页面渲染（pypdfium2/PyMuPDF/poppler）")
        count += 1

    for sf in ("png", "jpg", "webp", "bmp", "tiff", "gif"):
        REGISTRY.add(sf, "pdf", image_to_pdf, deps=("PIL",), group="pdf",
                     note="Pillow 生成 PDF")
        count += 1

    return count
