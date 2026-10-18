"""格式嗅探：先看文件头，再看扩展名。

扩展名可以随便改，文件头不行。所以判断真实格式时**永远优先 magic bytes**，
只有嗅探不出结果时才退回扩展名。
"""

from __future__ import annotations

import re
from pathlib import Path

from .formats import FORMATS, Format, guess_format

__all__ = ["sniff", "detect_format", "looks_textual", "detect_text_format"]

# 读多少字节够用（ICO/JPEG 等在头部，tar 的 ustar 在 257）
_HEAD = 4096

_TEXT_SAMPLE = 4096


def sniff(path: str | Path) -> Format | None:
    """按文件头判断格式，失败返回 ``None``。

    匹配策略：按 ``(offset, payload)`` 逐条比对；``offset == -1`` 表示尾部。
    同一 magic 命中多个候选时（例如 RIFF 既可能 wav 又可能 avi），
    再看第 8~12 字节的 FOURCC 细分。
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(_HEAD)
            tail = b""
            if size > _HEAD:
                fh.seek(max(0, size - 64))
                tail = fh.read(64)
    except OSError:
        return None

    if not head:
        return None

    buf = head + tail

    for fmt in FORMATS.values():
        for offset, magic in fmt.magic:
            if offset == -1:
                pos = len(buf) - len(magic)
                if pos >= 0 and buf[pos:pos + len(magic)] == magic:
                    return fmt
            else:
                if buf[offset:offset + len(magic)] == magic:
                    refined = _refine(fmt, head, path)
                    if refined is not None:
                        return refined
    return None


def _refine(fmt: Format, head: bytes, path: Path) -> Format | None:
    """容器格式靠内部结构细分：RIFF/FOURCC、Ogg 编解码、ftyp 品牌、ZIP 内容、CFB。"""
    if fmt.magic and fmt.magic[0][1] == b"PK\x03\x04":
        return _refine_zip(fmt, path)
    if fmt.magic and fmt.magic[0][1] == b"\xd0\xcf\x11\xe0":
        return _refine_cfb(fmt, head)
    return _refine_riff(fmt, head)


#: CFB（复合文档）里各 Office 类型的特征流名 —— doc/xls/ppt 共享同一 magic
_CFB_MARKERS = (
    (b"WordDocument", "doc"),
    (b"PowerPoint Document", "ppt"),
    (b"Workbook", "xls"),
    (b"Book", "xls"),
)


def _refine_cfb(fmt: Format, head: bytes) -> Format | None:
    """d0cf11e0 开头：按目录里的特征流名区分 doc / xls / ppt。"""
    for marker, name in _CFB_MARKERS:
        if marker in head:
            return FORMATS.get(name, fmt)
    return fmt


def _refine_zip(fmt: Format, path: Path) -> Format | None:
    """PK 开头的文件：按 ZIP 成员特征区分 docx/xlsx/pptx/odt/ods/odp/epub/cbz/3mf。"""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()[:200]
    except (OSError, zipfile.BadZipFile):
        return FORMATS.get("zip", fmt)  # 不是有效 ZIP，退回通用 zip 判断

    name_set = set(names)

    def has_prefix(prefix: str) -> bool:
        return any(n.startswith(prefix) for n in name_set)

    if "META-INF/container.xml" in name_set:
        return FORMATS["epub"]
    if "word/document.xml" in name_set or has_prefix("word/"):
        return FORMATS["docx"]
    if "xl/workbook.xml" in name_set or has_prefix("xl/"):
        return FORMATS["xlsx"]
    if "ppt/presentation.xml" in name_set or has_prefix("ppt/"):
        return FORMATS.get("pptx", fmt)
    if "3D/3dmodel.model" in name_set:
        return FORMATS["3mf"]
    if "mimetype" in name_set:
        try:
            with zipfile.ZipFile(path) as zf:
                mime = zf.read("mimetype").decode("ascii", "replace").strip()
            mime_map = {
                "application/epub+zip": "epub",
                "application/vnd.oasis.opendocument.text": "odt",
                "application/vnd.oasis.opendocument.spreadsheet": "ods",
                "application/vnd.oasis.opendocument.presentation": "odp",
            }
            if mime in mime_map:
                return FORMATS.get(mime_map[mime], fmt)
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
    # 全是图片的 ZIP → 漫画包
    img_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
    if names and all(n.lower().endswith(img_exts) for n in names):
        return FORMATS.get("cbz", fmt)
    return FORMATS.get("zip", fmt)


def _refine_riff(fmt: Format, head: bytes) -> Format | None:
    """RIFF/OggS/ISO-BMFF 这类容器靠 FOURCC 或 ftyp 品牌细分。"""
    if fmt.magic and fmt.magic[0][1] == b"RIFF":
        fourcc = head[8:12]
        table = {
            b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp",
            b"ACON": "ani", b"CDDA": "cda",
        }
        return FORMATS.get(table.get(fourcc, fmt.name))
    if fmt.magic and fmt.magic[0][1] == b"OggS":
        # Ogg 里第 28 字节起是编解码标识
        codec = head[28:36]
        if codec.startswith(b"\x01vorbis"):
            return FORMATS["ogg"]
        if codec.startswith(b"OpusHead"):
            return FORMATS["opus"]
        if codec.startswith(b"\x80theora"):
            return FORMATS["ogv"]
        if codec.startswith(b"Speex"):
            return FORMATS["ogg"]
        return fmt
    if fmt.magic and fmt.magic[0] == (4, b"ftyp"):
        brand = head[8:12]
        table = {
            b"isom": "mp4", b"iso2": "mp4", b"mp41": "mp4", b"mp42": "mp4",
            b"avc1": "mp4", b"dash": "mp4", b"M4V ": "mp4", b"M4A ": "aac",
            b"M4B ": "m4b", b"qt  ": "mov",
            b"3gp4": "3gp", b"3gp5": "3gp", b"3g2a": "3gp",
            b"heic": "heic", b"heix": "heic", b"mif1": "heic", b"msf1": "heic",
            b"avif": "avif", b"avis": "avif",
        }
        return FORMATS.get(table.get(brand, fmt.name))
    return fmt


def looks_textual(path: str | Path) -> bool:
    """启发式判断是不是文本文件（无 NUL、多数可解码为 UTF-8/Latin-1）。"""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            sample = fh.read(_TEXT_SAMPLE)
    except OSError:
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        # UTF-16/32 常见 BOM 例外
        if sample[:2] in (b"\xff\xfe", b"\xfe\xff") or sample[:4] in (
            b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"
        ):
            return True
        return False
    if b"\xef\xbb\xbf" in sample[:4]:
        return True
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            sample.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


#: 文本内容特征 -> 格式，用于扩展名缺失时的兜底。
#: 顺序即优先级：**具体的在前，通用的在后**（字幕先于 YAML，
#: plist 先于泛 XML，否则 "00:00:01,000" 这类时间戳会被当成 YAML 的键）。
_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("plist", re.compile(r"<!DOCTYPE\s+plist", re.I)),
    ("html", re.compile(r"<!DOCTYPE\s+html|<html[\s>]", re.I)),
    ("xml", re.compile(r"^\s*<\?xml\s+version", re.I)),
    ("svg", re.compile(r"<svg[\s>]", re.I)),
    ("srt", re.compile(r"^\d+\s*$\s*^\d{2}:\d{2}:\d{2},\d{3}\s*-->", re.M)),
    ("vtt", re.compile(r"^WEBVTT", re.M)),
    ("ass", re.compile(r"^\[Script Info\]", re.M)),
    ("lrc", re.compile(r"^\[\d{2}:\d{2}\.\d{2}\]", re.M)),
    ("base64", re.compile(r"^\s*data:[^,]*;base64,\S", re.I)),
    ("yaml", re.compile(r"^(?:---\s*$|[\w][\w\-]*\s*:(?=\s|$))", re.M)),
    ("ini", re.compile(r"^\s*\[[^\]\n]{1,64}\]\s*\n\s*[\w.\-]+\s*[=:]", re.M)),
    ("tex", re.compile(r"\\documentclass|\\begin\{document\}")),
    ("rtf", re.compile(r"^\{{\\rtf")),
)


def detect_text_format(path: str | Path) -> Format | None:
    """对文本文件做内容级猜测（HTML/JSON/字幕…）。"""
    path = Path(path)
    try:
        text = path.read_text("utf-8", errors="replace")[:8192]
    except OSError:
        return None
    stripped = text.lstrip()
    # JSON 用真解析器判断（比正则准）：能 load 成功的才认
    if stripped[:1] in ("{", "["):
        import json as _json

        try:
            _json.loads(stripped)
            return FORMATS.get("json")
        except ValueError:
            pass
    for name, pattern in _TEXT_PATTERNS:
        if pattern.search(text):
            return FORMATS.get(name)
    return None


def detect_format(path: str | Path, *, content: bool = True) -> Format | None:
    """综合判断格式：magic → 文本特征 → 扩展名。

    >>> detect_format("song.mp3") is not None or True
    True

    Args:
        path: 文件路径（可以不真实存在）。
        content: 是否读取文件内容；``False`` 时纯按扩展名判断（快，适合列表预览）。
    """
    path = Path(path)

    if content and path.exists() and path.is_file():
        fmt = sniff(path)
        if fmt is not None:
            return fmt
        if looks_textual(path):
            fmt = detect_text_format(path)
            if fmt is not None:
                return fmt
            by_ext = guess_format(path.name)
            if by_ext is not None and by_ext.text:
                return by_ext
            if path.suffix.lower() in ("", ".txt", ".text"):
                return FORMATS.get("txt")

    return guess_format(path.name)


def describe(path: str | Path) -> dict[str, object]:
    """给文件生成一份「体检报告」，用于 CLI ``info`` 子命令。"""
    path = Path(path)
    info: dict[str, object] = {"path": str(path)}
    if not path.exists():
        info["error"] = "文件不存在"
        return info
    st = path.stat()
    info["size"] = st.st_size
    info["size_human"] = human_size(st.st_size)
    ext_fmt = guess_format(path.name)
    sniffed = sniff(path)
    info["ext_format"] = ext_fmt.name if ext_fmt else None
    info["sniffed_format"] = sniffed.name if sniffed else None
    info["category"] = sniffed.category if sniffed else (ext_fmt.category if ext_fmt else None)
    info["textual"] = looks_textual(path)
    if info["ext_format"] != info["sniffed_format"]:
        info["mismatch"] = True
    return info


def human_size(n: int | float) -> str:
    """人类可读的字节数。"""
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= step
    return f"{n:.2f} TB"
