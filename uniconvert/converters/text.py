"""文本世界：编码转换、Base64/Hex/URL/QP、哈希摘要、Markdown/HTML/RTF。

这个模块撑起了「纯文本能到达的宇宙」：

* ``txt`` / ``md`` / ``html`` / ``rtf`` 之间的文档级互转；
* ``txt`` ⇄ ``base64`` / ``hex`` / ``urlencode`` / ``quoted-printable`` / ``rot13``；
* 任何格式 → ``hash``（md5/sha1/sha256/sha512/blake2b/crc32，支持 HMAC）；
* ``txt -> txt`` 自环 = 换编码 / 换换行符（``-o from_encoding=gb18030 -o to_encoding=utf-8``）。
"""

from __future__ import annotations

import base64 as _b64
import binascii
import codecs
import hashlib
import hmac
import html as _html
import quopri
import re
import shutil
import urllib.parse
import zlib
from html.parser import HTMLParser
from pathlib import Path

from ..core.formats import FORMATS
from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import (
    decode_text,
    is_text_bytes,
    opt_bool,
    opt_str,
    read_bytes,
    write_bytes,
)

__all__ = ["render_markdown", "html_to_markdown", "html_to_text", "rtf_to_text"]

ENCODE_FORMATS = ["base64", "hex", "urlencode", "quoted-printable"]


# =========================================================================== #
# 编码族：txt/bin ⇄ base64 / hex / urlencode / quoted-printable / rot13
# =========================================================================== #
def _apply_encode(kind: str, data: bytes) -> bytes:
    if kind == "base64":
        return _b64.b64encode(data) + b"\n"
    if kind == "hex":
        return binascii.hexlify(data, b" ") + b"\n"
    if kind == "urlencode":
        return urllib.parse.quote_from_bytes(data, safe="").encode("ascii")
    if kind == "quoted-printable":
        return quopri.encodestring(data, quotetabs=False)
    if kind == "rot13":
        text = decode_text(data, "utf-8")
        return codecs.encode(text, "rot_13").encode("utf-8")
    raise ConversionError(f"未知编码：{kind}")  # pragma: no cover


