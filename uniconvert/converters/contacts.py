"""通讯录转换：vCard（.vcf）⇄ CSV / JSON。

手机通讯录导出的 vcf 常需要转成表格（Excel 编辑）或 JSON（程序处理），
反向也需要批量生成 vcf。本模块零依赖实现 vCard 2.1/3.0 的常用字段读写：

* 字段：FN/N（姓名）、NICKNAME（昵称）、ORG（组织）、TITLE（职位）、
  TEL（电话，多值取全部）、EMAIL（邮箱）、ADR（地址）、BDAY（生日）、
  NOTE（备注）、URL（网址）
* vCard 行折叠（续行以空格/制表开头）、TEL;TYPE=CELL 参数剥离、
  \\n 转义还原
"""

from __future__ import annotations

import csv as _csv
import io
import json as _json
import re
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import opt_str, read_text, write_text

#: 内部字段名（也是 CSV 表头 / JSON 键），带中文别名便于手写 CSV
COLUMNS = ["name", "nickname", "org", "title", "phone", "email",
           "address", "birthday", "note", "url"]

_ALIASES = {
    "name": "name", "fn": "name", "姓名": "name", "名字": "name",
    "nickname": "nickname", "昵称": "nickname",
    "org": "org", "组织": "org", "公司": "org", "organization": "org",
    "title": "title", "职位": "title", "职务": "title",
    "phone": "phone", "tel": "phone", "电话": "phone", "手机": "phone", "手机号": "phone",
    "email": "email", "邮箱": "email", "邮件": "email", "e-mail": "email",
    "address": "address", "地址": "address", "adr": "address",
    "birthday": "birthday", "生日": "birthday", "bday": "birthday",
    "note": "note", "备注": "note", "notes": "note",
    "url": "url", "网址": "url", "网站": "url", "web": "url",
}

_ESCAPE_BACK = {"\\n": "\n", "\\N": "\n", "\\,": ",", "\\;": ";", "\\\\": "\\"}


def _unescape(value: str) -> str:
    for k, v in _ESCAPE_BACK.items():
        value = value.replace(k, v)
    return value


def _escape(value: str) -> str:
    return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))


def parse_vcf(text: str) -> list[dict]:
    """解析 vcf 文本 → [{name, phone: [...], ...}]。"""
    cards: list[dict] = []
    current: dict | None = None
    # vCard 行折叠：以空格/制表开头的行是上一行的续行
    unfolded: list[str] = []
    for raw in text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw)

    for line in unfolded:
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {col: [] for col in COLUMNS}
            continue
        if upper == "END:VCARD":
            if current is not None:
                cards.append({k: (v if k in ("phone", "email") else (v[0] if v else ""))
                              for k, v in current.items()})
            current = None
            continue
        if current is None or ":" not in line:
            continue
        prop, _, value = line.partition(":")
        name, _, params = prop.partition(";")
        key = name.upper()
        if name.upper() == "N":
            field = "name"
            if value:
                parts = [p.strip() for p in value.split(";")]
                value = "".join(p for p in (parts[1], parts[0]) if p) or ",".join(p for p in parts if p)
        elif key == "FN":
            field = "name"
        elif key == "NICKNAME":
            field = "nickname"
        elif key == "ORG":
            field = "org"
            value = value.split(";")[0]
        elif key == "TITLE":
            field = "title"
        elif key == "TEL":
            field = "phone"
        elif key == "EMAIL":
            field = "email"
        elif key == "ADR":
            field = "address"
            value = "; ".join(p for p in value.split(";") if p.strip())
        elif key == "BDAY":
            field = "birthday"
        elif key == "NOTE":
            field = "note"
        elif key == "URL":
            field = "url"
        else:
            continue
        value = _unescape(value).strip()
        if not value:
            continue
        if field == "phone" and "type=cell" in params.lower():
            current["phone"].insert(0, value)  # 手机号排前面
        elif field in ("phone", "email"):
            current[field].append(value)
        elif not current[field]:
            current[field] = [value]
    if not cards:
        if text.strip():
            raise ConversionError("没有解析出任何 vCard 条目（检查文件是否为 .vcf）")
        return []
    return cards


