"""内置转换器集合。

导入本包即完成全部内置转换器的注册（幂等：重复导入不会重复注册）。
外部插件走 :mod:`uniconvert.plugins`。

分组一览：

* ``data``     —— JSON/YAML/TOML/XML/INI/plist/JSONL/CSV/TSV 全连通
* ``text``     —— 编码转换、txt 换编码
* ``markdown`` —— md/html/txt 互转
* ``codec``    —— base64/hex/urlencode/quoted-printable/rot13
* ``hash``     —— 任意格式 → 摘要
* ``subtitle`` —— srt/ass/ssa/vtt/lrc 全连通
* ``archive``  —— zip/tar/7z/cbz/rar 重打包 + gz/bz2/xz/zst 压缩流
* ``document`` —— docx/odt/pptx/xlsx/ods/epub/fb2 零依赖解析
* ``office``   —— LibreOffice 增强（doc/ppt/xls ↔ pdf 等）
* ``pdf``      —— PDF 文本/图像抽取、图片生成 PDF、完整工具箱
* ``image``    —— Pillow 图片互转、水印/拼接/压到指定大小、SVG
* ``ffmpeg``   —— 音视频/字幕轨/GIF
* ``font``     —— ttf/otf/woff/woff2
* ``3d``       —— stl/obj/ply 三角面互转
* ``contacts`` —— vCard(vcf) ⇄ CSV/JSON
"""

from __future__ import annotations

from ..core.registry import REGISTRY

__all__ = ["load_builtin", "registered_counts"]

_LOADED = False


def load_builtin() -> dict[str, int]:
    """注册全部内置转换器（幂等）。返回各模块注册的边数。"""
    global _LOADED
    counts: dict[str, int] = {}
    if _LOADED:
        for group in REGISTRY.groups():
            counts[group] = len(REGISTRY.group(group))
        return counts

    from . import (
        archive,
        comics,
        contacts,
        data,
        document,
        font,
        image,
        mailfmt,
        media,
        ocr,
        pdf,
        speech,
        subtitle,
        text,
        three_d,
    )

    counts["data"] = data.register_data_converters()
    counts["text"] = text.register_text_converters()
    counts["subtitle"] = subtitle.register_subtitle_converters()
    counts["archive"] = archive.register_archive_converters()
    counts["document"] = document.register_document_converters()
    counts["pdf"] = pdf.register_pdf_converters()
    counts["image"] = image.register_image_converters()
    counts["media"] = media.register_media_converters()
    counts["font"] = font.register_font_converters()
    counts["3d"] = three_d.register_three_d_converters()
    counts["contacts"] = contacts.register_contacts_converters()
    counts["ocr"] = ocr.register_ocr_converters()
    counts["comic"] = comics.register_comic_converters()
    counts["email"] = mailfmt.register_mail_converters()
    counts["speech"] = speech.register_speech_converters()

    _LOADED = True
    return counts


def registered_counts() -> dict[str, int]:
    """当前注册表里各组的边数（按 group 统计）。"""
    return REGISTRY.groups()


#: 导入即注册（幂等）
load_builtin()