def _apply_decode(kind: str, data: bytes) -> bytes:
    if kind == "base64":
        # 支持 data URI（data:image/png;charset=...;base64,....）直接解码成文件
        cleaned = re.sub(rb"^\s*data:[^,]*;base64,", b"", data.strip(), flags=re.I)
        try:
            return _b64.b64decode(re.sub(rb"\s+", b"", cleaned), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConversionError(f"Base64 解码失败：{exc}") from exc
    if kind == "hex":
        try:
            return binascii.unhexlify(re.sub(rb"[\s,;:]+", b"", data))
        except (binascii.Error, ValueError) as exc:
            raise ConversionError(f"Hex 解码失败：{exc}") from exc
    if kind == "urlencode":
        try:
            return urllib.parse.unquote_to_bytes(data.decode("ascii", "strict"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConversionError(f"URL 解码失败：{exc}") from exc
    if kind == "quoted-printable":
        return quopri.decodestring(data)
    if kind == "rot13":
        text = decode_text(data, "utf-8")
        return codecs.encode(text, "rot_13").encode("utf-8")
    raise ConversionError(f"未知编码：{kind}")  # pragma: no cover


def _codec_converter(kind: str, encode: bool):
    def conv(src: Path, dst: Path, opts) -> Path:
        data = read_bytes(src)
        out = _apply_encode(kind, data) if encode else _apply_decode(kind, data)
        if not encode and dst.suffix.lower().lstrip(".") in (
            "txt", "text", "md", "json", "yaml", "yml", "xml", "csv", "html",
        ) and not is_text_bytes(out[:4096]):
            raise ConversionError(
                f"解码结果不是文本，无法写入 {dst.name}（试试改成 .bin 目标）"
            )
        return write_bytes(dst, out)

    conv.__name__ = f"{'encode' if encode else 'decode'}_{kind}"
    return conv


def _rot13_converter(encode: bool):
    def conv(src: Path, dst: Path, opts) -> Path:
        data = read_bytes(src)
        text = decode_text(data, "utf-8")
        return write_bytes(dst, codecs.encode(text, "rot_13").encode("utf-8"))

    conv.__name__ = "rot13"
    return conv


# =========================================================================== #
# 哈希
# =========================================================================== #
_HASHLIB_ALGOS = {"md5", "sha1", "sha224", "sha256", "sha384", "sha512",
                  "sha3_256", "sha3_512", "blake2b", "blake2s"}


def convert_hash(src: Path, dst: Path, opts) -> Path:
    """任何文件 → 摘要文本（``sha256sum`` 风格）。

    选项：``algorithm``（默认 sha256，另支持 crc32/crc32c）、``hmac_key``。
    """
    data = read_bytes(src)
    algo = opt_str(opts, "algorithm", "sha256").lower().replace("-", "")
    key = opt_str(opts, "hmac_key", "")

    if algo in ("crc32", "crc32c"):
        crc = zlib.crc32(data) if algo == "crc32" else zlib.crc32(data, 0)  # crc32c 无标准库实现，退化为 crc32
        digest = f"{crc & 0xFFFFFFFF:08x}"
        algo = "crc32"
    elif algo in _HASHLIB_ALGOS:
        if key:
            digest = hmac.new(key.encode("utf-8"), data, algo).hexdigest()
            algo = f"hmac-{algo}"
        else:
            digest = hashlib.new(algo, data).hexdigest()
    else:
        raise ConversionError(
            f"未知摘要算法：{algo}（支持 {'/'.join(sorted(_HASHLIB_ALGOS | {'crc32'}))}）"
        )

    line = f"{digest}  *{src.name}\n{algo}\n"
    return write_bytes(dst, line.encode("utf-8"))


# =========================================================================== #
# 换编码 / 换行（txt -> txt 自环）
# =========================================================================== #
def transcode_text(src: Path, dst: Path, opts) -> Path:
    from_enc = opt_str(opts, "from_encoding", "") or opt_str(opts, "encoding", "")
    to_enc = opt_str(opts, "to_encoding", "")
    newline = opt_str(opts, "newline", "")
    if not (from_enc or to_enc or newline):
        shutil.copy2(src, dst)  # 没有任何编码/换行选项：走快速通道
        return dst
    from ._util import read_text, write_text

    text = read_text(src, opts, default_encoding=from_enc or "auto")
    return write_text(dst, text, opts, default_encoding=to_enc or "utf-8")


# =========================================================================== #
# Markdown ⇄ HTML ⇄ 纯文本
# =========================================================================== #
_CODE_PLACEHOLDER = "\x00CODE{}\x00"


def _escape(text: str) -> str:
    return _html.escape(text, quote=False)


def _inline_md(text: str) -> str:
    placeholders: list[str] = []

    def stash(snippet: str) -> str:
        placeholders.append(snippet)
        return _CODE_PLACEHOLDER.format(len(placeholders) - 1)

    text = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>{_escape(m.group(1))}</code>"), text)
    text = _escape(text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)",
                  lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)",
                  lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)
    for i, snippet in enumerate(placeholders):
        text = text.replace(_CODE_PLACEHOLDER.format(i), snippet)
    return text


def render_markdown(md: str, *, title: str = "", standalone: bool = True,
                    extra_css: str = "") -> str:
    """最小但够用的 Markdown → HTML 渲染器（标题/列表/引用/代码/表格基础）。"""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_ul = in_ol = in_quote = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol, in_quote
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    while i < len(lines):
        line = lines[i]

        # 代码块
        m = re.match(r"^(```|~~~)\s*([\w+#-]*)\s*$", line)
        if m:
            close_lists()
            fence = m.group(1)
            lang = m.group(2)
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{_escape(chr(10).join(body))}</code></pre>")
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline_md(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # 分隔线
        if re.match(r"^\s*([-*_])\s*(\1\s*){2,}$", line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # 引用
        m = re.match(r"^>\s?(.*)$", line)
        if m:
            if in_ul or in_ol:
                close_lists()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{_inline_md(m.group(1))}</p>")
            i += 1
            continue

        # 无序列表
        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline_md(m.group(1))}</li>")
            i += 1
            continue

        # 有序列表
        m = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline_md(m.group(1))}</li>")
            i += 1
            continue

        # 空行
        if not line.strip():
            close_lists()
            i += 1
            continue

        # 普通段落（合并连续行）
        close_lists()
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|```|~~~|>|\s*[-*+]\s|\s*\d+[.)]\s)", lines[i]
        ):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline_md(' '.join(para))}</p>")

    close_lists()
    body = "\n".join(out)

    if not standalone:
        return body + "\n"
    doc_title = title or "文档"
    css = extra_css or (
        "body{max-width:860px;margin:2em auto;padding:0 1em;"
        "font-family:'Segoe UI','Microsoft YaHei',sans-serif;line-height:1.7;color:#24292f}"
        "pre{background:#f6f8fa;padding:1em;border-radius:6px;overflow:auto}"
        "code{background:#f6f8fa;padding:.15em .35em;border-radius:4px}"
        "blockquote{border-left:4px solid #d0d7de;margin:0;padding:0 1em;color:#57606a}"
        "table{border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:.4em .8em}"
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        f"<meta charset=\"utf-8\">\n<title>{_escape(doc_title)}</title>\n"
        f"<style>{css}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


class _HtmlToMd(HTMLParser):
    """HTML → Markdown 的流式转换器（覆盖常见标签，不追求完美保真）。"""

    _BLOCK = {"p", "div", "section", "article", "header", "footer", "main"}
    _HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._list_stack: list[str] = []
        self._ol_counters: list[int] = []
        self._quote = 0
        self._in_pre = False
        self._skip_depth = 0  # script/style

    # ---- 工具 ----
    def _newline(self, n: int = 1) -> None:
        text = "".join(self.parts)
        trailing = len(text) - len(text.rstrip("\n"))
        if trailing < n:
            self.parts.append("\n" * (n - trailing))

    def _prefix(self) -> str:
        indent = ""
        if self._quote:
            indent += "> " * self._quote
        if self._list_stack:
            depth = len(self._list_stack) - 1
            indent += "  " * depth
        return indent

    # ---- 标签 ----
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        adict = dict(attrs)
        if tag in self._HEADING:
            self._newline(2)
            self.parts.append(self._prefix() + "#" * self._HEADING[tag] + " ")
        elif tag in self._BLOCK:
            self._newline(2)
        elif tag == "br":
            self.parts.append("  \n")
        elif tag == "hr":
            self._newline(2)
            self.parts.append("---\n\n")
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "del":
            self.parts.append("~~")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
        elif tag == "pre":
            self._newline(2)
            self.parts.append("```\n")
            self._in_pre = True
        elif tag == "blockquote":
            self._newline(2)
            self._quote += 1
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            if tag == "ol":
                self._ol_counters.append(0)
            if not self._list_stack[:-1]:
                self._newline(1)
        elif tag == "li":
            self._newline(1)
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_counters[-1] += 1
                self.parts.append(self._prefix() + f"{self._ol_counters[-1]}. ")
            else:
                self.parts.append(self._prefix() + "- ")
        elif tag == "a":
            self.parts.append("[")
            self._pending_href = adict.get("href", "")
        elif tag == "img":
            src = adict.get("src", "")
            alt = adict.get("alt", "")
            self.parts.append(f"![{alt}]({src})")
        elif tag == "table":
            self._newline(2)
        elif tag in ("tr",):
            self._newline(1)
        elif tag in ("td", "th"):
            text = "".join(self.parts)
            if self.parts and not text.endswith(("\n", "| ")):
                self.parts.append(" | ")
        elif tag == "title":
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "title"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADING:
            self._newline(2)
        elif tag in self._BLOCK:
            self._newline(2)
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag == "del":
            self.parts.append("~~")
        elif tag == "code" and not self._in_pre:
            self.parts.append("`")
        elif tag == "pre":
            self._newline(1)
            self.parts.append("```\n")
            self._in_pre = False
        elif tag == "blockquote":
            self._newline(1)
            self._quote = max(0, self._quote - 1)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            if tag == "ol" and self._ol_counters:
                self._ol_counters.pop()
            if not self._list_stack:
                self._newline(2)
        elif tag == "li":
            self._newline(1)
        elif tag == "a":
            href = getattr(self, "_pending_href", "")
            self.parts.append(f"]({href})" if href else "]")
            self._pending_href = ""
        elif tag == "table":
            self._newline(2)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_markdown(html_text: str) -> str:
    parser = _HtmlToMd()
    parser.feed(html_text)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


class _HtmlToText(HTMLParser):
    _BLOCK = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2",
              "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self._BLOCK:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in self._BLOCK:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)

    def _newline(self) -> None:
        text = "".join(self.parts)
        if text and not text.endswith("\n"):
            self.parts.append("\n")


def html_to_text(html_text: str) -> str:
    parser = _HtmlToText()
    parser.feed(html_text)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# ---- RTF ----
_RTF_SKIP_DESTS = ("fonttbl", "colortbl", "stylesheet", "info", "pict",
                   "header", "footer", "footnote", "themedata", "datastore")


def _remove_rtf_group(text: str, start: int) -> str:
    """从 ``start``（'{' 的位置）起删掉整个平衡的 RTF 组。"""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[:start] + text[i + 1:]
        i += 1
    return text[:start]


def rtf_to_text(rtf: str, codepage: str = "cp936") -> str:
    """基础 RTF 文本抽取（无依赖）。复杂排版只保留文字内容。"""
    if not rtf.lstrip().startswith("{\\rtf"):
        raise ConversionError("不是有效的 RTF 文件（缺少 {\\rtf 头）")

    # 1) 删除头部定义组
    for dest in _RTF_SKIP_DESTS:
        while True:
            m = re.search(r"\{\\" + dest + r"\b", rtf)
            if not m:
                break
            rtf = _remove_rtf_group(rtf, m.start())
    # 2) 删除 \* 忽略组
    while True:
        m = re.search(r"\{\\\*", rtf)
        if not m:
            break
        rtf = _remove_rtf_group(rtf, m.start())

    # 3) 转义字符
    rtf = re.sub(r"\\'([0-9a-fA-F]{2})",
                 lambda m: bytes([int(m.group(1), 16)]).decode(codepage, "replace"), rtf)

    def _uni(m: re.Match) -> str:
        try:
            code = int(m.group(1))
        except ValueError:
            return ""
        if code < 0:
            code += 65536
        return chr(code)

    rtf = re.sub(r"\\u(-?\d+)\s?\??", _uni, rtf)

    # 4) 结构控制字
    rtf = re.sub(r"\\par[d]?\b", "\n", rtf)
    rtf = re.sub(r"\\line\b", "\n", rtf)
    rtf = re.sub(r"\\tab\b", "\t", rtf)
    rtf = rtf.replace("\\{", "\x01").replace("\\}", "\x02").replace("\\\\", "\x03")

    # 5) 去掉其余控制字与花括号
    rtf = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", rtf)
    rtf = re.sub(r"[{}]", "", rtf)
    rtf = rtf.replace("\x01", "{").replace("\x02", "}").replace("\x03", "\\")

    text = re.sub(r"\n{3,}", "\n\n", rtf)
    return text.strip() + "\n"


# =========================================================================== #
# 简单文本互转
# =========================================================================== #
def _strip_markdown(md: str) -> str:
    md = re.sub(r"```.*?```", "", md, flags=re.S)
    md = re.sub(r"`([^`]*)`", r"\1", md)
    md = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"^(#{1,6})\s*", "", md, flags=re.M)
    md = re.sub(r"^>\s?", "", md, flags=re.M)
    md = re.sub(r"^\s*[-*+]\s+", "", md, flags=re.M)
    md = re.sub(r"\*\*([^*]+)\*\*", r"\1", md)
    md = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", md)
    return md


