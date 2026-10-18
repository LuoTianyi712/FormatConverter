"""文档世界：docx / odt / xlsx / ods / odt / epub / fb2 的零依赖解析与互转。

原理：这些「现代 Office / 电子书格式」本质都是 **ZIP + XML**，
标准库的 ``zipfile`` + ``xml.etree`` 就能读。因此本模块不依赖任何
第三方包（LibreOffice 只在 doc/pdf 互转时作为可选增强）。

覆盖：

* ``docx -> txt / md / html``（段落、标题、表格）；
* ``odt -> txt / html``（段落、标题、列表）；
* ``xlsx -> csv / tsv / json / html``（共享字符串、多工作表）；
* ``ods -> csv / json``（含重复列压缩展开）；
* ``epub -> txt / html / md``（按 spine 顺序抽取）；
* ``fb2 -> txt / html``；
* ``html -> epub``（生成标准 EPUB3）；
* ``doc / docx / odt / pptx… <-> pdf``（可选 LibreOffice）。
"""

from __future__ import annotations

import csv as _csv
import html as _html
import io
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..core.deps import has_dep
from ..core.registry import REGISTRY
from ..errors import ConversionError, DependencyMissingError
from ._util import (
    decode_text,
    opt_bool,
    opt_str,
    read_bytes,
    read_text,
    run_tool,
    write_text,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
ODS_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
ODS_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
ODS_OFFVAL = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"  # pptx 文本


# =========================================================================== #
# docx
# =========================================================================== #
def _docx_paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        tag = node.tag
        if tag == W + "t":
            parts.append(node.text or "")
        elif tag == W + "tab":
            parts.append("\t")
        elif tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts)


def _docx_paragraph_style(p: ET.Element) -> str:
    props = p.find(W + "pPr")
    if props is None:
        return ""
    style = props.find(W + "pStyle")
    if style is None:
        return ""
    return style.get(W + "val", "") or ""


def _docx_iter_blocks(root: ET.Element):
    """按文档顺序产出 ('p', element) / ('table', element)。"""
    body = root.find(W + "body")
    if body is None:
        return
    for child in body:
        if child.tag == W + "p":
            yield "p", child
        elif child.tag == W + "tbl":
            yield "table", child


def _docx_table_rows(tbl: ET.Element) -> list[list[str]]:
    rows = []
    for tr in tbl.findall(W + "tr"):
        cells = []
        for tc in tr.findall(W + "tc"):
            text = "\n".join(
                _docx_paragraph_text(p) for p in tc.findall(W + "p")
            ).strip()
            cells.append(text)
        rows.append(cells)
    return rows


def docx_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    out: list[str] = []
    for kind, elem in _docx_iter_blocks(root):
        if kind == "p":
            out.append(_docx_paragraph_text(elem))
        else:
            for row in _docx_table_rows(elem):
                out.append("\t".join(row))
    return "\n".join(out).strip() + "\n"


