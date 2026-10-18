"""邮件转换：EML → HTML / 文本。

对标 PDFengine / iTextMaster / CoolUtils 的做法：

* 解析 RFC822 邮件（标准库 ``email``），优先 HTML 正文，回退纯文本；
* ``cid:`` 内嵌图片转 base64 data URI 内联（邮件里的图片不丢）；
* 头部信息表（发件人/收件人/抄送/主题/日期），可选完整 Received 链；
* 附件清单（名称/大小），``attachments=目录`` 时逐个导出；
* EML → PDF 由转换图自动多跳实现（eml→html→pdf，需 LibreOffice）。

MSG/PST/OST 等 Outlook 专有格式暂不支持（见路线图）。
"""

from __future__ import annotations

import base64
import email
import email.policy
import html as _html
from email.message import Message
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import opt_bool, opt_str, read_bytes, write_text

__all__ = ["eml_to_html", "eml_to_text", "extract_attachments"]


def _decode_part(part: Message) -> bytes:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 - 编码异常的单部件跳过
        return b""
    return payload or b""


def _collect(msg: Message):
    """遍历邮件部件：返回 (html 正文, 纯文本正文, cid 图片表, 附件表)。"""
    html_body = ""
    text_body = ""
    cid_map: dict[str, tuple[str, bytes]] = {}
    attachments: list[tuple[str, bytes]] = []

    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        cid = part.get("Content-ID", "").strip("<>")

        if ctype == "text/html" and disp != "attachment" and not html_body:
            raw = _decode_part(part)
            charset = part.get_content_charset() or "utf-8"
            html_body = raw.decode(charset, "replace")
            continue
        if ctype == "text/plain" and disp != "attachment" and not text_body:
            raw = _decode_part(part)
            charset = part.get_content_charset() or "utf-8"
            text_body = raw.decode(charset, "replace")
            continue
        if cid and ctype.startswith("image/"):
            cid_map[cid] = (ctype, _decode_part(part))
            continue
        if filename and disp in ("attachment", "inline") and not ctype.startswith(
                "multipart/"):
            attachments.append((filename, _decode_part(part)))
    return html_body, text_body, cid_map, attachments


def _inline_images(html_body: str, cid_map: dict[str, tuple[str, bytes]]) -> str:
    for cid, (mime, data) in cid_map.items():
        b64 = base64.b64encode(data).decode("ascii")
        uri = f"data:{mime};base64,{b64}"
        for pattern in (f"cid:{cid}", f"cid:{cid.lower()}"):
            html_body = html_body.replace(pattern, uri)
    return html_body


def _header_table(msg: Message, full_headers: bool) -> str:
    rows = [
        ("发件人", msg.get("From", "")),
        ("收件人", msg.get("To", "")),
        ("抄送", msg.get("Cc", "")),
        ("主题", msg.get("Subject", "")),
        ("日期", msg.get("Date", "")),
    ]
    html_rows = "".join(
        f"<tr><th>{_html.escape(k)}</th><td>{_html.escape(str(v))}</td></tr>"
        for k, v in rows if v
    )
    extra = ""
    if full_headers:
        received = msg.get_all("Received") or []
        if received:
            chain = "".join(
                f"<li>{_html.escape(str(item))}</li>" for item in received
            )
            extra = (
                "<details><summary>完整 Received 链（" +
                str(len(received)) + " 跳）</summary><ol>" + chain +
                "</ol></details>"
            )
        for key in ("Message-ID", "Return-Path", "DKIM-Signature"):
            value = msg.get(key)
            if value:
                extra += (
                    f'<p class="meta"><b>{_html.escape(key)}</b>：'
                    f"{_html.escape(str(value)[:500])}</p>"
                )
    return f'<table class="mail-head">{html_rows}</table>{extra}'


