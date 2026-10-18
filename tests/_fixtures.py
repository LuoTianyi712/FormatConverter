"""测试夹具：在内存里生成 docx / xlsx / odt / ods / epub / fb2 的合法字节。"""

from __future__ import annotations

import io
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx_bytes() -> bytes:
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>文档标题</w:t></w:r></w:p>
    <w:p><w:r><w:t>第一段 开头</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>带制表符</w:t></w:r></w:p>
    <w:p><w:r><w:t>第二段</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>A1</w:t></w:r></w:p></w:tc>
             <w:tc><w:p><w:r><w:t>B1</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>A2</w:t></w:r></w:p></w:tc>
             <w:tc><w:p><w:r><w:t>B2</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<?xml version=\"1.0\"?><Types/>")
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


def make_xlsx_bytes() -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"

    shared = f"""<?xml version="1.0"?>
<sst xmlns="{ns}" count="5" uniqueCount="5">
  <si><t>姓名</t></si><si><t>年龄</t></si>
  <si><t>张三</t></si><si><t>李四</t></si><si><t>30</t></si>
</sst>"""

    sheet = f"""<?xml version="1.0"?>
<worksheet xmlns="{ns}">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>30</v></c></row>
    <row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>25.5</v></c></row>
  </sheetData>
</worksheet>"""

    workbook = f"""<?xml version="1.0"?>
<workbook xmlns="{ns}" xmlns:r="{rns}">
  <sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

    rels = f"""<?xml version="1.0"?>
<Relationships xmlns="{pkg}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<?xml version=\"1.0\"?><Types/>")
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def make_odt_bytes() -> bytes:
    t = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    o = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="{o}" xmlns:text="{t}">
  <office:body><office:text>
    <text:h text:outline-level="1">ODT 标题</text:h>
    <text:p>正文第一段<text:tab/>尾。</text:p>
    <text:p>正文第二段</text:p>
    <text:list>
      <text:list-item><text:p>项目一</text:p></text:list-item>
      <text:list-item><text:p>项目二</text:p></text:list-item>
    </text:list>
  </office:text></office:body>
</office:document-content>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content)
    return buf.getvalue()


def make_epub_bytes() -> bytes:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">test-book</dc:identifier>
    <dc:title>测试电子书</dc:title>
  </metadata>
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

    def chapter(n: int, text: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第{n}章</title></head>
<body><h1>第{n}章</h1><p>{text}</p></body></html>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/chapter1.xhtml", chapter(1, "第一章正文内容。"))
        zf.writestr("OEBPS/chapter2.xhtml", chapter(2, "第二章正文内容。"))
    return buf.getvalue()


def make_fb2_bytes() -> bytes:
    return """<?xml version="1.0" encoding="UTF-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <body>
    <section>
      <title><p>书名标题</p></title>
      <p>第一段内容。</p>
      <empty-line/>
      <p>第二段内容。</p>
    </section>
  </body>
</FictionBook>""".encode("utf-8")


def make_srt_text() -> str:
    return (
        "1\n"
        "00:00:01,000 --> 00:00:03,500\n"
        "你好，世界\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "第二句字幕\n"
        "有第二行\n"
    )
