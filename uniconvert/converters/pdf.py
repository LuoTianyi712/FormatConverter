"""PDF 相关转换（多后端自动降级）。

* ``pdf -> txt``：pypdf / PyMuPDF / pdftotext（poppler）；
* ``pdf -> png/jpg``：pypdfium2 / PyMuPDF / pdftoppm（poppler）/ magick；
* ``png/jpg -> pdf``：Pillow（多张图合并成一个 PDF 见 :func:`images_to_pdf`）。

后端全部运行时探测；一个都没有时给出明确的安装提示。
所有依赖探测只在真正执行转换时发生，路由规划阶段零开销。
"""

from __future__ import annotations

import re
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
# PDF 页面组织 / 元数据 / 长图 / 裁剪（对标简PDF工具箱 / pdftool2 / iLovePDF）
# =========================================================================== #
def _parse_order(spec: str, total: int) -> list[int]:
    """``"3,1,2"`` 或 ``"1-3,5"`` 混排 → 0 基页序列表。"""
    order: list[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a or 1), int(b or total)
            except ValueError:
                raise ConversionError(f"页序无法解析：{part!r}")
            step = 1 if end >= start else -1
            order.extend(p - 1 for p in range(start, end + step, step))
        else:
            try:
                order.append(int(part) - 1)
            except ValueError:
                raise ConversionError(f"页序无法解析：{part!r}")
    bad = [p for p in order if not 0 <= p < total]
    if bad:
        raise ConversionError(f"页序里有超出范围的页码：{[p + 1 for p in bad]}")
    return order


def pdf_pages_organize(
    src: str | Path,
    dst: str | Path,
    *,
    order: str = "",
    delete: str = "",
    reverse: bool = False,
    odd_even: str = "",
    password: str = "",
) -> Path:
    """页面组织：指定顺序 / 删除页 / 整体倒序 / 只留奇偶页。"""
    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    total = len(reader.pages)
    pages = list(range(total))

    if odd_even:
        want = odd_even.strip().lower()
        if want in ("odd", "奇", "奇数", "1"):
            pages = [p for p in pages if (p + 1) % 2 == 1]
        elif want in ("even", "偶", "偶数", "2"):
            pages = [p for p in pages if (p + 1) % 2 == 0]
        else:
            raise ConversionError(f"odd_even 只支持 odd/even，收到 {odd_even!r}")

    if delete.strip():
        removed = set(_parse_pages(delete, total))
        pages = [p for p in pages if p not in removed]

    if reverse:
        pages = list(reversed(pages))

    if order.strip():
        idx = _parse_order(order, total)
        if delete.strip():
            removed = set(_parse_pages(delete, total))
            idx = [p for p in idx if p not in removed]
        pages = idx

    if not pages:
        raise ConversionError("页面组织后没有剩下任何页")

    writer = pypdf.PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p])
    return _write_writer(writer, Path(dst))


def pdf_remove_blank(src: str | Path, dst: str | Path, *, password: str = "") -> Path:
    """删除空白页（无文字且无图片的页）。"""
    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    writer = pypdf.PdfWriter()
    removed = 0
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        try:
            has_images = bool(list(page.images))
        except Exception:  # noqa: BLE001
            has_images = False
        if not text and not has_images:
            removed += 1
            continue
        writer.add_page(page)
    if removed == 0:
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
    if len(writer.pages) == 0:
        raise ConversionError("所有页面都是空白页，不能全部删除")
    return _write_writer(writer, Path(dst))


