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
        base_duration = getattr(im, "info", {}).get("duration", 100) or 100
        speed = opt_float(opts, "speed", 1.0) or 1.0
        save_kwargs.update(save_all=True, append_images=processed[1:],
                           duration=max(10, int(base_duration / speed)),
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
# 图片工具箱（对标格式工厂：水印 / 拼接 / 压到指定大小）
# =========================================================================== #
_POS_ALIASES = {
    "tl": "tl", "左上": "tl", "left-top": "tl",
    "tc": "tc", "上": "tc", "top": "tc",
    "tr": "tr", "右上": "tr", "right-top": "tr",
    "ml": "ml", "左": "ml", "left": "ml",
    "mc": "mc", "中": "mc", "center": "mc",
    "mr": "mr", "右": "mr", "right": "mr",
    "bl": "bl", "左下": "bl", "left-bottom": "bl", "bottom-left": "bl",
    "bc": "bc", "下": "bc", "bottom": "bc",
    "br": "br", "右下": "br", "right-bottom": "br", "bottom-right": "br",
}

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size)  # Pillow ≥10.1 支持尺寸
    except TypeError:  # pragma: no cover - 老 Pillow
        return ImageFont.load_default()


def _make_watermark_piece(base, opts):
    """生成水印小图（RGBA）：文字或图片。"""
    from PIL import Image, ImageDraw

    opacity = max(0, min(100, opt_int(opts, "opacity", 40)))
    alpha = int(255 * opacity / 100)
    image_path = opt_str(opts, "image", "")
    if image_path:
        try:
            piece = Image.open(image_path).convert("RGBA")
        except OSError as exc:
            raise ConversionError(f"水印图片无法打开：{exc}") from exc
        scale = opt_float(opts, "scale", 20.0)
        target_w = max(8, int(base.width * scale / 100))
        ratio = target_w / piece.width
        piece = piece.resize((target_w, max(1, int(piece.height * ratio))), Image.LANCZOS)
    else:
        text = opt_str(opts, "text", "")
        if not text:
            raise ConversionError("水印需要 text=文字 或 image=图片路径 之一")
        size = opt_int(opts, "size", 0) or max(12, base.width // 20)
        font = _load_font(size)
        pad = max(4, size // 6)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0] + pad * 2
        h = bbox[3] - bbox[1] + pad * 2
        piece = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(piece)
        color = opt_str(opts, "color", "#FFFFFF")
        stroke = opt_int(opts, "stroke", 0)
        if stroke > 0:
            draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color,
                      stroke_width=stroke, stroke_fill=opt_str(opts, "stroke_color", "#000000"))
        else:
            draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color)
    # 调透明度
    r, g, b, a = piece.split()
    a = a.point(lambda v: int(v * alpha / 255))
    piece = Image.merge("RGBA", (r, g, b, a))
    rotate = opt_float(opts, "rotate", 0.0)
    if rotate:
        piece = piece.rotate(-rotate, expand=True, resample=Image.BICUBIC)
    return piece


def image_watermark(src: Path, dst: Path, opts) -> Path:
    """给图片加水印（text=文字 / image=图片，支持平铺 tile=true）。"""
    need_dep("PIL", why="水印需要 Pillow")
    from PIL import Image

    base = Image.open(src)
    if getattr(base, "is_animated", False) and dst.suffix.lower() == ".gif":
        base = base.convert("RGBA")
    else:
        base = base.convert("RGBA")
    piece = _make_watermark_piece(base, opts)
    margin = opt_int(opts, "margin", 16)

    if opt_bool(opts, "tile", False):
        gap = opt_int(opts, "gap", 40)
        step_x = piece.width + gap
        step_y = piece.height + gap
        y = margin
        row = 0
        while y < base.height:
            x = margin + (step_x // 2 if row % 2 else 0)
            while x < base.width:
                base.alpha_composite(piece, (x, y))
                x += step_x
            y += step_y
            row += 1
    else:
        pos = _POS_ALIASES.get(opt_str(opts, "pos", "br").lower(), "br")
        x = {
            "l": margin,
            "c": (base.width - piece.width) // 2,
            "r": base.width - piece.width - margin,
        }[{"tl": "l", "ml": "l", "bl": "l", "tc": "c", "mc": "c", "bc": "c",
           "tr": "r", "mr": "r", "br": "r"}[pos]]
        y = {
            "t": margin,
            "m": (base.height - piece.height) // 2,
            "b": base.height - piece.height - margin,
        }[{"tl": "t", "tc": "t", "tr": "t", "ml": "m", "mc": "m", "mr": "m",
           "bl": "b", "bc": "b", "br": "b"}[pos]]
        base.alpha_composite(piece, (max(0, x), max(0, y)))

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst_fmt = dst.suffix.lower().lstrip(".")
    if dst_fmt in ("jpg", "jpeg", "bmp"):
        base = base.convert("RGB")
    base.save(dst, quality=opt_int(opts, "quality", 92))
    return dst


def image_collage(files: list[str | Path], dst: str | Path, opts) -> Path:
    """拼接多张图片：``mode=grid|h|v``（网格 / 横长图 / 竖长图）。"""
    need_dep("PIL", why="图片拼接需要 Pillow")
    from PIL import Image, ImageOps

    paths = [Path(f) for f in files]
    if not paths:
        raise ConversionError("没有可拼接的图片")
    images = []
    for p in paths:
        im = Image.open(p)
        if getattr(im, "is_animated", False):
            im.seek(0)
        images.append(im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im.copy())

    mode = opt_str(opts, "mode", "grid").lower()
    gap = opt_int(opts, "gap", 8)
    bg = opt_str(opts, "bg", "white") or "white"

    if mode == "h":
        cols = len(images)
    elif mode == "v":
        cols = 1
    else:
        cols = max(1, opt_int(opts, "cols", 0) or int(len(images) ** 0.5 + 0.999))
    rows = (len(images) + cols - 1) // cols

    cell_w = opt_int(opts, "cell_width", 0)
    cell_h = opt_int(opts, "cell_height", 0)
    width = opt_int(opts, "width", 0)
    if not cell_w:
        natural = min(im.width for im in images)
        cell_w = width // cols - gap if width else natural
        cell_w = max(16, cell_w)
    if not cell_h:
        cell_h = max(16, round(cell_w * min(im.height / im.width for im in images)))

    canvas_w = cols * cell_w + gap * (cols + 1)
    canvas_h = rows * cell_h + gap * (rows + 1)
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg)

    for idx, im in enumerate(images):
        row, col = divmod(idx, cols)
        fitted = ImageOps.fit(im, (cell_w, cell_h), Image.LANCZOS)
        x = gap + col * (cell_w + gap)
        y = gap + row * (cell_h + gap)
        canvas.paste(fitted, (x, y))

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, quality=opt_int(opts, "quality", 92))
    return dst


