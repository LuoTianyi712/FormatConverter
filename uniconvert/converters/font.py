"""字体转换（fontTools 后端）。

WOFF/WOFF2 本质是 TTF/OTF 的压缩包装，
所以常见需求（网页字体 ⇄ 桌面字体）可以无损互转。
TTF ⇄ OTF（轮廓曲线转换）不在支持范围内（需要额外算法）。
"""

from __future__ import annotations

from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import need_dep, opt_int

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