def pdf_add_cover(
    src: str | Path,
    dst: str | Path,
    *,
    cover_image: str = "",
    cover_text: str = "",
    password: str = "",
) -> Path:
    """加封面：封面图片（等比缩放到页面大小）或大号文字标题页。"""
    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    if not reader.pages:
        raise ConversionError("PDF 没有页面")
    first = reader.pages[0]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)

    writer = pypdf.PdfWriter()
    overlay_writer = pypdf.PdfWriter()

    if cover_image:
        need_dep("PIL", why="封面图片需要 Pillow")
        from PIL import Image

        im = Image.open(cover_image)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        ratio = min(width / im.width, height / im.height)

        import tempfile

        with tempfile.TemporaryDirectory(prefix="uniconvert-cover-") as tmp:
            tmp_pdf = Path(tmp) / "cover.pdf"
            im.save(tmp_pdf, "PDF")
            img_reader = pypdf.PdfReader(str(tmp_pdf))
            cover = writer.add_blank_page(width=width, height=height)
            cover.merge_transformed_page(
                img_reader.pages[0],
                pypdf.Transformation().scale(ratio).translate(
                    (width - im.width * ratio) / 2,
                    (height - im.height * ratio) / 2,
                ),
            )
    else:
        overlay = _make_overlay_page(
            overlay_writer, width, height,
            text=cover_text or "封面", size=min(width, height) // 10,
            x=width / 2 - min(width, height) / 10 * len(cover_text or "封面") / 2,
            y=height / 2,
        )
        cover = writer.add_blank_page(width=width, height=height)
        cover.merge_page(overlay)

    for page in reader.pages:
        writer.add_page(page)
    return _write_writer(writer, Path(dst))


def pdf_metadata(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    set_values: dict | None = None,
    clean: bool = False,
    password: str = "",
) -> dict | Path:
    """读取/修改/清理 PDF 元数据。``dst=None`` 时只读取返回 dict。"""
    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    info = dict(reader.metadata or {})
    if dst is None:
        return {str(k).lstrip("/"): str(v) for k, v in info.items()}

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if clean:
        writer.add_metadata({})
    if set_values:
        writer.add_metadata({f"/{k}": str(v) for k, v in set_values.items()})
    return _write_writer(writer, Path(dst))


def pdf_crop(
    src: str | Path,
    dst: str | Path,
    *,
    margins: str = "",
    box: str = "",
    pages: str = "",
    password: str = "",
) -> Path:
    """裁剪页边距（``margins=左右,下,右?`` 取 l,t,r,b 点值）或指定 CropBox。"""
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)

    rect = None
    if box.strip():
        parts = [p for p in re.split(r"[,\s]+", box.strip()) if p]
        if len(parts) != 4:
            raise ConversionError("box 需要 4 个数字：左,下,右,上（点）")
        rect = [float(p) for p in parts]
    elif margins.strip():
        parts = [p for p in re.split(r"[,\s]+", margins.strip()) if p]
        values = [float(p) for p in parts]
        if len(values) == 1:
            l, t, r, b = values * 4
        elif len(values) == 2:
            l = r = values[0]
            t = b = values[1]
        elif len(parts) == 4:
            l, t, r, b = values
        else:
            raise ConversionError("margins 支持 1/2/4 个数字（点）")
    else:
        raise ConversionError("请给出 margins 或 box")

    wanted = set(_parse_pages(pages, len(reader.pages)))
    writer = pypdf.PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in wanted:
            mb = page.mediabox
            if rect is not None:
                new_box = rect
            else:
                new_box = [float(mb.left) + l, float(mb.bottom) + b,
                           float(mb.right) - r, float(mb.top) - t]
            if new_box[2] <= new_box[0] or new_box[3] <= new_box[1]:
                raise ConversionError("裁剪后页面尺寸为负，请减小边距")
            page[NameObject("/CropBox")] = ArrayObject(
                [FloatObject(v) for v in new_box])
        writer.add_page(page)
    return _write_writer(writer, Path(dst))


