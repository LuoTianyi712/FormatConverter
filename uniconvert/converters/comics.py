"""漫画包 ⇄ PDF（cbz / cbr / cbt / cb7 → pdf，pdf → cbz）。

对标 SumatraPDF / useotools / cb2pdf 的漫画转换：

* **→ PDF**：按文件名自然序（2 在 10 前）逐图一页；跳过隐藏文件、
  `__MACOSX`、`.DS_Store`、`Thumbs.db`、`ComicInfo.xml`；
  `rtl=true` 反转阅读顺序（日漫从右往左）；
* **PDF → CBZ**：逐页渲染为 JPEG（dpi/质量可调）+ `ComicInfo.xml`。

rar/7z 容器复用压缩包模块的多后端（rarfile / py7zr / 7z.exe）。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import opt_bool, opt_int, opt_str, read_bytes
from .archive import _iter_7z, _iter_rar, _iter_tar, _iter_zip

COMIC_FORMATS = ["cbz", "cbr", "cbt", "cb7"]
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
               ".tif", ".tiff", ".avif", ".jxl")
_SKIP_NAMES = {"comicinfo.xml", "thumbs.db", ".ds_store", "desktop.ini"}
_SKIP_PARTS = ("__macosx", ".git", ".svn")
_NUM_RE = re.compile(r"(\d+)")


def _natural_key(name: str):
    return [int(tok) if tok.isdigit() else tok.lower()
            for tok in _NUM_RE.split(name)]


def _comic_members(src: Path, fmt: str):
    """按阅读顺序产出 (名字, 字节) 的图片成员。"""
    readers = {"cbz": _iter_zip, "cbt": _iter_tar,
               "cbr": _iter_rar, "cb7": _iter_7z}
    reader = readers.get(fmt)
    if reader is None:  # pragma: no cover
        raise ConversionError(f"不支持的漫画容器：{fmt}")
    members = []
    for m in reader(src):
        if m.is_dir:
            continue
        name = m.name.replace("\\", "/")
        low = name.lower()
        if any(part in low for part in _SKIP_PARTS):
            continue
        base = low.rsplit("/", 1)[-1]
        if base in _SKIP_NAMES or base.startswith("._"):
            continue
        if not low.endswith(_IMAGE_EXTS):
            continue
        members.append((name, m.read()))
    members.sort(key=lambda pair: _natural_key(pair[0]))
    if not members:
        raise ConversionError("漫画包里没有找到图片页面")
    return members


def comic_to_pdf(src: Path, dst: Path, opts, fmt: str = "") -> Path:
    """cbz/cbr/cbt/cb7 → PDF（一图一页，可选 rtl 逆序）。"""
    from ._util import need_dep

    need_dep("PIL", why="漫画转 PDF 需要 Pillow 做图片编码")
    from PIL import Image

    if not fmt:
        fmt = src.suffix.lstrip(".").lower()
    pages = _comic_members(src, fmt)
    if opt_bool(opts, "rtl", False):
        pages = list(reversed(pages))
    quality = opt_int(opts, "quality", 90)

    frames = []
    for name, data in pages:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as exc:  # noqa: BLE001 - 单页坏图不应整本失败
            raise ConversionError(f"第 {len(frames) + 1} 页图片无法解码（{name}）：{exc}") from exc
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, "#FFFFFF")
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        frames.append(im)

    dst.parent.mkdir(parents=True, exist_ok=True)
    first, rest = frames[0], frames[1:]
    first.save(dst, "PDF", save_all=bool(rest), append_images=rest, quality=quality)
    return dst


def pdf_to_cbz(src: Path, dst: Path, opts) -> Path:
    """PDF → CBZ（逐页渲染 JPEG + ComicInfo.xml）。"""
    from .pdf import _render_fitz, _render_pypdfium2

    data = read_bytes(src)
    dpi = opt_int(opts, "dpi", 150)
    quality = opt_int(opts, "quality", 85)
    pngs = None
    from ..core.deps import has_dep

    if has_dep("pypdfium2"):
        pngs = _render_pypdfium2(data, dpi)
    elif has_dep("fitz"):  # pragma: no cover
        pngs = _render_fitz(data, dpi)
    else:
        from ._util import need_any

        need_any(["pypdfium2", "fitz", "pdftoppm"],
                 why="PDF 逐页渲染需要渲染后端", feature="PDF → CBZ")

    from PIL import Image

    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        total = len(pngs)
        for i, png in enumerate(pngs, 1):
            im = Image.open(io.BytesIO(png))
            if im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            zf.writestr(f"{i:03d}.jpg", buf.getvalue())
        comic_info = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<ComicInfo>\n"
            f"  <Title>{src.stem}</Title>\n"
            f"  <PageCount>{total}</PageCount>\n"
            "</ComicInfo>\n"
        )
        zf.writestr("ComicInfo.xml", comic_info)
    return dst


def register_comic_converters() -> int:
    count = 0
    for sf in COMIC_FORMATS:
        def make(sf=sf):
            def conv(src: Path, dst: Path, opts) -> Path:
                return comic_to_pdf(src, dst, opts, sf)
            conv.__name__ = f"comic_{sf}_to_pdf"
            return conv

        REGISTRY.add(sf, "pdf", make(), group="comic",
                     note="漫画一图一页转 PDF（自然序，rtl 选项）")
        count += 1

    REGISTRY.add("pdf", "cbz", pdf_to_cbz, group="comic",
                 note="逐页渲染 JPEG + ComicInfo.xml")
    count += 1
    return count
