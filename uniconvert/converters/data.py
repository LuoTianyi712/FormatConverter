"""结构化数据互转：JSON / YAML / TOML / XML / INI / plist / JSONL / CSV / TSV。

统一走「先解析成 Python 对象、再序列化」的转换模型。
除 YAML 外全部用标准库实现（YAML 缺失时给出 pip 安装提示）。
整族之间是**全连通**的：任意两种格式互转都有直连边，
同格式自转（如 ``json -> json``）表示重新格式化/压缩。
"""

from __future__ import annotations

import configparser
import csv as _csv
import datetime as _dt
import io
import json
import plistlib
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import (
    decode_text,
    need_dep,
    opt_bool,
    opt_int,
    opt_str,
    read_bytes,
    write_bytes,
    write_text,
)

if sys.version_info >= (3, 11):
    import tomllib as _toml_read
else:  # pragma: no cover - Python 3.10 兜底
    try:
        import tomli as _toml_read  # type: ignore
    except ImportError:  # pragma: no cover
        _toml_read = None  # type: ignore

#: 本族格式（相互全连通）
DATA_FORMATS = ["json", "yaml", "toml", "xml", "ini", "plist", "jsonl", "csv", "tsv"]

_DEFAULT_ENCODING = "utf-8-sig"


# =========================================================================== #
# 解析：文本/字节 -> Python 对象
# =========================================================================== #
def _load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"JSON 解析失败：{exc}") from exc


def _load_yaml(text: str) -> Any:
    need_dep("yaml", why="YAML 解析/生成需要 PyYAML")
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConversionError(f"YAML 解析失败：{exc}") from exc


def _load_toml(text: str) -> Any:
    if _toml_read is None:  # pragma: no cover
        raise ConversionError("当前 Python 缺少 tomllib（可 pip install tomli 兜底）")
    try:
        return _toml_read.loads(text)
    except _toml_read.TOMLDecodeError as exc:
        raise ConversionError(f"TOML 解析失败：{exc}") from exc


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_to_obj(elem: ET.Element) -> Any:
    """XML → dict 的启发式映射：

    * 属性 → ``"@属性名"`` 键；
    * 重复子元素 → 数组；单个子元素 → 直接值；
    * 纯文本叶子节点 → 字符串。
    """
    children = list(elem)
    text = (elem.text or "").strip()
    if not children and not elem.attrib:
        return text
    obj: dict[str, Any] = {}
    for k, v in elem.attrib.items():
        obj["@" + k] = v
    if text:
        obj["#text"] = text
    buckets: dict[str, list[Any]] = {}
    for child in children:
        buckets.setdefault(_strip_ns(child.tag), []).append(_xml_to_obj(child))
    for tag, items in buckets.items():
        obj[tag] = items[0] if len(items) == 1 else items
    return obj


def _load_xml(text: str) -> Any:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ConversionError(f"XML 解析失败：{exc}") from exc
    return {_strip_ns(root.tag): _xml_to_obj(root)}


def _load_ini(text: str) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConversionError(f"INI 解析失败：{exc}") from exc
    out: dict[str, Any] = {}
    if parser.defaults():
        out["__default__"] = dict(parser.defaults())
    for section in parser.sections():
        out[section] = dict(parser.items(section, raw=True))
    return out


def _load_plist(data: bytes) -> Any:
    try:
        return plistlib.loads(data)
    except Exception as exc:  # noqa: BLE001 - plistlib 异常类型杂
        raise ConversionError(f"plist 解析失败：{exc}") from exc


def _load_jsonl(text: str) -> list[Any]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ConversionError(f"JSONL 第 {i} 行解析失败：{exc}") from exc
    return out


def _sniff_delimiter(text: str, default: str) -> str:
    sample = text[:8192]
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except _csv.Error:
        return default


_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")


def _infer_scalar(value):
    """``"25"`` → 25、``"1.5"`` → 1.5、``"true"`` → True；其他保持字符串。"""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if _INT_RE.fullmatch(s):
        try:
            return int(s)
        except ValueError:
            pass
    if _FLOAT_RE.fullmatch(s):
        try:
            return float(s)
        except ValueError:
            pass
    return value