def pdf_longpic(
    src: str | Path,
    dst: str | Path,
    *,
    dpi: int = 150,
    pages: str = "",
    quality: int = 88,
    password: str = "",
) -> Path:
    """PDF → 长图：选定的页从上到下无缝拼成一张竖图（移动端分享用）。"""
    need_dep("PIL", why="长图拼接需要 Pillow")
    from PIL import Image

    data = read_bytes(src)
    images = None
    if has_dep("pypdfium2"):
        images = _render_pypdfium2(data, dpi)
    elif has_dep("fitz"):  # pragma: no cover
        images = _render_fitz(data, dpi)
    if images is None:
        raise DependencyMissingError("pypdfium2", why="长图渲染需要渲染后端",
                                     install="pip install pypdfium2")

    wanted = _parse_pages(pages, len(images))
    if not wanted:
        raise ConversionError("页范围为空")
    frames = []
    for p in wanted:
        im = Image.open(io_buffer(images[p]))
        if im.mode != "RGB":
            im = im.convert("RGB")
        frames.append(im)

    target_w = max(f.width for f in frames)
    scaled = []
    for f in frames:
        if f.width != target_w:
            ratio = target_w / f.width
            f = f.resize((target_w, max(1, int(f.height * ratio))), Image.LANCZOS)
        scaled.append(f)
    total_h = sum(f.height for f in scaled)
    if total_h > 32000:
        raise ConversionError(
            f"拼接后高度 {total_h}px 超出图片上限（32000px），"
            f"请降低 dpi、减少页数或分段转换"
        )
    canvas = Image.new("RGB", (target_w, total_h), "#FFFFFF")
    y = 0
    for f in scaled:
        canvas.paste(f, (0, y))
        y += f.height
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() == ".png":
        canvas.save(dst, "PNG")
    else:
        canvas.save(dst, "JPEG", quality=quality)
    return dst


# =========================================================================== #
# PDF 工具箱（对标 iLovePDF / Smallpdf 的核心操作）
# =========================================================================== #
def _pypdf():
    need_dep("pypdf", why="PDF 操作（合并/拆分/旋转/加密/抽图）需要 pypdf")
    import pypdf

    return pypdf


def _open_reader(src: Path, password: str = ""):
    pypdf = _pypdf()
    reader = pypdf.PdfReader(str(src))
    if reader.is_encrypted:
        if not password:
            raise ConversionError("PDF 已加密，请用 password=密码 选项提供打开密码")
        if reader.decrypt(password) == 0:
            raise ConversionError("PDF 密码错误，无法打开")
    return reader