def _attachment_html(attachments: list[tuple[str, bytes]]) -> str:
    if not attachments:
        return ""
    items = "".join(
        f"<li>{_html.escape(name)}"
        f"（{len(data) / 1024:.1f} KB）</li>"
        for name, data in attachments
    )
    return f"<h3>附件（{len(attachments)} 个）</h3><ul>{items}</ul>"


_STYLE = (
    "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;max-width:860px;"
    "margin:1.5em auto;padding:0 1em;line-height:1.6}"
    "table.mail-head{border-collapse:collapse;width:100%;margin-bottom:1em}"
    "table.mail-head th{text-align:right;padding:.3em .6em;color:#555;"
    "white-space:nowrap;vertical-align:top}"
    "table.mail-head td{border-bottom:1px solid #ddd;padding:.3em .6em}"
    ".meta{color:#777;font-size:.9em;word-break:break-all}"
    "blockquote{border-left:3px solid #ccc;margin:0;padding-left:1em;color:#555}"
    "pre{white-space:pre-wrap}"
)


def eml_to_html(data: bytes, *, full_headers: bool = False,
                attachment_dir: Path | None = None) -> tuple[str, list[Path]]:
    try:
        msg = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"邮件解析失败：{exc}") from exc
    if not isinstance(msg, Message):  # pragma: no cover
        raise ConversionError("不是有效的邮件文件")

    html_body, text_body, cid_map, attachments = _collect(msg)
    if html_body:
        body = _inline_images(html_body, cid_map)
    elif text_body:
        body = f"<pre>{_html.escape(text_body)}</pre>"
    else:
        # 只剩附件类邮件
        body = "<p>（该邮件没有正文）</p>"

    written: list[Path] = []
    if attachment_dir is not None:
        attachment_dir = Path(attachment_dir)
        attachment_dir.mkdir(parents=True, exist_ok=True)

    doc = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(str(msg.get('Subject', '邮件')))}</title>"
        f"<style>{_STYLE}</style></head><body>\n"
        + _header_table(msg, full_headers)
        + f"<div class=\"mail-body\">\n{body}\n</div>\n"
        + _attachment_html(attachments)
        + "\n</body></html>\n"
    )
    if attachment_dir is not None and attachments:
        for name, blob in attachments:
            safe = Path(name).name or "attachment.bin"
            target = attachment_dir / safe
            i = 1
            while target.exists():
                target = attachment_dir / f"{Path(safe).stem} ({i}){Path(safe).suffix}"
                i += 1
            target.write_bytes(blob)
            written.append(target)
    return doc, written


def eml_to_text(data: bytes) -> str:
    from .text import html_to_text

    doc, _ = eml_to_html(data)
    return html_to_text(doc)


def extract_attachments(src: Path, outdir: Path) -> list[Path]:
    """单独导出邮件附件（CLI/API 用）。"""
    _, written = eml_to_html(read_bytes(src), attachment_dir=outdir)
    return written


# --------------------------------------------------------------------------- #
# 转换器注册
# --------------------------------------------------------------------------- #
def _eml_converter_factory(dst_fmt: str):
    def conv(src: Path, dst: Path, opts) -> Path:
        data = read_bytes(src)
        attachment_dir = None
        attach_opt = opt_str(opts, "attachments", "")
        if attach_opt:
            attachment_dir = Path(attach_opt)
            if not attachment_dir.is_absolute():
                attachment_dir = dst.parent / attachment_dir
        doc, _ = eml_to_html(
            data,
            full_headers=opt_bool(opts, "full_headers", False),
            attachment_dir=attachment_dir,
        )
        if dst_fmt == "html":
            return write_text(dst, doc, opts)
        from .text import html_to_text

        return write_text(dst, html_to_text(doc), opts)

    conv.__name__ = f"eml_to_{dst_fmt}"
    return conv


def register_mail_converters() -> int:
    count = 0
    for df in ("html", "txt"):
        REGISTRY.add("eml", df, _eml_converter_factory(df), group="email",
                     note="邮件渲染（cid 内联图 / attachments=目录 导出附件）")
        count += 1
    return count