def _maybe_infer(obj, types: bool):
    if not types:
        return obj
    if isinstance(obj, list):
        out = []
        for row in obj:
            if isinstance(row, dict):
                out.append({k: _infer_scalar(v) for k, v in row.items()})
            elif isinstance(row, list):
                out.append([_infer_scalar(v) for v in row])
            else:
                out.append(_infer_scalar(row))
        return out
    return obj


def _load_csv(text: str, opts) -> list[Any]:
    """解析 CSV。``from_delimiter`` 指定读入分隔符（缺省自动嗅探），
    ``types=true`` 时把数字/布尔列还原成原生类型。"""
    delimiter = opt_str(opts, "from_delimiter", "")
    if not delimiter:
        delimiter = _sniff_delimiter(text, ",")
    header = opt_bool(opts, "header", True)
    try:
        if header:
            reader = _csv.DictReader(io.StringIO(text), delimiter=delimiter)
            rows: list[Any] = [dict(row) for row in reader]
        else:
            reader = _csv.reader(io.StringIO(text), delimiter=delimiter)
            rows = [list(row) for row in reader]
    except _csv.Error as exc:
        raise ConversionError(f"CSV 解析失败：{exc}") from exc
    return _maybe_infer(rows, opt_bool(opts, "types", False))


def _load_tsv(text: str, opts) -> list[Any]:
    try:
        reader = _csv.reader(io.StringIO(text), delimiter="\t")
        rows = [list(row) for row in reader]
    except _csv.Error as exc:
        raise ConversionError(f"TSV 解析失败：{exc}") from exc
    if rows and all(len(r) == len(rows[0]) for r in rows):
        header = rows[0]
        obj: Any = [dict(zip(header, r)) for r in rows[1:]]
    else:
        obj = rows
    return _maybe_infer(obj, opt_bool(opts, "types", False))


_LOADERS = {
    "json": _load_json,
    "yaml": _load_yaml,
    "toml": _load_toml,
    "xml": _load_xml,
    "ini": _load_ini,
    "jsonl": _load_jsonl,
}


# =========================================================================== #
# 序列化：Python 对象 -> 文本/字节
# =========================================================================== #
def _json_default(value: Any):
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _dump_json(obj: Any, opts) -> str:
    indent = opt_int(opts, "indent", 2)
    if indent < 0:
        indent = None  # type: ignore[assignment]
    return json.dumps(
        obj,
        indent=indent,
        ensure_ascii=opt_bool(opts, "ensure_ascii", False),
        sort_keys=opt_bool(opts, "sort_keys", False),
        default=_json_default,
    ) + "\n"


def _dump_yaml(obj: Any, opts) -> str:
    need_dep("yaml", why="YAML 解析/生成需要 PyYAML")
    import yaml

    return yaml.safe_dump(
        obj,
        allow_unicode=True,
        sort_keys=opt_bool(opts, "sort_keys", False),
        default_flow_style=opt_bool(opts, "flow_style", False),
        indent=opt_int(opts, "indent", 2),
    )


_TOML_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        escaped = (
            v.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise ConversionError(f"TOML 不支持的值：{v!r}（{type(v).__name__}）")


def _dump_toml(obj: Any, opts) -> str:
    """最小可用 TOML 写出器：标量、数组、嵌套表、表数组。"""
    if not isinstance(obj, dict):
        raise ConversionError("TOML 顶层必须是表（键值对对象）")

    lines: list[str] = []

    def emit_table(table: dict, prefix: list[str]) -> None:
        scalars = {k: v for k, v in table.items()
                   if not isinstance(v, dict) and not _is_table_array(v)}
        subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
        arrays = {k: v for k, v in table.items() if _is_table_array(v)}

        if prefix:
            key = ".".join(_toml_key(p) for p in prefix)
            lines.append(f"[{key}]")
        for k, v in scalars.items():
            lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
        for k, arr in arrays.items():
            for item in arr:
                full = ".".join(_toml_key(p) for p in prefix + [k])
                lines.append(f"[[{full}]]")
                sub_scalars = {kk: vv for kk, vv in item.items()
                               if not isinstance(vv, dict) and not _is_table_array(vv)}
                for kk, vv in sub_scalars.items():
                    lines.append(f"{_toml_key(kk)} = {_toml_value(vv)}")
        for k, v in subtables.items():
            if lines:
                lines.append("")
            emit_table(v, prefix + [k])

    def _is_table_array(v: Any) -> bool:
        return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)

    def _toml_key(k: str) -> str:
        return k if _TOML_BARE.match(k) else _toml_value(k)

    emit_table(obj, [])
    return "\n".join(lines) + "\n"