def _write_writer(writer, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dst.open("wb") as fh:
            writer.write(fh)
    except OSError as exc:
        raise ConversionError(f"写入 PDF 失败：{exc}") from exc
    return dst


def pdf_merge(files: list[str | Path], dst: str | Path, *, password: str = "") -> Path:
    """把多个 PDF 合并为一个（按传入顺序）。"""
    pypdf = _pypdf()
    paths = [Path(f) for f in files]
    if not paths:
        raise ConversionError("没有可合并的 PDF 文件")
    writer = pypdf.PdfWriter()
    for path in paths:
        if not path.is_file():
            raise ConversionError(f"文件不存在：{path}")
        reader = _open_reader(path, password)
        for page in reader.pages:
            writer.add_page(page)
    return _write_writer(writer, Path(dst))


def pdf_split(
    src: str | Path,
    outdir: str | Path = ".",
    *,
    every: int = 0,
    ranges: str = "",
    password: str = "",
) -> list[Path]:
    """拆分 PDF。

    * ``every=N``：每 N 页一个文件；
    * ``ranges="1-3,5,7-9"``：按页范围各出一个文件；
    * 都不给：每页一个文件。
    """
    pypdf = _pypdf()
    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    reader = _open_reader(src, password)
    total = len(reader.pages)
    if total == 0:
        raise ConversionError("PDF 没有页面")

    chunks: list[list[int]] = []
    if ranges.strip():
        chunks = []
        for part in ranges.split(","):
            part = part.strip()
            if not part:
                continue
            pages = _parse_pages(part, total)
            if pages:
                chunks.append(pages)
        if not chunks:
            raise ConversionError(f"页范围无法解析：{ranges!r}")
    elif every > 0:
        chunks = [list(range(i, min(i + every, total))) for i in range(0, total, every)]
    else:
        chunks = [[i] for i in range(total)]

    written: list[Path] = []
    for chunk in chunks:
        writer = pypdf.PdfWriter()
        for page_no in chunk:
            writer.add_page(reader.pages[page_no])
        if len(chunk) == 1:
            name = f"{src.stem}-p{chunk[0] + 1:03d}.pdf"
        else:
            name = f"{src.stem}-p{chunk[0] + 1:03d}-{chunk[-1] + 1:03d}.pdf"
        written.append(_write_writer(writer, outdir / name))
    return written


def pdf_rotate(
    src: str | Path,
    dst: str | Path,
    *,
    angle: int = 90,
    pages: str = "",
    password: str = "",
) -> Path:
    """顺时针旋转页面（angle 支持 90/180/270；pages 可选）。"""
    pypdf = _pypdf()
    angle = angle % 360
    if angle not in (90, 180, 270):
        raise ConversionError(f"旋转角度只支持 90/180/270，收到 {angle}")
    reader = _open_reader(Path(src), password)
    writer = pypdf.PdfWriter()
    targets = set(_parse_pages(pages, len(reader.pages)))
    for i, page in enumerate(reader.pages):
        if i in targets:
            page.rotate(angle)
        writer.add_page(page)
    return _write_writer(writer, Path(dst))


def pdf_encrypt(
    src: str | Path,
    dst: str | Path,
    *,
    password: str,
    owner_password: str = "",
    algorithm: str = "AES-256",
    open_password: str = "",
) -> Path:
    """给 PDF 加密（设置打开密码）。

    ``password`` 是要**设置**的新密码；``open_password`` 是源 PDF 已有密码。
    """
    if not password:
        raise ConversionError("请用 password=新密码 指定要设置的打开密码")
    pypdf = _pypdf()
    reader = _open_reader(Path(src), open_password)
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    kwargs = {"owner_password": owner_password or None}
    try:
        writer.encrypt(password, algorithm=algorithm, **kwargs)
    except (TypeError, ValueError):
        writer.encrypt(password, algorithm="RC4-128", **kwargs)  # 旧版兜底
    return _write_writer(writer, Path(dst))


def pdf_decrypt(
    src: str | Path,
    dst: str | Path,
    *,
    password: str = "",
) -> Path:
    """去掉 PDF 打开密码。"""
    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    return _write_writer(writer, Path(dst))


def pdf_extract_images(
    src: str | Path,
    outdir: str | Path = ".",
    *,
    min_size: int = 0,
    password: str = "",
) -> list[Path]:
    """抽出 PDF 内嵌的所有图片（按内容去重，剔除小于 min_size 字节的图标）。"""
    import hashlib

    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    reader = _open_reader(src, password)

    seen: set[str] = set()
    written: list[Path] = []
    for page_no, page in enumerate(reader.pages, 1):
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 - 个别坏对象跳过
            continue
        for img in images:
            data = getattr(img, "data", None)
            if not data:
                continue
            if min_size and len(data) < min_size:
                continue
            digest = hashlib.sha1(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            name = getattr(img, "name", "") or "img"
            ext = Path(name).suffix.lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"):
                ext = ".png"
            out = outdir / f"{src.stem}-img{len(written) + 1:03d}-p{page_no}{ext}"
            out.write_bytes(data)
            written.append(out)
    if not written:
        raise ConversionError("该 PDF 里没有可提取的内嵌图片")
    return written


def pdf_compress(
    src: str | Path,
    dst: str | Path,
    *,
    dpi: int = 120,
    quality: int = 75,
    grayscale: bool = False,
    target: str = "",
) -> Path:
    """把 PDF 重渲染为图片再打包（有损压缩，省体积；扫描件效果最好）。

    ``target="5MB"`` 时自动搜索 dpi/质量组合压到目标大小以内。
    """
    need_dep("PIL", why="PDF 压缩需要 Pillow 做图片编码")
    from PIL import Image

    data = read_bytes(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    def render(use_dpi: int):
        images = None
        if has_dep("pypdfium2"):
            try:
                images = _render_pypdfium2(data, use_dpi)
            except Exception:  # noqa: BLE001
                images = None
        if images is None and has_dep("fitz"):  # pragma: no cover
            try:
                images = _render_fitz(data, use_dpi)
            except Exception:  # noqa: BLE001
                images = None
        if images is None:
            raise DependencyMissingError(
                "pypdfium2",
                why="PDF 压缩需要渲染后端",
                install="pip install pypdfium2",
            )
        frames = []
        for png_bytes in images:
            im = Image.open(io_buffer(png_bytes))
            if grayscale:
                im = im.convert("L")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            frames.append(im)
        return frames

    def save(frames, use_quality: int) -> None:
        first, rest = frames[0], frames[1:]
        first.save(dst, "PDF", save_all=bool(rest), append_images=rest,
                   quality=use_quality)

    if target:
        from .image import parse_size_bytes

        target_bytes = parse_size_bytes(target)
        attempts = [dpi, int(dpi * 0.8), 96, 72, 60]
        seen = set()
        best_under = False
        for use_dpi in attempts:
            if use_dpi in seen or use_dpi < 40:
                continue
            seen.add(use_dpi)
            frames = render(use_dpi)
            for use_quality in (quality, max(40, quality - 15), 40):
                save(frames, use_quality)
                if dst.stat().st_size <= target_bytes:
                    best_under = True
                    break
            if best_under:
                break
        if not best_under:
            raise ConversionError(
                f"已尝试到 dpi=60/质量40 仍压不到 {target}，"
                f"请放宽目标或先删除部分页面"
            )
        return dst

    frames = render(dpi)
    save(frames, quality)
    return dst


# =========================================================================== #
# PDF 页码 / 水印（矢量叠加，对标 Smallpdf/iLovePDF）
# =========================================================================== #
def _pypdf_gen():
    from pypdf import generic

    return generic


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _hex_text(text: str) -> str:
    """PDF 十六进制字符串（UTF-16BE），用于 CID 字体写入中文。"""
    return text.encode("utf-16-be").hex().upper()


def _measure_text(text: str, size: int) -> float:
    """粗略测量文本宽度（点）。有 PIL 用真实字体，没有就按比例估。"""
    try:
        from .image import _load_font

        font = _load_font(size)
        return float(font.getlength(text))
    except Exception:  # noqa: BLE001
        ascii_count = sum(1 for ch in text if ord(ch) < 128)
        wide = len(text) - ascii_count
        return size * (ascii_count * 0.5 + wide * 1.0)


_POS_FN = {
    "tl": lambda w, h, tw, th, m: (m, h - th - m),
    "tc": lambda w, h, tw, th, m: ((w - tw) / 2, h - th - m),
    "tr": lambda w, h, tw, th, m: (w - tw - m, h - th - m),
    "ml": lambda w, h, tw, th, m: (m, (h - th) / 2),
    "mc": lambda w, h, tw, th, m: ((w - tw) / 2, (h - th) / 2),
    "mr": lambda w, h, tw, th, m: (w - tw - m, (h - th) / 2),
    "bl": lambda w, h, tw, th, m: (m, m),
    "bc": lambda w, h, tw, th, m: ((w - tw) / 2, m),
    "br": lambda w, h, tw, th, m: (w - tw - m, m),
}

_POS_ALIASES = {
    "c": "mc", "中间": "mc",
    "左上": "tl", "top-left": "tl", "上中": "tc", "top": "tc",
    "右上": "tr", "top-right": "tr", "左中": "ml", "left": "ml",
    "居中": "mc", "center": "mc", "中": "mc", "右中": "mr", "right": "mr",
    "左下": "bl", "bottom-left": "bl", "下中": "bc", "bottom": "bc",
    "右下": "br", "bottom-right": "br",
}


def _parse_color(value: str, default=(0.2, 0.2, 0.2)) -> tuple[float, float, float]:
    value = (value or "").strip()
    if value.startswith("#") and len(value) == 7:
        try:
            return (int(value[1:3], 16) / 255, int(value[3:5], 16) / 255,
                    int(value[5:7], 16) / 255)
        except ValueError:
            pass
    named = {"black": (0, 0, 0), "white": (1, 1, 1), "red": (0.8, 0, 0),
             "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5),
             "blue": (0, 0, 0.8), "红色": (0.8, 0, 0), "灰色": (0.5, 0.5, 0.5),
             "黑色": (0, 0, 0), "白色": (1, 1, 1), "蓝色": (0, 0, 0.8)}
    return named.get(value.lower(), default)


def _make_overlay_page(writer, width: float, height: float, *, text: str,
                       size: float, x: float, y: float,
                       color=(0.2, 0.2, 0.2), alpha: float | None = None,
                       rotate: float = 0.0):
    """构造一个只含一段文字的叠加页（矢量）。"""
    import math

    g = _pypdf_gen()
    NameObject = g.NameObject
    DictionaryObject = g.DictionaryObject
    NumberObject = g.NumberObject
    FloatObject = g.FloatObject
    DecodedStreamObject = g.DecodedStreamObject

    page = writer.add_blank_page(width=float(width), height=float(height))
    if "/Resources" not in page:
        page[NameObject("/Resources")] = DictionaryObject()
    resources = page["/Resources"]

    is_ascii = all(ord(ch) < 128 for ch in text)
    fonts = DictionaryObject()
    helv = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fonts[NameObject("/F1")] = writer._add_object(helv)
    if not is_ascii:
        # 标准 CJK 字体（无需嵌入，Acrobat/Chrome/Edge 均支持）
        cid_info = DictionaryObject({
            NameObject("/Registry"): g.TextStringObject("Adobe"),
            NameObject("/Ordering"): g.TextStringObject("GB1"),
            NameObject("/Supplement"): NumberObject(4),
        })
        descendant = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): cid_info,
        })
        cjk = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): g.ArrayObject(
                [writer._add_object(descendant)]),
        })
        fonts[NameObject("/F2")] = writer._add_object(cjk)
    resources[NameObject("/Font")] = fonts

    gs_name = ""
    if alpha is not None and alpha < 1.0:
        gs = DictionaryObject({
            NameObject("/Type"): NameObject("/ExtGState"),
            # 注意：pypdf 6 的 NumberObject 会把浮点截断成 0，透明度必须用 FloatObject
            NameObject("/ca"): FloatObject(round(alpha, 3)),
            NameObject("/CA"): FloatObject(round(alpha, 3)),
        })
        egs = DictionaryObject()
        egs[NameObject("/GS")] = writer._add_object(gs)
        resources[NameObject("/ExtGState")] = egs
        gs_name = "/GS gs"

    ops: list[str] = ["q"]
    if gs_name:
        ops.append(gs_name)
    r, gg, b = color
    ops.append(f"{r:.3f} {gg:.3f} {b:.3f} rg")
    if rotate:
        rad = math.radians(rotate)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        ops.append(f"{cos_a:.5f} {sin_a:.5f} {-sin_a:.5f} {cos_a:.5f} {x:.2f} {y:.2f} cm")
        tx = ty = 0.0
    else:
        tx, ty = x, y
    if is_ascii:
        ops.append(f"BT /F1 {size:.2f} Tf {tx:.2f} {ty:.2f} Td "
                   f"({_escape_pdf_text(text)}) Tj ET")
    else:
        ops.append(f"BT /F2 {size:.2f} Tf {tx:.2f} {ty:.2f} Td "
                   f"<{_hex_text(text)}> Tj ET")
    ops.append("Q")

    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def _stamp_pages(src: Path, dst: Path, *, pages: str, password: str,
                 make_overlay) -> Path:
    """通用盖章：页先挂到 writer，再对指定页叠加 overlay。

    ``make_overlay(overlay_writer, width, height, index)`` 返回叠加页；
    overlay 使用**独立的 writer**构造，避免它们混进输出页序列。
    """
    pypdf = _pypdf()
    reader = _open_reader(src, password)
    total = len(reader.pages)
    wanted = set(_parse_pages(pages, total))

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    overlay_writer = pypdf.PdfWriter()
    for i, page in enumerate(writer.pages):
        if i in wanted:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            overlay = make_overlay(overlay_writer, width, height, i)
            if overlay is not None:
                page.merge_page(overlay)
    return _write_writer(writer, dst)


