"""字体转换（fontTools 后端）。

WOFF/WOFF2 本质是 TTF/OTF 的压缩包装，
所以常见需求（网页字体 ⇄ 桌面字体）可以无损互转。
TTF ⇄ OTF（轮廓曲线转换）不在支持范围内（需要额外算法）。

另提供**子集化**（``font_subset``）：只保留需要的字符，
网页字体体积可缩小 90%+（对标 XnView/Filee 的字体子集功能）。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import need_dep, opt_bool, opt_int, opt_str

#: 同一轮廓族内的互转组合
FONT_FORMATS = ["ttf", "otf", "woff", "woff2"]


def convert_font(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    need_dep("fontTools", why="字体转换需要 fontTools")
    if dst_fmt in ("woff2",):
        need_dep("brotli", why="WOFF2 压缩需要 brotli 模块")
    from fontTools.ttLib import TTFont

    try:
        font = TTFont(str(src), lazy=False)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"fontTools 无法解析该字体：{exc}") from exc

    flavor = {"woff": "woff", "woff2": "woff2"}.get(dst_fmt)
    kwargs: dict = {"flavor": flavor} if flavor else {}
    if flavor == "woff":
        kwargs["woffVersion"] = opt_int(opts, "woff_version", 1)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        font.save(str(dst), **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"写出 {dst_fmt} 失败：{exc}") from exc
    finally:
        font.close()
    return dst


def font_subset(src: str | Path, dst: str | Path, *, text: str = "",
                text_file: str | Path | None = None,
                unicodes: str = "", keep_layout: bool = True,
                options=None, **opts) -> Path:
    """字体子集化：只保留指定字符，体积可缩 90%+。

    * ``text``：保留的字符全集（如网页用到的字）；
    * ``text_file``：从文本文件读取字符（自动去重）；
    * ``unicodes``：``"U+4E00-9FFF,U+0020-007E"`` 形式的区间；
    * 都不给：默认 ASCII + 常用标点 + 数字（最小可用集）。
    """
    from ..core.job import Options

    options = Options.from_any(options)
    options.update(opts)
    need_dep("fontTools", why="字体子集化需要 fontTools")

    from fontTools import subset as _subset

    char_text = text or ""
    if text_file is not None:
        from ._util import read_text

        char_text += read_text(Path(text_file), options)

    unicode_list: list[int] = []
    if unicodes:
        for part in unicodes.split(","):
            part = part.strip().upper().replace("U+", "")
            if not part:
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                unicode_list.extend(range(int(a, 16), int(b, 16) + 1))
            else:
                unicode_list.append(int(part, 16))

    if not char_text and not unicode_list:
        char_text = "".join(chr(c) for c in range(0x20, 0x7F)) + \
            "，。！？：；“”‘’（）—…、"

    subset_opts = _subset.Options()
    subset_opts.notdef_outline = True
    subset_opts.recalc_bounds = True
    subset_opts.name_IDs = ["*"]
    subset_opts.name_languages = ["*"]
    if keep_layout:
        subset_opts.layout_features = ["*"]

    src_p, dst_p = Path(src), Path(dst)
    flavor = {"woff": "woff", "woff2": "woff2"}.get(dst_p.suffix.lower().lstrip("."))
    if flavor == "woff2":
        need_dep("brotli", why="WOFF2 输出需要 brotli")
    if flavor:
        subset_opts.flavor = flavor

    font = _subset.load_font(str(src_p), subset_opts)
    try:
        subsetter = _subset.Subsetter(options=subset_opts)
        subsetter.populate(text=char_text, unicodes=unicode_list or None)
        subsetter.subset(font)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        _subset.save_font(font, str(dst_p), subset_opts)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"字体子集化失败：{exc}") from exc
    finally:
        font.close()
    return dst_p


def register_font_converters() -> int:
    count = 0
    for sf in FONT_FORMATS:
        for df in FONT_FORMATS:
            if sf == df:
                continue

            def make(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_font(src, dst, opts, df)
                conv.__name__ = f"font_to_{df}"
                return conv

            deps = ("fontTools", "brotli") if "woff2" in (sf, df) else ("fontTools",)
            REGISTRY.add(sf, df, make(), deps=deps, group="font",
                         note="字体容器互转（无损）")
            count += 1
    return count
