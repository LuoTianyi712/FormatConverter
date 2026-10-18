"""图片转换。

三套后端策略：

* **Pillow**（首选）：光栅图互转、缩放、质量、动画帧透传；
* **运行时多后端**：SVG 栅格化（cairosvg / inkscape / magick / rsvg-convert）；
* **零依赖**：光栅图 → SVG 内嵌（纯标准库）。

图片族与 PDF 的互转见 :mod:`uniconvert.converters.pdf`。
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import (
    need_any,
    need_dep,
    opt_bool,
    opt_float,
    opt_int,
    opt_str,
    read_bytes,
    run_tool,
)

#: Pillow 能读写的常见光栅格式（作为转换目标）
RASTER_FORMATS = ["jpg", "png", "webp", "bmp", "gif", "tiff", "ico", "tga", "jp2", "avif", "heic"]

#: 仅能作为源（Pillow 只读不写）
RASTER_SOURCE_ONLY = ["psd", "dds", "qoi"]

#: Pillow 保存时需要特殊处理的格式
_JPEG_LIKE = {"jpg"}

__all__ = ["convert_image", "raster_to_svg", "svg_to_raster"]


# =========================================================================== #
# Pillow 光栅互转
# =========================================================================== #
def _parse_size(spec: str) -> tuple[int | None, int | None]:
    spec = spec.strip().lower()
    if not spec:
        return None, None
    m = re.fullmatch(r"(\d*)[x×*](\d*)", spec)
    if not m:
        raise ConversionError(f"分辨率写法应为 1280x720 / 1280x / x720，收到：{spec!r}")
    w = int(m.group(1)) if m.group(1) else None
    h = int(m.group(2)) if m.group(2) else None
    if w is None and h is None:
        raise ConversionError(f"分辨率缺少数字：{spec!r}")
    return w, h


def _flatten(im, background: str):
    from PIL import Image

    bg = background or "#FFFFFF"
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        canvas = Image.new("RGB", im.size, bg)
        canvas.paste(im, mask=im.split()[-1])
        return canvas
    if im.mode not in ("RGB", "L"):
        return im.convert("RGB")
    return im


def _resize(im, size: tuple[int | None, int | None]):
    w, h = size
    if w is None and h is None:
        return im
    from PIL import Image

    cur_w, cur_h = im.size
    if w is None:
        w = max(1, round(cur_w * (h / cur_h)))
    if h is None:
        h = max(1, round(cur_h * (w / cur_w)))
    return im.resize((w, h), Image.LANCZOS)


def _rotate(im, degrees: float):
    if not degrees:
        return im
    from PIL import Image

    return im.rotate(-degrees, expand=True, resample=Image.BICUBIC,
                     fillcolor=(255, 255, 255))


def _frames_of(im, keep_animation: bool):
    from PIL import ImageSequence

    if not keep_animation or not getattr(im, "is_animated", False):
        return [im.copy()]
    return [frame.copy() for frame in ImageSequence.Iterator(im)]


def convert_image(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    need_dep("PIL", why="图片转换需要 Pillow")
    from PIL import Image

    try:
        im = Image.open(src)
    except Exception as exc:  # noqa: BLE001 - PIL 异常类型杂
        raise ConversionError(f"Pillow 无法识别该图片：{exc}") from exc

    quality = opt_int(opts, "quality", 90)
    size = _parse_size(opt_str(opts, "resize", "") or opt_str(opts, "scale", ""))
    rotate = opt_float(opts, "rotate", 0.0)
    grayscale = opt_bool(opts, "grayscale", False)
    background = opt_str(opts, "background", "")
    animated_target = dst_fmt in ("gif", "webp") and getattr(im, "is_animated", False)
    keep_animation = animated_target and opt_bool(opts, "all_frames", True) \
        and opt_str(opts, "time", "") == ""

    frames = _frames_of(im, keep_animation)
    processed = []
    for frame in frames:
        if grayscale:
            frame = frame.convert("L")
        if dst_fmt in _JPEG_LIKE or dst_fmt in ("bmp", "tga"):
            frame = _flatten(frame, background)
        elif dst_fmt in ("png", "webp", "tiff", "gif", "ico"):
            if frame.mode == "CMYK":
                frame = frame.convert("RGB")
            elif frame.mode == "P" and dst_fmt != "gif":
                frame = frame.convert("RGBA")
        frame = _resize(frame, size)
        frame = _rotate(frame, rotate)
        processed.append(frame)

    save_kwargs: dict = {}
    if dst_fmt in _JPEG_LIKE:
        save_kwargs.update(quality=quality,
                           optimize=opt_bool(opts, "optimize", False),
                           progressive=opt_bool(opts, "progressive", False))
        if opt_bool(opts, "exif", True):
            exif = getattr(im, "info", {}).get("exif")
            if exif:
                save_kwargs["exif"] = exif
    elif dst_fmt == "png":
        save_kwargs["compress_level"] = max(0, min(9, opt_int(opts, "compress_level", 6)))
    elif dst_fmt == "webp":
        save_kwargs.update(quality=quality, method=opt_int(opts, "method", 4),
                           lossless=opt_bool(opts, "lossless", False))
    elif dst_fmt == "tiff":
        save_kwargs["compression"] = opt_str(opts, "compression", "tiff_lzw")
    elif dst_fmt == "ico":
        sizes = opt_str(opts, "sizes", "")
        save_kwargs["sizes"] = [(256, 256)] if not sizes else [
            (int(s), int(s)) for s in re.findall(r"\d+", sizes)
        ]
    elif dst_fmt == "gif":
        save_kwargs["optimize"] = opt_bool(opts, "optimize", True)
    elif dst_fmt == "avif":
        save_kwargs["quality"] = quality
    elif dst_fmt == "heic":
        save_kwargs["quality"] = quality

    if len(processed) > 1:
        save_kwargs.update(save_all=True, append_images=processed[1:],
                           duration=getattr(im, "info", {}).get("duration", 100),
                           loop=getattr(im, "info", {}).get("loop", 0))

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if len(processed) == 1:
            processed[0].save(dst, **save_kwargs)
        else:
            processed[0].save(dst, **save_kwargs)
    except (KeyError, OSError, ValueError) as exc:
        raise ConversionError(
            f"Pillow 无法写出 {dst_fmt}（可能是该版本缺少对应插件）：{exc}"
        ) from exc
    return dst


# =========================================================================== #
# SVG → 光栅（多后端）
# =========================================================================== #
def svg_to_raster(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    width = opt_int(opts, "width", 0)
    height = opt_int(opts, "height", 0)
    scale = opt_float(opts, "scale", 0.0)

    # 后端 1：cairosvg（Python 包）
    try:
        import cairosvg  # type: ignore

        png_bytes = cairosvg.svg2png(
            url=str(src),
            output_width=width or None,
            output_height=height or None,
            scale=scale or None,
        )
        if dst_fmt == "png":
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(png_bytes)
            return dst
        # 其它格式：先转 PNG，再交给 Pillow
        need_dep("PIL", why="SVG → {} 需要 Pillow 做二次编码".format(dst_fmt))
        from PIL import Image

        im = Image.open(io.BytesIO(png_bytes))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGBA" if dst_fmt != "jpg" else "RGB").save(
            dst, quality=opt_int(opts, "quality", 90))
        return dst
    except ImportError:
        pass

    # 后端 2~4：外部命令
    backend = None
    for name, check in (("inkscape", None), ("magick", None), ("rsvg-convert", None)):
        from ..core.deps import has_dep

        if has_dep(name):
            backend = name
            break

    if backend is None:
        need_any(["py:cairosvg", "inkscape", "magick", "rsvg-convert"],
                 why="SVG 栅格化需要一个渲染后端", feature="SVG 转换")

    tmp_png = dst.with_suffix(".svgconv.png")
    if backend == "inkscape":
        run_tool(["inkscape", str(src), "--export-type=png",
                  f"--export-filename={tmp_png}",
                  *( [f"--export-width={width}"] if width else [] ),
                  *( [f"--export-height={height}"] if height else [] )],
                 timeout=300)
    elif backend == "magick":
        run_tool(["magick", "-background", "none", str(src),
                  *( [f"-resize", f"{width or ''}x{height or ''}"] if (width or height) else [] ),
                  str(tmp_png)], timeout=300)
    else:  # rsvg-convert
        run_tool(["rsvg-convert", "-o", str(tmp_png), str(src),
                  *( ["-w", str(width)] if width else [] ),
                  *( ["-h", str(height)] if height else [] )], timeout=300)

    try:
        if dst_fmt == "png":
            import shutil as _shutil

            dst.parent.mkdir(parents=True, exist_ok=True)
            _shutil.move(str(tmp_png), str(dst))
            return dst
        need_dep("PIL", why=f"SVG → {dst_fmt} 需要 Pillow 做二次编码")
        from PIL import Image

        im = Image.open(tmp_png)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGBA" if dst_fmt != "jpg" else "RGB").save(
            dst, quality=opt_int(opts, "quality", 90))
        return dst
    finally:
        tmp_png.unlink(missing_ok=True)


# =========================================================================== #
# 光栅 → SVG（零依赖内嵌）
# =========================================================================== #
def raster_to_svg(src: Path, dst: Path, opts) -> Path:
    data = read_bytes(src)
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff", ".avif": "image/avif",
    }.get(src.suffix.lower(), "image/png")

    width = opt_int(opts, "width", 0)
    height = opt_int(opts, "height", 0)
    if (not width or not height):
        try:
            import struct

            if src.suffix.lower() == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", data[16:24])
            elif src.suffix.lower() in (".jpg", ".jpeg"):
                width, height = _jpeg_size(data)
        except Exception:  # noqa: BLE001
            pass
    width = width or 1024
    height = height or 768

    b64 = base64.b64encode(data).decode("ascii")
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <image width="{width}" height="{height}" '
        f'xlink:href="data:{mime};base64,{b64}"/>\n'
        "</svg>\n"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(svg, encoding="utf-8")
    return dst


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    while i < len(data) - 9:
        marker = data[i:i + 2]
        if marker[0] != 0xFF:
            i += 1
            continue
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                         0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        i += 2 + size
    raise ValueError("不是有效 JPEG")


# =========================================================================== #
# 注册
# =========================================================================== #
def register_image_converters() -> int:
    count = 0
    sources = RASTER_FORMATS + RASTER_SOURCE_ONLY

    for sf in sources:
        for df in RASTER_FORMATS:
            if sf == df:
                continue
            deps: tuple[str, ...] = ("PIL",)

            def make(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_image(src, dst, opts, df)
                conv.__name__ = f"img_{df}"
                return conv

            REGISTRY.add(
                sf, df, make(),
                deps=deps,
                group="image",
                weight=1.0, quality=0.9,
                note="Pillow 重编码",
            )
            count += 1

    # ---- SVG ----
    for df in ("png", "jpg", "webp"):
        def make_svg(df=df):
            def conv(src: Path, dst: Path, opts) -> Path:
                return svg_to_raster(src, dst, opts, df)
            conv.__name__ = f"svg_to_{df}"
            return conv

        REGISTRY.add("svg", df, make_svg(), group="image",
                     note="SVG 栅格化（cairosvg/inkscape/magick/rsvg）")
        count += 1

    for sf in RASTER_FORMATS + ["gif"]:
        if sf == "svg":
            continue

        def conv_embed(src: Path, dst: Path, opts) -> Path:
            return raster_to_svg(src, dst, opts)

        conv_embed.__name__ = f"{sf}_to_svg"
        REGISTRY.add(sf, "svg", conv_embed, group="image",
                     note="图片内嵌进 SVG（零依赖）")
        count += 1

    return count