def pdf_add_page_numbers(
    src: str | Path,
    dst: str | Path,
    *,
    start: int = 1,
    pos: str = "bc",
    size: int = 10,
    margin: int = 28,
    template: str = "{n} / {total}",
    pages: str = "",
    color: str = "#444444",
    password: str = "",
) -> Path:
    """给 PDF 加页码（矢量文字，支持任意位置/起始页/页范围）。"""
    pos = _POS_ALIASES.get(pos.lower(), pos.lower())
    if pos not in _POS_FN:
        raise ConversionError(f"位置应为 tl/tc/tr/ml/mc/mr/bl/bc/br，收到 {pos!r}")
    rgb = _parse_color(color, (0.27, 0.27, 0.27))

    pypdf = _pypdf()
    reader = _open_reader(Path(src), password)
    total = len(reader.pages)
    wanted = set(_parse_pages(pages, total))

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    overlay_writer = pypdf.PdfWriter()
    for i, page in enumerate(writer.pages):
        if i in wanted:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            text = (template or "{n}").replace("{n}", str(start + i)).replace(
                "{total}", str(total))
            tw = _measure_text(text, size)
            th = size * 1.15
            x, y = _POS_FN[pos](width, height, tw, th, margin)
            overlay = _make_overlay_page(
                overlay_writer, width, height, text=text, size=size,
                x=max(0, x), y=max(0, y), color=rgb)
            page.merge_page(overlay)
    return _write_writer(writer, Path(dst))