def build_vcf(cards: list[dict]) -> str:
    out: list[str] = []
    for card in cards:
        norm = {col: card.get(col, "") for col in COLUMNS}
        name = str(norm["name"]).strip()
        out.append("BEGIN:VCARD")
        out.append("VERSION:3.0")
        out.append(f"FN:{_escape(name)}")
        out.append(f"N:{_escape(name)};;;;")
        if norm["nickname"]:
            out.append(f"NICKNAME:{_escape(norm['nickname'])}")
        if norm["org"]:
            out.append(f"ORG:{_escape(norm['org'])}")
        if norm["title"]:
            out.append(f"TITLE:{_escape(norm['title'])}")
        phones = norm["phone"]
        if isinstance(phones, (list, tuple)):
            for p in phones:
                if str(p).strip():
                    out.append(f"TEL;TYPE=CELL:{_escape(p)}")
        elif str(phones).strip():
            out.append(f"TEL;TYPE=CELL:{_escape(phones)}")
        emails = norm["email"]
        if isinstance(emails, (list, tuple)):
            for e in emails:
                if str(e).strip():
                    out.append(f"EMAIL:{_escape(e)}")
        elif str(emails).strip():
            out.append(f"EMAIL:{_escape(emails)}")
        if norm["address"]:
            out.append(f"ADR:;;{_escape(norm['address'])};;;;")
        if norm["birthday"]:
            out.append(f"BDAY:{_escape(norm['birthday'])}")
        if norm["url"]:
            out.append(f"URL:{_escape(norm['url'])}")
        if norm["note"]:
            out.append(f"NOTE:{_escape(norm['note'])}")
        out.append("END:VCARD")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# 转换实现
# --------------------------------------------------------------------------- #
def _cell_text(value) -> str:
    return " / ".join(str(v) for v in value if str(v).strip()) if isinstance(value, list) else str(value)


def vcf_to_structured(src: Path, opts) -> list[dict]:
    return parse_vcf(read_text(src, opts))


def structured_to_vcf(rows: list[dict], dst: Path, opts) -> Path:
    normalized: list[dict] = []
    for row in rows:
        card: dict = {}
        for key, value in row.items():
            field = _ALIASES.get(str(key).strip().lower(), None)
            if field:
                card[field] = value
        if card:
            normalized.append(card)
    return write_text(dst, build_vcf(normalized), opts)


def convert_contacts(src: Path, dst: Path, opts, src_fmt: str, dst_fmt: str) -> Path:
    if src_fmt == "vcf":
        cards = vcf_to_structured(src, opts)
        if dst_fmt == "json":
            payload = _json.dumps(
                [{k: v for k, v in c.items() if v} for c in cards],
                ensure_ascii=False, indent=2,
            ) + "\n"
            return write_text(dst, payload, opts)
        if dst_fmt == "csv":
            buf = io.StringIO(newline="")
            writer = _csv.writer(buf, lineterminator="\n")
            writer.writerow(COLUMNS)
            for card in cards:
                writer.writerow([_cell_text(card.get(col, "")) for col in COLUMNS])
            return write_text(dst, buf.getvalue(), opts)
        raise ConversionError(f"通讯录不支持 {src_fmt} -> {dst_fmt}")

    # csv / json -> vcf
    if src_fmt == "json":
        text = read_text(src, opts)
        try:
            rows = _json.loads(text)
        except _json.JSONDecodeError as exc:
            raise ConversionError(f"JSON 解析失败：{exc}") from exc
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ConversionError("通讯录 JSON 应为对象数组")
    else:
        delimiter = opt_str(opts, "delimiter", ",") or ","
        text = read_text(src, opts)
        rows = list(_csv.DictReader(io.StringIO(text), delimiter=delimiter))
    return structured_to_vcf(rows, dst, opts)


def register_contacts_converters() -> int:
    count = 0
    for a, b in (("vcf", "csv"), ("vcf", "json")):
        def make(sf=a, df=b):
            def conv(src: Path, dst: Path, opts) -> Path:
                return convert_contacts(src, dst, opts, sf, df)
            conv.__name__ = f"contacts_{sf}_to_{df}"
            return conv

        REGISTRY.add(a, b, make(), group="contacts",
                     note="通讯录转换（vCard ⇄ 表格/JSON）")
        count += 1

        def make_rev(sf=b, df=a):
            def conv(src: Path, dst: Path, opts) -> Path:
                return convert_contacts(src, dst, opts, sf, df)
            conv.__name__ = f"contacts_{sf}_to_{df}"
            return conv

        REGISTRY.add(b, a, make_rev(), group="contacts",
                     note="通讯录转换（表格/JSON ⇄ vCard）")
        count += 1
    return count