def _obj_to_xml_elem(tag: str, value: Any) -> ET.Element:
    elem = ET.Element(tag)
    if isinstance(value, dict):
        for k, v in value.items():
            if k.startswith("@"):
                elem.set(k[1:], "" if v is None else str(v))
            elif k == "#text":
                elem.text = "" if v is None else str(v)
        for k, v in value.items():
            if k.startswith("@") or k == "#text":
                continue
            if isinstance(v, list):
                for item in v:
                    elem.append(_obj_to_xml_elem(k, item))
            else:
                elem.append(_obj_to_xml_elem(k, v))
    elif isinstance(value, list):
        for item in value:
            elem.append(_obj_to_xml_elem("item", item))
    else:
        elem.text = "" if value is None else str(value)
    return elem


def _indent_xml(elem: ET.Element, level: int = 0, step: str = "  ") -> None:
    pad = "\n" + step * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + step
        for child in elem:
            _indent_xml(child, level + 1, step)
            if not child.tail or not child.tail.strip():
                child.tail = pad + step
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = pad
    elif level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


def _dump_xml(obj: Any, opts) -> str:
    root_tag = opt_str(opts, "root", "root")
    if isinstance(obj, dict) and len(obj) == 1:
        (root_tag, obj), = obj.items()
    elem = _obj_to_xml_elem(root_tag, obj)
    if opt_bool(opts, "indent", True):
        _indent_xml(elem)
    header = opt_bool(opts, "declaration", True)
    body = ET.tostring(elem, encoding="unicode")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n' if header else body + "\n")


def _dump_ini(obj: Any, opts) -> str:
    if not isinstance(obj, dict):
        raise ConversionError("INI 顶层必须是 {节名: {键: 值}} 结构")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # 保留键名大小写
    defaults = obj.get("__default__")
    if isinstance(defaults, dict):
        parser[DEFAULTSECT] = {str(k): _ini_scalar(v) for k, v in defaults.items()}
    for section, values in obj.items():
        if section == "__default__":
            continue
        if not isinstance(values, dict):
            raise ConversionError(f"INI 节 {section!r} 的值必须是键值对")
        parser[str(section)] = {str(k): _ini_scalar(v) for k, v in values.items()}
    buf = io.StringIO()
    parser.write(buf)
    return buf.getvalue()


DEFAULTSECT = configparser.DEFAULTSECT


def _ini_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _dump_plist(obj: Any, opts) -> bytes:
    fmt = opt_str(opts, "format", "xml").lower()
    fmt_type = plistlib.FMT_BINARY if fmt == "binary" else plistlib.FMT_XML
    if not isinstance(obj, dict):
        obj = {"items": obj}
    try:
        return plistlib.dumps(obj, fmt=fmt_type, sort_keys=opt_bool(opts, "sort_keys", False))
    except (TypeError, OverflowError) as exc:
        raise ConversionError(f"对象无法写成 plist：{exc}") from exc