def pdf_add_watermark(
    src: str | Path,
    dst: str | Path,
    *,
    text: str = "",
    size: int = 0,
    opacity: int = 15,
    rotate: float = 45.0,
    pos: str = "c",
    color: str = "#808080",
    pages: str = "",
    password: str = "",
) -> Path:
    """给 PDF 加水印（矢量文字；中文用标准 CJK 字体，透明度可调）。"""
    if not text:
        raise ConversionError("请用 text=水印文字 指定水印内容")
    pos = _POS_ALIASES.get(pos.lower(), pos.lower())
    if pos not in _POS_FN:
        raise ConversionError(f"位置应为 tl/tc/tr/ml/mc/mr/bl/bc/br，收到 {pos!r}")
    rgb = _parse_color(color, (0.5, 0.5, 0.5))
    alpha = max(0.01, min(1.0, opacity / 100))

    def make(overlay_writer, width: float, height: float, _i: int):
        wm_size = size or max(18, int(min(width, height) / 8))
        tw = _measure_text(text, wm_size)
        th = wm_size * 1.2
        x, y = _POS_FN[pos](width, height, tw, th, 0)
        if rotate:
            import math

            rad = math.radians(rotate)
            # 旋转会让包围盒变化，重新居中
            x = (width - (tw * abs(math.cos(rad)) + th * abs(math.sin(rad)))) / 2
            y = (height - (tw * abs(math.sin(rad)) + th * abs(math.cos(rad)))) / 2
        return _make_overlay_page(
            overlay_writer, width, height, text=text, size=wm_size,
            x=max(0, x), y=max(0, y), color=rgb, alpha=alpha,
            rotate=0.0 if not rotate else rotate,
        )

    return _stamp_pages(Path(src), Path(dst), pages=pages, password=password,
                        make_overlay=make)


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