def _text_to_html(text: str, opts) -> str:
    title = opt_str(opts, "title", "") or "文本"
    pre = _escape(text)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{_escape(title)}</title>\n</head>\n<body>\n<pre>{pre}</pre>\n</body>\n</html>\n"
    )


# =========================================================================== #
# 注册
# =========================================================================== #
def register_text_converters() -> int:
    count = 0

    def add(src, dst, func, **kw):
        nonlocal count
        REGISTRY.add(src, dst, func, group=kw.pop("group", "codec"),
                     **kw)
        count += 1

    # ---- 编码族 ----
    for kind in ENCODE_FORMATS:
        add("txt", kind, _codec_converter(kind, True), note=f"{kind} 编码")
        add(kind, "txt", _codec_converter(kind, False), note=f"{kind} 解码")
        add("bin", kind, _codec_converter(kind, True), note=f"{kind} 编码")
        add(kind, "bin", _codec_converter(kind, False), note=f"{kind} 解码")
    add("txt", "rot13", _rot13_converter(True), note="ROT13 移位")
    add("rot13", "txt", _rot13_converter(False), note="ROT13 移位")

    # ---- 二进制 / 文本 ----
    def bin_to_txt(src: Path, dst: Path, opts) -> Path:
        data = read_bytes(src)
        sample = data[:8192]
        if not is_text_bytes(sample):
            raise ConversionError("内容不是文本编码，无法作为纯文本写出（试试 -o encoding=gb18030）")
        enc = opt_str(opts, "encoding", "auto")
        if enc in ("auto", "detect", ""):
            enc = "utf-8-sig" if data.startswith(codecs.BOM_UTF8) else "utf-8"
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                text = data.decode("gb18030", "replace")
            from ._util import write_text

            return write_text(dst, text, opts)
        from ._util import write_text

        return write_text(dst, decode_text(data, enc), opts)

    def txt_to_bin(src: Path, dst: Path, opts) -> Path:
        return write_bytes(dst, read_bytes(src))

    add("bin", "txt", bin_to_txt, note="按文本解码")
    add("txt", "bin", txt_to_bin, note="按 UTF-8 编码写出")

    # ---- 哈希（任何格式 → hash）----
    hash_sources = [name for name in sorted(FORMATS) if name != "hash"]
    REGISTRY.add(hash_sources, "hash", convert_hash, group="hash",
                 weight=2.0, note="生成摘要")
    count += len(hash_sources)

    # ---- txt 自环：换编码 / 换行 ----
    REGISTRY.add("txt", "txt", transcode_text, group="text", allow_self=True,
                 note="换编码/换行（from_encoding/to_encoding/newline）")
    count += 1
    REGISTRY.add("md", "md", transcode_text, group="text", allow_self=True,
                 note="换编码/换行")
    count += 1

    # ---- 文档族 ----
    def md_to_html(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        text = read_text(src, opts)
        html_out = render_markdown(
            text,
            title=opt_str(opts, "title", "") or src.stem,
            standalone=opt_bool(opts, "standalone", True),
            extra_css=opt_str(opts, "css", ""),
        )
        return write_text(dst, html_out, opts)

    def html_to_md(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        return write_text(dst, html_to_markdown(read_text(src, opts)), opts)

    def html_to_txt(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        return write_text(dst, html_to_text(read_text(src, opts)), opts)

    def md_to_txt(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        return write_text(dst, _strip_markdown(read_text(src, opts)), opts)

    def txt_to_html(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        return write_text(dst, _text_to_html(read_text(src, opts), opts), opts)

    def txt_to_md(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        return write_text(dst, read_text(src, opts), opts)

    def rtf_to_txt(src: Path, dst: Path, opts) -> Path:
        from ._util import read_text, write_text

        text = rtf_to_text(read_text(src, opts, default_encoding="latin-1"),
                           codepage=opt_str(opts, "codepage", "cp936"))
        return write_text(dst, text, opts)

    add("md", "html", md_to_html, group="markdown", note="Markdown 渲染")
    add("html", "md", html_to_md, group="markdown", note="HTML 抽取")
    add("html", "txt", html_to_txt, group="markdown", note="去标签")
    add("md", "txt", md_to_txt, group="markdown", note="去 Markdown 语法")
    add("txt", "html", txt_to_html, group="markdown")
    add("txt", "md", txt_to_md, group="markdown")
    add("rtf", "txt", rtf_to_txt, group="text", note="基础文本抽取")

    return count