def _dump_jsonl(obj: Any, opts) -> str:
    if not isinstance(obj, list):
        raise ConversionError("JSONL 需要数组（每行一条记录）作为顶层结构")
    lines = [
        json.dumps(item, ensure_ascii=opt_bool(opts, "ensure_ascii", False), default=_json_default)
        for item in obj
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _rows_jsonl_to_csv(obj: Any) -> list[Any]:
    return obj if isinstance(obj, list) else [obj]


def _dump_csv(obj: Any, opts, *, force_delimiter: str = "") -> str:
    delimiter = force_delimiter or opt_str(opts, "delimiter", ",")
    quotechar = opt_str(opts, "quotechar", '"') or '"'
    buf = io.StringIO(newline="")
    writer = _csv.writer(buf, delimiter=delimiter, quotechar=quotechar, lineterminator="\n")

    rows = _rows_jsonl_to_csv(obj)
    if rows and isinstance(rows[0], dict):
        fieldnames: dict[str, None] = {}
        for row in rows:
            if isinstance(row, dict):
                for k in row:
                    fieldnames.setdefault(str(k), None)
        header = list(fieldnames)
        if opt_bool(opts, "header", True):
            writer.writerow(header)
        for row in rows:
            if isinstance(row, dict):
                writer.writerow([_csv_scalar(row.get(k, "")) for k in header])
            else:
                writer.writerow([_csv_scalar(row)])
    elif isinstance(obj, dict):
        writer.writerow(["key", "value"])
        for k, v in obj.items():
            writer.writerow([k, _csv_scalar(v)])
    elif rows and isinstance(rows[0], (list, tuple)):
        for row in rows:
            writer.writerow([_csv_scalar(c) for c in row])
    else:
        writer.writerow([_csv_scalar(obj)])
    return buf.getvalue()


def _csv_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _dump_tsv(obj: Any, opts) -> str:
    return _dump_csv(obj, opts, force_delimiter="\t")


_DUMPERS = {
    "json": _dump_json,
    "yaml": _dump_yaml,
    "toml": _dump_toml,
    "xml": _dump_xml,
    "ini": _dump_ini,
    "jsonl": _dump_jsonl,
    "csv": _dump_csv,
    "tsv": _dump_tsv,
}


# =========================================================================== #
# 转换主函数
# =========================================================================== #
def convert_data(src: Path, dst: Path, opts, src_fmt: str, dst_fmt: str) -> Path:
    """数据族转换的统一入口（由注册工厂按对绑定格式名）。"""
    # ---- 解析 ----
    if src_fmt == "plist":
        obj = _load_plist(read_bytes(src))
    elif src_fmt in ("csv", "tsv"):
        text = decode_text(read_bytes(src), opt_str(opts, "encoding", _DEFAULT_ENCODING) or _DEFAULT_ENCODING)
        obj = _load_csv(text, opts) if src_fmt == "csv" else _load_tsv(text, opts)
    elif src_fmt in _LOADERS:
        text = decode_text(read_bytes(src), opt_str(opts, "encoding", _DEFAULT_ENCODING) or _DEFAULT_ENCODING)
        obj = _LOADERS[src_fmt](text)
    else:  # pragma: no cover - 注册表保证不会出现
        raise ConversionError(f"数据族不支持读取 {src_fmt}")

    # ---- 序列化 ----
    if dst_fmt == "plist":
        return write_bytes(dst, _dump_plist(obj, opts))
    if dst_fmt == "tsv":
        return write_text(dst, _dump_tsv(obj, opts), opts)
    if dst_fmt in _DUMPERS:
        return write_text(dst, _DUMPERS[dst_fmt](obj, opts), opts)
    raise ConversionError(f"数据族不支持写出 {dst_fmt}")  # pragma: no cover


def register_data_converters() -> int:
    """注册数据族全连通边（含自环：重格式化）。返回注册边数。"""
    count = 0
    for sf in DATA_FORMATS:
        for df in DATA_FORMATS:
            deps: tuple[str, ...] = ()
            # YAML 读写都需要 PyYAML
            if "yaml" in (sf, df):
                deps = ("yaml",)

            def make(sf=sf, df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_data(src, dst, opts, sf, df)
                conv.__name__ = f"data_{sf}_to_{df}"
                return conv

            REGISTRY.add(
                sf, df, make(),
                deps=deps,
                group="data",
                allow_self=True,
                note="结构化数据互转" if sf != df else "重新格式化",
            )
            count += 1
    return count