def parse_size_bytes(text: str) -> int:
    """``"500KB"/"1.5MB"/"800"`` → 字节数。"""
    import re as _re

    m = _re.fullmatch(r"\s*([\d.]+)\s*([kKmMgG]?)[bB]?\s*", text or "")
    if not m:
        raise ConversionError(f"无法解析目标大小：{text!r}（示例：500KB / 1.5MB）")
    value = float(m.group(1))
    unit = m.group(2).lower()
    factor = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit]
    return int(value * factor)


def image_compress_to_size(src: Path, dst: Path, opts) -> Path:
    """把图片压到指定大小以内（自动搜索质量，必要时逐级缩小尺寸）。

    选项：``target=500KB``（必填）、``min_quality``、``max_quality``。
    """
    need_dep("PIL", why="图片压缩需要 Pillow")
    import io as _io

    from PIL import Image

    target_raw = opt_str(opts, "target", "") or opt_str(opts, "target_size", "")
    if not target_raw:
        raise ConversionError("请用 target=500KB 指定目标大小")
    target = parse_size_bytes(target_raw)
    dst_fmt = dst.suffix.lower().lstrip(".") or "jpg"
    if dst_fmt in ("jpeg",):
        dst_fmt = "jpg"
    if dst_fmt not in ("jpg", "webp"):
        raise ConversionError(
            f"『压到指定大小』只支持 jpg/webp 目标（{dst_fmt} 不支持质量参数），"
            f"可先转换格式：convert 图片.png 图片.jpg"
        )
    min_q = max(5, opt_int(opts, "min_quality", 25))
    max_q = min(95, opt_int(opts, "max_quality", 92))

    im = Image.open(src)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    def encode(img, quality: int) -> bytes:
        buf = _io.BytesIO()
        kwargs = {"quality": quality}
        if dst_fmt == "webp":
            kwargs["method"] = 4
        img.save(buf, dst_fmt.upper().replace("JPG", "JPEG"), **kwargs)
        return buf.getvalue()

    scale = 1.0
    best: bytes | None = None
    for _attempt in range(8):
        lo, hi = min_q, max_q
        found: bytes | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            data = encode(im, mid)
            if len(data) <= target:
                found = data
                lo = mid + 1
            else:
                hi = mid - 1
        if found is not None:
            best = found
            break
        # 质量降到最低还超标 → 缩小尺寸再来一轮
        new_w = max(16, int(im.width * 0.85))
        new_h = max(16, int(im.height * 0.85))
        if (new_w, new_h) == im.size:
            break
        im = im.resize((new_w, new_h), Image.LANCZOS)
        scale *= 0.85
    if best is None:
        best = encode(im, min_q)
        if len(best) > target:
            raise ConversionError(
                f"已缩到 {im.width}x{im.height}、质量 {min_q} 仍超过目标大小，"
                f"请放宽 target 或先裁剪图片"
            )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(best)
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

    # ---- 自环：同格式重压缩（改质量/尺寸/动图速度）----
    for fmt in ("jpg", "png", "webp", "gif"):
        def make_self(fmt=fmt):
            def conv(src: Path, dst: Path, opts) -> Path:
                return convert_image(src, dst, opts, fmt)
            conv.__name__ = f"img_recompress_{fmt}"
            return conv

        REGISTRY.add(fmt, fmt, make_self(), deps=("PIL",), group="image",
                     allow_self=True, note="重压缩/改质量/尺寸/动图速度")
        count += 1

    return count