def docx_to_html(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    out: list[str] = []
    for kind, elem in _docx_iter_blocks(root):
        if kind == "p":
            text = _docx_paragraph_text(elem)
            style = _docx_paragraph_style(elem).lower()
            if style.startswith("heading") or style.startswith("标题"):
                level = "".join(ch for ch in style if ch.isdigit()) or "2"
                level = min(6, max(1, int(level)))
                out.append(f"<h{level}>{_html.escape(text)}</h{level}>")
            elif not text.strip():
                out.append("<p></p>")
            else:
                out.append(f"<p>{_html.escape(text)}</p>")
        else:
            rows = _docx_table_rows(elem)
            if not rows:
                continue
            html_rows = []
            for idx, row in enumerate(rows):
                tag = "th" if idx == 0 else "td"
                cells = "".join(
                    f"<{tag}>{_html.escape(c)}</{tag}>" for c in row
                )
                html_rows.append(f"<tr>{cells}</tr>")
            out.append(
                "<table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">"
                + "".join(html_rows) + "</table>"
            )
    return "\n".join(out) + "\n"


# =========================================================================== #
# odt
# =========================================================================== #
def _odt_text_of(elem: ET.Element) -> str:
    parts: list[str] = []

    def walk(node: ET.Element, into: list[str]) -> None:
        if node.text:
            into.append(node.text)
        for child in node:
            tag = child.tag
            if tag == T + "s":
                count = int(child.get(T + "c", "1") or "1")
                into.append(" " * count)
            elif tag == T + "tab":
                into.append("\t")
            elif tag == T + "line-break":
                into.append("\n")
            elif tag == T + "span":
                walk(child, into)
            else:
                walk(child, into)
            if child.tail:
                into.append(child.tail)

    walk(elem, parts)
    return "".join(parts)


def _odt_blocks(content_root: ET.Element):
    body = content_root.find(f"{ODS_OFFICE}body")
    if body is None:
        body = content_root.find("body")
    if body is None:
        return
    text_root = body.find(f"{ODS_OFFICE}text") if body.find(f"{ODS_OFFICE}text") is not None else body
    for child in list(text_root):
        if child.tag == T + "h":
            yield "h", child
        elif child.tag == T + "p":
            yield "p", child
        elif child.tag == T + "list":
            for item in child.iter(T + "list-item"):
                yield "li", item
        elif child.tag == ODS_TABLE + "table":
            yield "table", child


def odt_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    out: list[str] = []
    for kind, elem in _odt_blocks(root):
        if kind == "table":
            for tr in elem.findall(ODS_TABLE + "table-row"):
                cells = [_odt_text_of(tc).strip() for tc in tr.findall(ODS_TABLE + "table-cell")]
                out.append("\t".join(cells))
        elif kind == "li":
            out.append("- " + _odt_text_of(elem).strip())
        else:
            out.append(_odt_text_of(elem).rstrip())
    return "\n".join(out).strip() + "\n"


def odt_to_html(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    out: list[str] = []
    for kind, elem in _odt_blocks(root):
        if kind == "table":
            rows = []
            for tr in elem.findall(ODS_TABLE + "table-row"):
                cells = "".join(
                    f"<td>{_html.escape(_odt_text_of(tc).strip())}</td>"
                    for tc in tr.findall(ODS_TABLE + "table-cell")
                )
                rows.append(f"<tr>{cells}</tr>")
            out.append("<table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">"
                       + "".join(rows) + "</table>")
        elif kind == "li":
            out.append(f"<li>{_html.escape(_odt_text_of(elem).strip())}</li>")
        elif kind == "h":
            level = elem.get(T + "outline-level", "2")
            level = min(6, max(1, int("".join(c for c in level if c.isdigit()) or "2")))
            out.append(f"<h{level}>{_html.escape(_odt_text_of(elem).strip())}</h{level}>")
        else:
            text = _odt_text_of(elem).strip()
            if text:
                out.append(f"<p>{_html.escape(text)}</p>")
    return "\n".join(out) + "\n"


# =========================================================================== #
# xlsx / ods
# =========================================================================== #
def _col_to_index(ref: str) -> int:
    """'B3' -> 2（0 基列号）。"""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _xlsx_sheet_map(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """返回 [(工作表名, zip 内路径)]，按工作簿定义顺序。"""
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map: dict[str, str] = {}
    for rel in rels.findall(PKG_REL_NS + "Relationship"):
        target = rel.get("Target", "")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = "xl/" + target.lstrip("./")
        rel_map[rel.get("Id", "")] = target
    sheets: list[tuple[str, str]] = []
    for sheet in wb.findall(f"{XLSX_NS}sheets/{XLSX_NS}sheet"):
        name = sheet.get("name", f"Sheet{len(sheets) + 1}")
        rid = sheet.get(f"{XLSX_REL_NS}id", "")
        path = rel_map.get(rid, f"xl/worksheets/sheet{len(sheets) + 1}.xml")
        sheets.append((name, path))
    return sheets


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out: list[str] = []
    for si in sst.findall(XLSX_NS + "si"):
        text = "".join(t.text or "" for t in si.iter(XLSX_NS + "t"))
        out.append(text)
    return out


def _xlsx_sheet_rows(zf: zipfile.ZipFile, sheet_path: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: list[list[str]] = []
    for row_elem in root.iter(XLSX_NS + "row"):
        cells: dict[int, str] = {}
        max_idx = -1
        for c in row_elem.findall(XLSX_NS + "c"):
            ref = c.get("r", "")
            idx = _col_to_index(ref) if ref else max_idx + 1
            ctype = c.get("t")
            value_elem = c.find(XLSX_NS + "v")
            raw = value_elem.text if value_elem is not None and value_elem.text is not None else ""
            if ctype == "s" and raw:
                try:
                    raw = shared[int(raw)]
                except (ValueError, IndexError):
                    pass
            elif ctype == "inlineStr":
                is_elem = c.find(XLSX_NS + "is")
                raw = "".join(t.text or "" for t in is_elem.iter(XLSX_NS + "t")) if is_elem is not None else ""
            elif ctype == "b" and raw:
                raw = "TRUE" if raw == "1" else "FALSE"
            if raw == "" and value_elem is None and ctype is None:
                continue
            cells[idx] = raw
            max_idx = max(max_idx, idx)
        rows.append([cells.get(i, "") for i in range(max_idx + 1)])
    while rows and not any(cell for cell in rows[-1]):
        rows.pop()
    return rows


def xlsx_to_rows(data: bytes, sheet: str = "") -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        sheets = _xlsx_sheet_map(zf)
        if not sheets:
            raise ConversionError("工作簿里没有工作表")
        if sheet:
            chosen = None
            for idx, (name, path) in enumerate(sheets):
                if sheet == name or sheet == str(idx + 1):
                    chosen = (name, path)
                    break
            if chosen is None:
                names = ", ".join(n for n, _ in sheets)
                raise ConversionError(f"找不到工作表 {sheet!r}（可选：{names}）")
        else:
            chosen = sheets[0]
        shared = _xlsx_shared_strings(zf)
        return _xlsx_sheet_rows(zf, chosen[1], shared)


def _rows_to_csv_text(rows: list[list[str]], delimiter: str = ",") -> str:
    buf = io.StringIO(newline="")
    writer = _csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    writer.writerows(rows)
    return buf.getvalue()


def _rows_to_html(rows: list[list[str]], title: str = "") -> str:
    trs = []
    for idx, row in enumerate(rows):
        tag = "th" if idx == 0 else "td"
        trs.append("<tr>" + "".join(
            f"<{tag}>{_html.escape(str(c))}</{tag}>" for c in row) + "</tr>")
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title or '表格')}</title>"
        "<style>table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px 8px}</style>"
        f"</head><body>\n<table>\n" + "\n".join(trs) + "\n</table>\n</body></html>\n"
    )


def rows_to_json_obj(rows: list[list[str]], header: bool = True):
    import json as _json  # noqa: F401  (仅类型提示用途)
    if not rows:
        return []
    if header:
        head = [str(h) for h in rows[0]]
        out = []
        for row in rows[1:]:
            item = {}
            for i, key in enumerate(head):
                item[key] = row[i] if i < len(row) else ""
            out.append(item)
        return out
    return [list(r) for r in rows]


# ---- ODS ----
def ods_to_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    for table in root.iter(ODS_TABLE + "table"):
        rows: list[list[str]] = []
        for tr in table.findall(ODS_TABLE + "table-row"):
            repeat_row = int(tr.get(ODS_TABLE + "number-rows-repeated", "1") or "1")
            row: list[str] = []
            for tc in tr.findall(ODS_TABLE + "table-cell"):
                repeat = int(tc.get(ODS_TABLE + "number-columns-repeated", "1") or "1")
                value = tc.get(ODS_OFFVAL + "value")
                if value is None:
                    value = _odt_text_of(tc).strip()
                row.extend([value] * min(repeat, 1024))
            while row and row[-1] == "":
                row.pop()
            for _ in range(min(repeat_row, 10000)):
                rows.append(list(row))
        while rows and not any(cell for cell in rows[-1]):
            rows.pop()
        if rows:
            return rows
    return []


# =========================================================================== #
# pptx（演示文稿，零依赖文本抽取）
# =========================================================================== #
def _pptx_slides(data: bytes) -> list[list[str]]:
    """按幻灯片顺序返回每页的段落文本。"""
    import re

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist()
                 if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        if not names:
            raise ConversionError("PPTX 里没有找到幻灯片（可能不是有效 pptx）")
        names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        slides: list[list[str]] = []
        for name in names:
            root = ET.fromstring(zf.read(name))
            lines: list[str] = []
            for para in root.iter(A + "p"):
                text = "".join(t.text or "" for t in para.iter(A + "t"))
                if text.strip():
                    lines.append(text.strip())
            slides.append(lines)
    return slides


def pptx_to_text(data: bytes) -> str:
    out: list[str] = []
    for i, lines in enumerate(_pptx_slides(data), 1):
        out.append(f"=== 幻灯片 {i} ===")
        out.extend(lines)
        out.append("")
    return "\n".join(out).strip() + "\n"


def pptx_to_html(data: bytes) -> str:
    out: list[str] = []
    for i, lines in enumerate(_pptx_slides(data), 1):
        out.append(f"<h2>幻灯片 {i}</h2>")
        for line in lines:
            out.append(f"<p>{_html.escape(line)}</p>")
    return "\n".join(out) + "\n"


# =========================================================================== #
# epub / fb2
# =========================================================================== #
def _epub_spine_docs(data: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """返回 (书名, [(文件名, XHTML 字节)])，按 spine 顺序。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        ns_c = "{urn:oasis:names:tc:opendocument:xmlns:container}"
        rootfile = container.find(f".//{ns_c}rootfile")
        if rootfile is None:
            raise ConversionError("EPUB 缺少 rootfile 声明")
        opf_path = rootfile.get("full-path", "")
        opf_dir = str(Path(opf_path).parent).replace("\\", "/")
        opf = ET.fromstring(zf.read(opf_path))

        opfns = "{http://www.idpf.org/2007/opf}"
        dcns = "{http://purl.org/dc/elements/1.1/}"
        title_elem = opf.find(f".//{dcns}title")
        title = (title_elem.text or "").strip() if title_elem is not None else ""

        manifest: dict[str, str] = {}
        for item in opf.iter(f"{opfns}item"):
            manifest[item.get("id", "")] = item.get("href", "")

        docs: list[tuple[str, bytes]] = []
        for itemref in opf.iter(f"{opfns}itemref"):
            href = manifest.get(itemref.get("idref", ""), "")
            if not href:
                continue
            full = f"{opf_dir}/{href}" if opf_dir and opf_dir != "." else href
            try:
                docs.append((href, zf.read(full)))
            except KeyError:
                continue
    if not docs:
        raise ConversionError("EPUB 没有可读的正文文档")
    return title, docs


def _xhtml_to_text(data: bytes) -> str:
    from .text import html_to_text

    return html_to_text(decode_text(data, "utf-8"))


def epub_to_text(data: bytes) -> str:
    title, docs = _epub_spine_docs(data)
    chunks = [f"《{title}》" if title else ""]
    for href, doc in docs:
        chunks.append(_xhtml_to_text(doc).strip())
    return "\n\n".join(c for c in chunks if c).strip() + "\n"


def epub_to_html(data: bytes) -> str:
    from .text import html_to_text  # noqa: F401

    title, docs = _epub_spine_docs(data)
    bodies = []
    for href, doc in docs:
        text = decode_text(doc, "utf-8")
        body = _strip_xhtml_wrapper(text)
        bodies.append(body)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title or '电子书')}</title></head><body>\n"
        + "\n<hr>\n".join(bodies) + "\n</body></html>\n"
    )


def _strip_xhtml_wrapper(text: str) -> str:
    """去掉 XHTML 文档外壳，只保留 <body> 内部。"""
    import re

    m = re.search(r"<body[^>]*>(.*?)</body>", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    return text.strip()


def fb2_to_text(data: bytes) -> str:
    root = ET.fromstring(data)
    out: list[str] = []

    def walk(elem: ET.Element) -> None:
        for child in elem:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in ("title", "subtitle"):
                line = " ".join((t.text or "").strip() for t in child.iter() if t.text)
                if line.strip():
                    out.append(("# " if tag == "title" else "## ") + line.strip())
            elif tag == "p":
                out.append(" ".join((t.text or "").strip() for t in child.iter() if t.text))
            elif tag == "empty-line":
                out.append("")
            elif tag in ("section", "body", "poem", "stanza", "epigraph", "cite"):
                walk(child)
            elif tag == "v":
                out.append((child.text or "").strip())

    body = root.find("{http://www.gribuser.ru/xml/fictionbook/2.0}body")
    if body is None:
        for child in root:
            if child.tag.rsplit("}", 1)[-1] == "body":
                body = child
                break
    if body is None:
        raise ConversionError("FB2 缺少 body 节点")
    walk(body)
    return "\n".join(out).strip() + "\n"


def html_to_epub(html_text: str, title: str = "", author: str = "") -> bytes:
    """把一份 HTML 打包成最小但合法的 EPUB3。"""
    import re

    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.S | re.I)
        title = (m.group(1).strip() if m else "未命名文档") or "未命名文档"
    book_id = f"urn:uuid:{uuid.uuid4()}"
    body = _strip_xhtml_wrapper(html_text)
    chapter = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<!DOCTYPE html>\n"
        "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"zh-CN\">\n"
        f"<head><meta charset=\"utf-8\"/><title>{_html.escape(title)}</title></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
    opf = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"3.0\" unique-identifier=\"bookid\">\n"
        "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">\n"
        f"    <dc:identifier id=\"bookid\">{book_id}</dc:identifier>\n"
        f"    <dc:title>{_html.escape(title)}</dc:title>\n"
        f"    <dc:language>zh-CN</dc:language>\n"
        + (f"    <dc:creator>{_html.escape(author)}</dc:creator>\n" if author else "")
        + "  </metadata>\n"
        "  <manifest>\n"
        "    <item id=\"nav\" href=\"nav.xhtml\" media-type=\"application/xhtml+xml\" properties=\"nav\"/>\n"
        "    <item id=\"chap1\" href=\"chapter1.xhtml\" media-type=\"application/xhtml+xml\"/>\n"
        "  </manifest>\n"
        "  <spine><itemref idref=\"chap1\"/></spine>\n"
        "</package>\n"
    )
    nav = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\">\n"
        f"<head><title>{_html.escape(title)}</title></head>\n<body>\n"
        "<nav epub:type=\"toc\" id=\"toc\"><ol>"
        f"<li><a href=\"chapter1.xhtml\">{_html.escape(title)}</a></li>"
        "</ol></nav>\n</body>\n</html>\n"
    )
    container = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">\n"
        "  <rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
        "media-type=\"application/oebps-package+xml\"/></rootfiles>\n"
        "</container>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须第一个条目且不压缩
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/chapter1.xhtml", chapter)
    return buf.getvalue()


# =========================================================================== #
# CAJ → PDF（知网文献，提取内嵌 PDF —— 零依赖启发式）
# =========================================================================== #
def caj_to_pdf(src: Path, dst: Path, opts) -> Path:
    """CAJ → PDF。

    相当一部分 CAJ 文件内部直接嵌了完整的 PDF 数据（或在头里藏了 PDF 流），
    这里用零依赖的方式把那段 PDF 提取出来；遇到加密/专有变体（HN/KDH 等）
    给出明确提示（推荐 caj2pdf 工具）。
    """
    data = read_bytes(src)

    # 情况 1：本来就是被改成 .caj 的 PDF
    if data[:5] == b"%PDF-":
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return dst

    # 情况 2：内部嵌有 PDF（从 %PDF- 起到文件尾）
    idx = data.find(b"%PDF-")
    if idx >= 0:
        candidate = data[idx:]
        tail = candidate[-4096:]
        if b"%%EOF" in tail or b"startxref" in tail:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(candidate)
            return dst

    raise ConversionError(
        "这个 CAJ 文件里没有可提取的 PDF 数据（可能是加密/专有变体）。\n"
        "  建议用开源工具 caj2pdf 处理：https://github.com/caj2pdf/caj2pdf"
    )


# =========================================================================== #
# LibreOffice 增强（可选）
# =========================================================================== #
def _soffice() -> str:
    if has_dep("soffice"):
        return "soffice"
    if has_dep("exe:soffice"):
        return "soffice"
    raise DependencyMissingError(
        "soffice",
        why="Office 格式互转（doc/docx/ppt/odt↔pdf 等）需要 LibreOffice",
        install="安装 LibreOffice 后将 soffice 加入 PATH（Windows 默认在 C:\\Program Files\\LibreOffice\\program）",
    )


_SOFFICE_MAP = {
    "pdf": "pdf", "docx": "docx", "doc": "doc", "odt": "odt",
    "pptx": "pptx", "xlsx": "xlsx", "html": "html", "txt": "txt",
    "rtf": "rtf", "odp": "odp", "ods": "ods", "epub": "epub",
}


def soffice_convert(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """用 LibreOffice 做重格式转换（转化的其实是「导出」）。"""
    tool = _soffice()
    target = _SOFFICE_MAP.get(dst_fmt)
    if target is None:
        raise ConversionError(f"LibreOffice 不支持导出为 {dst_fmt}")
    with tempfile.TemporaryDirectory(prefix="uniconvert-lo-") as tmp:
        run_tool([
            tool, "--headless", "--norestore", "--convert-to", target,
            "--outdir", tmp, str(src),
        ], timeout=600)
        produced = Path(tmp) / (src.stem + "." + target)
        if not produced.exists():
            # 有些版本改名/多文件，兜底找目录里第一个
            candidates = list(Path(tmp).glob(f"*.{target}"))
            if not candidates:
                raise ConversionError(f"LibreOffice 没有产出 {target} 文件")
            produced = candidates[0]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, dst)
    return dst


# =========================================================================== #
# 转换包装
# =========================================================================== #
def _rows_to_xlsx(rows, dst: Path, opts) -> Path:
    """把行数据写成 xlsx（需要 openpyxl）。"""
    from ..converters._util import need_dep

    need_dep("openpyxl", why="写出 .xlsx 需要 openpyxl（读取 xlsx 不需要）")
    import openpyxl  # type: ignore

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = opt_str(opts, "sheet", "Sheet1") or "Sheet1"
    for row in rows:
        ws.append([_xlsx_cell(v) for v in row])
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dst))
    return dst


def _xlsx_cell(v):
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    if s == "":
        return None
    # 数值串还原成数字，避免全表变文本
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def csv_to_xlsx(src: Path, dst: Path, opts) -> Path:
    import csv as _csv_mod

    text = read_text(src, opts)
    delimiter = opt_str(opts, "delimiter", "") or ","
    reader = _csv_mod.reader(io.StringIO(text), delimiter=delimiter)
    return _rows_to_xlsx(list(reader), dst, opts)


def json_to_xlsx(src: Path, dst: Path, opts) -> Path:
    import json as _json_mod

    obj = _json_mod.loads(read_text(src, opts))
    rows: list[list] = []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        head: dict[str, None] = {}
        for item in obj:
            if isinstance(item, dict):
                for k in item:
                    head.setdefault(str(k), None)
        keys = list(head)
        rows.append(keys)
        for item in obj:
            rows.append([item.get(k, "") if isinstance(item, dict) else "" for k in keys])
    elif isinstance(obj, list):
        rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in obj]
    else:
        rows = [[k, v] for k, v in obj.items()] if isinstance(obj, dict) else [[obj]]
    return _rows_to_xlsx(rows, dst, opts)


def _wrap_doc(fn, src_fmt: str, dst_fmt: str):
    def conv(src: Path, dst: Path, opts) -> Path:
        data = read_bytes(src)
        if dst_fmt == "txt":
            out = fn(data)
            if src_fmt in ("epub", "fb2"):
                return write_text(dst, out, opts)
            return write_text(dst, out, opts)
        if dst_fmt == "html":
            out = fn(data)
            return write_text(dst, out, opts)
        if dst_fmt == "md":
            from .text import html_to_markdown

            html_out = fn(data)
            return write_text(dst, html_to_markdown(html_out), opts)
        raise ConversionError(f"不支持 {src_fmt} -> {dst_fmt}")  # pragma: no cover

    conv.__name__ = f"{src_fmt}_to_{dst_fmt}_doc"
    return conv


def _xlsx_converter(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    rows = xlsx_to_rows(read_bytes(src), sheet=opt_str(opts, "sheet", ""))
    if dst_fmt == "csv":
        return write_text(dst, _rows_to_csv_text(rows, ","), opts)
    if dst_fmt == "tsv":
        return write_text(dst, _rows_to_csv_text(rows, "\t"), opts)
    if dst_fmt == "html":
        return write_text(dst, _rows_to_html(rows, src.stem), opts)
    if dst_fmt == "json":
        import json as _json

        obj = rows_to_json_obj(rows, header=opt_bool(opts, "header", True))
        return write_text(dst, _json.dumps(obj, ensure_ascii=False, indent=2) + "\n", opts)
    if dst_fmt == "md":
        head = rows[0] if rows else []
        lines = ["| " + " | ".join(str(h) for h in head) + " |",
                 "|" + "---|" * len(head)]
        for row in rows[1:]:
            padded = list(row) + [""] * (len(head) - len(row))
            lines.append("| " + " | ".join(str(c) for c in padded[:len(head)]) + " |")
        return write_text(dst, "\n".join(lines) + "\n", opts)
    raise ConversionError(f"不支持 xlsx -> {dst_fmt}")  # pragma: no cover


def _ods_converter(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    rows = ods_to_rows(read_bytes(src))
    if dst_fmt == "csv":
        return write_text(dst, _rows_to_csv_text(rows, ","), opts)
    if dst_fmt == "tsv":
        return write_text(dst, _rows_to_csv_text(rows, "\t"), opts)
    if dst_fmt == "json":
        import json as _json

        obj = rows_to_json_obj(rows, header=opt_bool(opts, "header", True))
        return write_text(dst, _json.dumps(obj, ensure_ascii=False, indent=2) + "\n", opts)
    raise ConversionError(f"不支持 ods -> {dst_fmt}")  # pragma: no cover


def _html_to_epub_conv(src: Path, dst: Path, opts) -> Path:
    text = read_text(src, opts)
    data = html_to_epub(
        text,
        title=opt_str(opts, "title", ""),
        author=opt_str(opts, "author", ""),
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return dst


# =========================================================================== #
# 注册
# =========================================================================== #
def register_document_converters() -> int:
    count = 0

    def add(src, dst, func, **kw):
        nonlocal count
        REGISTRY.add(src, dst, func, group=kw.pop("group", "document"), **kw)
        count += 1

    # ---- docx ----
    add("docx", "txt", _wrap_doc(docx_to_text, "docx", "txt"), note="零依赖解析 OOXML")
    add("docx", "html", _wrap_doc(docx_to_html, "docx", "html"))
    add("docx", "md", _wrap_doc(docx_to_html, "docx", "md"))

    # ---- odt ----
    add("odt", "txt", _wrap_doc(odt_to_text, "odt", "txt"), note="零依赖解析 ODF")
    add("odt", "html", _wrap_doc(odt_to_html, "odt", "html"))
    add("odt", "md", _wrap_doc(odt_to_html, "odt", "md"))

    # ---- pptx（演示文稿文本抽取）----
    add("pptx", "txt", _wrap_doc(pptx_to_text, "pptx", "txt"), note="零依赖解析 OOXML")
    add("pptx", "html", _wrap_doc(pptx_to_html, "pptx", "html"))
    add("pptx", "md", _wrap_doc(pptx_to_html, "pptx", "md"))

    # ---- xlsx / ods ----
    for df in ("csv", "tsv", "json", "html", "md"):
        add("xlsx", df, lambda s, d, o, df=df: _xlsx_converter(s, d, o, df),
            note="解析工作表")
    for df in ("csv", "tsv", "json"):
        add("ods", df, lambda s, d, o, df=df: _ods_converter(s, d, o, df))

    # ---- 写入 xlsx（需要 openpyxl）----
    add("csv", "xlsx", csv_to_xlsx, deps=("openpyxl",), note="openpyxl 写出")
    add("tsv", "xlsx", csv_to_xlsx, deps=("openpyxl",), note="openpyxl 写出")
    add("json", "xlsx", json_to_xlsx, deps=("openpyxl",), note="openpyxl 写出")

    # ---- epub / fb2 ----
    add("epub", "txt", _wrap_doc(epub_to_text, "epub", "txt"), note="按 spine 顺序抽取")
    add("epub", "html", _wrap_doc(epub_to_html, "epub", "html"))
    add("epub", "md", _wrap_doc(epub_to_html, "epub", "md"))
    add("fb2", "txt", _wrap_doc(fb2_to_text, "fb2", "txt"))
    add("fb2", "html", _wrap_doc(fb2_to_text, "fb2", "html"))

    # ---- html -> epub ----
    add("html", "epub", _html_to_epub_conv, note="打包为 EPUB3")

    # ---- CAJ -> PDF ----
    add("caj", "pdf", caj_to_pdf, note="提取内嵌 PDF（零依赖启发式）")

    # ---- LibreOffice 增强（运行时检查依赖）----
    lo_pairs = [
        ("doc", "docx"), ("doc", "odt"), ("doc", "txt"), ("doc", "pdf"),
        ("doc", "html"),  # doc→md 走 doc→html→md
        ("docx", "pdf"), ("docx", "odt"),
        ("odt", "docx"), ("odt", "pdf"),
        ("rtf", "pdf"), ("rtf", "docx"),
        ("ppt", "pptx"), ("ppt", "pdf"), ("ppt", "html"),  # ppt→md 走 ppt→html→md
        ("pptx", "pdf"),
        ("xls", "xlsx"), ("xls", "pdf"), ("xlsx", "xls"), ("xlsx", "pdf"),
        ("pptx", "odp"),
    ]
    for sf, df in lo_pairs:
        def make(sf=sf, df=df):
            def conv(src: Path, dst: Path, opts) -> Path:
                return soffice_convert(src, dst, opts, df)
            conv.__name__ = f"lo_{sf}_to_{df}"
            return conv

        add(sf, df, make(), group="office",
            note="需要 LibreOffice（报错会提示安装）")

    return count
