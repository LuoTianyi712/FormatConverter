"""字幕互转：SRT / ASS / SSA / VTT / LRC。

统一模型：``Cue(start_ms, end_ms, text, style)``。
转换 = 解析成 Cue 列表 → 序列化，天然支持任意两种格式互转。
支持 ``shift`` 选项整体平移时间轴（秒，可为负）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import opt_float, read_text, write_text

SUBTITLE_FORMATS = ["srt", "ass", "ssa", "vtt", "lrc",
                    "microdvd", "ttml", "sbv", "smi"]

__all__ = ["Cue", "convert_subtitle", "parse_subtitle", "dump_subtitle",
           "merge_subtitles"]


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str
    style: str = "Default"

    def shifted(self, delta_s: float) -> "Cue":
        delta = int(round(delta_s * 1000))
        return Cue(max(0, self.start_ms + delta), max(0, self.end_ms + delta),
                   self.text, self.style)


# --------------------------------------------------------------------------- #
# 时间格式
# --------------------------------------------------------------------------- #
def _ms_from_srt(ts: str) -> int:
    m = re.match(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})", ts.strip())
    if not m:
        raise ConversionError(f"无法解析 SRT 时间戳：{ts!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600000 + int(mi) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0"))


def _srt_ts(ms: int) -> str:
    h, rem = divmod(max(0, ms), 3600000)
    mi, rem = divmod(rem, 60000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d},{msec:03d}"


def _vtt_ts(ms: int) -> str:
    return _srt_ts(ms).replace(",", ".")


def _ms_from_ass(ts: str) -> int:
    m = re.match(r"(\d+):(\d{1,2}):(\d{1,2})[.:](\d{1,2})", ts.strip())
    if not m:
        raise ConversionError(f"无法解析 ASS 时间戳：{ts!r}")
    h, mi, s, cs = m.groups()
    return int(h) * 3600000 + int(mi) * 60000 + int(s) * 1000 + int(cs.ljust(2, "0")) * 10


def _ass_ts(ms: int) -> str:
    h, rem = divmod(max(0, ms), 3600000)
    mi, rem = divmod(rem, 60000)
    s, msec = divmod(rem, 1000)
    return f"{h:d}:{mi:02d}:{s:02d}.{msec // 10:02d}"


def _ms_from_lrc(ts: str) -> int:
    m = re.match(r"(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?", ts.strip())
    if not m:
        raise ConversionError(f"无法解析 LRC 时间戳：{ts!r}")
    mi, s, frac = m.groups()
    frac_ms = 0
    if frac:
        frac_ms = int(frac.ljust(2, "0")[:2]) * 10
    return int(mi) * 60000 + int(s) * 1000 + frac_ms


def _lrc_ts(ms: int) -> str:
    mi, rem = divmod(max(0, ms), 60000)
    s, msec = divmod(rem, 1000)
    return f"[{mi:02d}:{s:02d}.{msec // 10:02d}]"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.strip().lstrip("\ufeff"))
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        if re.match(r"^\d+$", lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue
        m = re.match(r"(.+?)\s*-->\s*(\S+)", lines[0])
        if not m:
            continue
        start, end = _ms_from_srt(m.group(1)), _ms_from_srt(m.group(2))
        cues.append(Cue(start, end, "\n".join(lines[1:]).strip()))
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 SRT 字幕块")
    return cues


def parse_vtt(text: str) -> list[Cue]:
    lines = text.replace("\ufeff", "").splitlines()
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) or not line:
            i += 1
            continue
        if "-->" in line:
            m = re.match(r"(.+?)\s*-->\s*(\S+)", line)
            if m:
                start, end = _ms_from_srt(m.group(1)), _ms_from_srt(m.group(2))
                body: list[str] = []
                i += 1
                while i < len(lines) and lines[i].strip():
                    body.append(lines[i])
                    i += 1
                cues.append(Cue(start, end, "\n".join(body).strip()))
                continue
        i += 1
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 WebVTT 字幕块")
    return cues


_ASS_SECTION = re.compile(r"\[([^\]]+)\]")


def parse_ass(text: str) -> list[Cue]:
    cues: list[Cue] = []
    section = ""
    fields: list[str] = []
    for raw in text.replace("\ufeff", "").splitlines():
        line = raw.strip()
        msec = _ASS_SECTION.match(line)
        if msec:
            section = msec.group(1).lower()
            continue
        if section != "events":
            continue
        if line.lower().startswith("format:"):
            fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            continue
        if line.lower().startswith("dialogue:"):
            if not fields:
                fields = ["layer", "start", "end", "style", "name",
                          "marginl", "marginr", "marginv", "effect", "text"]
            parts = line.split(":", 1)[1].split(",", len(fields) - 1)
            if len(parts) < len(fields):
                continue
            row = dict(zip(fields, [p.strip() for p in parts[:-1]] + [parts[-1]]))
            try:
                start = _ms_from_ass(row.get("start", ""))
                end = _ms_from_ass(row.get("end", ""))
            except ConversionError:
                continue
            body = _ass_text_to_plain(parts[-1])
            cues.append(Cue(start, end, body, style=row.get("style", "Default") or "Default"))
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 ASS/SSA 事件行")
    return cues


_ASS_TAG = re.compile(r"\{[^}]*\}")
_ASS_ESCAPE = {"\\N": "\n", "\\n": "\n", "\\h": " "}


def _ass_text_to_plain(text: str) -> str:
    """去掉样式覆盖块 {\\...}，处理 \\N 换行。"""
    text = _ASS_TAG.sub("", text)
    for k, v in _ASS_ESCAPE.items():
        text = text.replace(k, v)
    return text.strip()


def parse_lrc(text: str) -> list[Cue]:
    pattern = re.compile(r"\[(\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?)\]")
    entries: list[tuple[int, str]] = []
    for line in text.replace("\ufeff", "").splitlines():
        stamps = pattern.findall(line)
        if not stamps:
            continue
        body = pattern.sub("", line).strip()
        for st in stamps:
            entries.append((_ms_from_lrc(st), body))
    if not entries:
        if text.strip():
            raise ConversionError("没有解析出任何 LRC 时间标签")
        return []
    entries.sort(key=lambda x: x[0])
    cues: list[Cue] = []
    for idx, (ms, body) in enumerate(entries):
        end = entries[idx + 1][0] if idx + 1 < len(entries) else ms + 4000
        if end <= ms:
            end = ms + 2000
        cues.append(Cue(ms, end, body))
    return cues


# --------------------------------------------------------------------------- #
# MicroDVD（.sub，帧格式）—— 规范确认：{start}{end}text，
# 首行 {1}{1}fps 声明帧率（默认 25），| 为换行，{y:i} 等样式标签
# --------------------------------------------------------------------------- #
def parse_microdvd(text: str) -> list[Cue]:
    cues: list[Cue] = []
    fps = 25.0
    pattern = re.compile(r"^\{(\d+)\}\{(\d+)\}(.*)$")
    for line in text.replace("\ufeff", "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if not m:
            continue
        start_f, end_f, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        if start_f == 1 and end_f == 1:
            try:
                v = float(rest.strip())
                if 0 < v < 1000:
                    fps = v
                    continue
            except ValueError:
                pass
        rest = re.sub(r"\{[^}]*\}", "", rest)
        body = rest.replace("|", "\n").strip()
        cues.append(Cue(int(start_f / fps * 1000), int(end_f / fps * 1000), body))
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 MicroDVD 字幕行")
    return cues


def dump_microdvd(cues: list[Cue], fps: float = 25.0) -> str:
    lines = [f"{{1}}{{1}}{fps:g}".replace(".0", "") if fps != 25.0 else "{1}{1}25"]
    for cue in cues:
        start_f = round(cue.start_ms / 1000 * fps)
        end_f = round(cue.end_ms / 1000 * fps)
        body = cue.text.replace("\n", "|")
        lines.append(f"{{{start_f}}}{{{end_f}}}{body}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# TTML / DFXP（W3C Timed Text）
# --------------------------------------------------------------------------- #
def parse_ttml(text: str) -> list[Cue]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ConversionError(f"TTML 解析失败：{exc}") from exc
    ns = "{http://www.w3.org/ns/ttml}"

    def to_ms(value: str) -> int:
        value = (value or "").strip()
        m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})(?:[.,](\d+))?", value)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
            frac = int(m.group(4).ljust(3, "0")[:3]) if m.group(4) else 0
            return h * 3600000 + mi * 60000 + s * 1000 + frac
        m = re.fullmatch(r"([\d.]+)(ms|s|m|h)?", value)
        if m:
            num = float(m.group(1))
            unit = m.group(2) or "s"
            return int(num * {"ms": 1, "s": 1000, "m": 60000, "h": 3600000}[unit])
        return 0

    cues: list[Cue] = []
    for p in root.iter(ns + "p"):
        begin = to_ms(p.get("begin", ""))
        end_ms = to_ms(p.get("end", "")) or to_ms(p.get("dur", ""))
        parts: list[str] = []
        if p.text:
            parts.append(p.text)
        for child in p:
            if child.tag == ns + "br":
                parts.append("\n")
            else:
                if child.text:
                    parts.append(child.text)
            if child.tail:
                parts.append(child.tail)
        body = "".join(parts).strip()
        if end_ms <= begin:
            end_ms = begin + 2000
        cues.append(Cue(begin, end_ms, body))
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 TTML 字幕")
    return cues


def dump_ttml(cues: list[Cue]) -> str:
    def ts(ms: int) -> str:
        return _srt_ts(ms).replace(",", ".")

    from xml.sax.saxutils import escape

    body = "\n".join(
        f'      <p begin="{ts(c.start_ms)}" end="{ts(c.end_ms)}">'
        f"{escape(c.text).replace(chr(10), '<br/>')}</p>"
        for c in cues
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="zh">\n'
        "  <body><div>\n" + body + "\n  </div></body>\n</tt>\n"
    )


# --------------------------------------------------------------------------- #
# SBV（YouTube）与 SAMI（.smi）
# --------------------------------------------------------------------------- #
def parse_sbv(text: str) -> list[Cue]:
    blocks = re.split(r"\n\s*\n", text.strip().lstrip("\ufeff"))
    cues: list[Cue] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        m = re.match(r"\s*(\d+:\d{2}:\d{2}[.,]\d{3})\s*,\s*(\d+:\d{2}:\d{2}[.,]\d{3})",
                     lines[0])
        if not m:
            continue
        cues.append(Cue(_ms_from_srt(m.group(1)), _ms_from_srt(m.group(2)),
                        "\n".join(lines[1:]).strip()))
    if not cues and text.strip():
        raise ConversionError("没有解析出任何 SBV 字幕块")
    return cues


def dump_sbv(cues: list[Cue]) -> str:
    blocks = [
        f"{_srt_ts(c.start_ms).replace(',', '.')},"
        f"{_srt_ts(c.end_ms).replace(',', '.')}\n{c.text}"
        for c in cues
    ]
    return "\n\n".join(blocks) + "\n"


def parse_smi(text: str) -> list[Cue]:
    entries: list[tuple[int, str]] = []
    for m in re.finditer(
            r"<sync[^>]*\bstart\s*=\s*(\d+)[^>]*>(.*?)(?=<sync|</body|$)",
            text, re.S | re.I):
        start = int(m.group(1))
        body_html = m.group(2)
        body = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.I)
        body = re.sub(r"<[^>]+>", "", body)
        import html as _html_mod

        body = _html_mod.unescape(body)
        body = re.sub(r"&nbsp;", " ", body)
        entries.append((start, body.strip()))
    if not entries:
        if text.strip():
            raise ConversionError("没有解析出任何 SAMI（.smi）字幕条目")
        return []
    cues: list[Cue] = []
    for i, (start, body) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + 4000
        cues.append(Cue(start, max(end, start + 500), body))
    return [c for c in cues if c.text]


def dump_smi(cues: list[Cue]) -> str:
    from xml.sax.saxutils import escape

    blocks = []
    for cue in cues:
        body = escape(cue.text).replace("\n", "<br>")
        blocks.append(
            f"<SYNC Start={cue.start_ms}><P Class=ZH>{body}\n"
            f"<SYNC Start={cue.end_ms}><P Class=ZH>&nbsp;"
        )
    return (
        "<SAMI>\n<HEAD><STYLE><!--\nP { font-family: 微软雅黑; }"
        "\n--></STYLE></HEAD>\n<BODY>\n" + "\n".join(blocks)
        + "\n</BODY>\n</SAMI>\n"
    )


_PARSERS = {
    "srt": parse_srt,
    "vtt": parse_vtt,
    "ass": parse_ass,
    "ssa": parse_ass,
    "lrc": parse_lrc,
    "microdvd": parse_microdvd,
    "ttml": parse_ttml,
    "sbv": parse_sbv,
    "smi": parse_smi,
}


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def dump_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        blocks.append(f"{i}\n{_srt_ts(cue.start_ms)} --> {_srt_ts(cue.end_ms)}\n{cue.text}")
    return "\n\n".join(blocks) + "\n"


def dump_vtt(cues: list[Cue]) -> str:
    blocks = [
        f"{_vtt_ts(c.start_ms)} --> {_vtt_ts(c.end_ms)}\n{c.text}"
        for c in cues
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


_ASS_HEADER = """[Script Info]
; 由 UniConvert 生成
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,52,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def dump_ass(cues: list[Cue], *, ssa: bool = False) -> str:
    header = _ASS_HEADER
    if ssa:
        header = header.replace("V4+ Styles", "V4 Styles").replace("v4.00+", "v4.00")
    lines = []
    for cue in cues:
        body = cue.text.replace("\n", "\\N")
        style = cue.style or "Default"
        lines.append(
            f"Dialogue: 0,{_ass_ts(cue.start_ms)},{_ass_ts(cue.end_ms)},"
            f"{style},,0,0,0,,{body}"
        )
    return header + "\n".join(lines) + "\n"


def dump_lrc(cues: list[Cue]) -> str:
    lines = []
    for cue in cues:
        body = cue.text.replace("\n", " ")
        lines.append(f"{_lrc_ts(cue.start_ms)}{body}")
    return "\n".join(lines) + "\n"


_DUMPERS = {
    "srt": dump_srt,
    "vtt": dump_vtt,
    "ass": lambda cues: dump_ass(cues),
    "ssa": lambda cues: dump_ass(cues, ssa=True),
    "lrc": dump_lrc,
    "microdvd": lambda cues: dump_microdvd(cues),
    "ttml": dump_ttml,
    "sbv": dump_sbv,
    "smi": dump_smi,
}

#: 全角去重：LRC 与 ASS 是样式强绑定格式，转换时给用户提醒
_REMARK = {
    ("ass", "lrc"): "ASS 样式信息在 LRC 中丢失",
    ("ssa", "lrc"): "SSA 样式信息在 LRC 中丢失",
    ("ass", "srt"): "ASS 样式信息在 SRT 中丢失",
    ("ssa", "srt"): "SSA 样式信息在 SRT 中丢失",
    ("lrc", "ass"): "LRC 无结束时间，按下一句起始推定",
}


def parse_subtitle(path: Path, fmt: str, opts) -> list[Cue]:
    text = read_text(path, opts, default_encoding="utf-8-sig")
    parser = _PARSERS.get(fmt)
    if parser is None:  # pragma: no cover
        raise ConversionError(f"不支持的字幕格式：{fmt}")
    return parser(text)


def dump_subtitle(cues: list[Cue], fmt: str) -> str:
    dumper = _DUMPERS.get(fmt)
    if dumper is None:  # pragma: no cover
        raise ConversionError(f"不支持的字幕格式：{fmt}")
    return dumper(cues)


_HI_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）|【[^】]*】")


def remove_hi_annotations(text: str) -> str:
    """去掉听力障碍标注：``[door slams]`` / ``（音乐）`` 等。"""
    return re.sub(r"\s{2,}", " ", _HI_RE.sub("", text)).strip()


def convert_subtitle(src: Path, dst: Path, opts, src_fmt: str, dst_fmt: str) -> Path:
    cues = parse_subtitle(src, src_fmt, opts)
    shift = opt_float(opts, "shift", 0.0)
    if shift:
        cues = [c.shifted(shift) for c in cues]
    scale = opt_float(opts, "scale", 1.0)
    if scale and abs(scale - 1.0) > 1e-9:
        cues = [Cue(int(c.start_ms * scale), int(c.end_ms * scale),
                    c.text, c.style) for c in cues]
    from ._util import opt_bool

    if opt_bool(opts, "remove_hi", False) or opt_bool(opts, "no_hi", False):
        cleaned = []
        for c in cues:
            text = remove_hi_annotations(c.text)
            if text:
                cleaned.append(Cue(c.start_ms, c.end_ms, text, c.style))
        cues = cleaned
    cues.sort(key=lambda c: c.start_ms)
    return write_text(dst, dump_subtitle(cues, dst_fmt), opts)


def merge_subtitles(
    first: str | Path,
    second: str | Path,
    dst: str | Path,
    *,
    mode: str = "bilingual",
    fmt: str = "",
    gap_ms: int = 5,
    options: Options | dict | None = None,
) -> Path:
    """合并两个字幕文件。

    * ``mode="bilingual"``：按时间就近配对成双语（原文\n译文）；
    * ``mode="concat"``：第二个文件整体接在第一个之后（时间平移）。
    """
    from ..core.job import Options as _Options

    opts = _Options.from_any(options)
    a_path, b_path = Path(first), Path(second)
    fmt = (fmt or b_path.suffix.lstrip(".") or a_path.suffix.lstrip(".")).lower()
    if fmt not in _DUMPERS:
        raise ConversionError(f"不支持的字幕格式：{fmt}")
    a = parse_subtitle(a_path, fmt, opts)
    b = parse_subtitle(b_path, fmt, opts)

    if mode == "concat":
        offset = (max(c.end_ms for c in a) if a else 0) + 500
        merged = list(a) + [Cue(c.start_ms + offset, c.end_ms + offset,
                                c.text, c.style) for c in b]
    else:  # bilingual：以 A 为轴，找时间重叠最大的 B 句挂第二行
        used: set[int] = set()
        merged = []
        for cue in a:
            best_idx, best_overlap = -1, 0
            for idx, other in enumerate(b):
                if idx in used:
                    continue
                overlap = min(cue.end_ms, other.end_ms) - max(cue.start_ms, other.start_ms)
                if overlap >= best_overlap:
                    best_idx, best_overlap = idx, overlap
            text = cue.text
            if best_idx >= 0 and (best_overlap > 0 or best_idx not in used):
                used.add(best_idx)
                text = f"{cue.text}\n{b[best_idx].text}"
            merged.append(Cue(cue.start_ms, cue.end_ms, text, cue.style))
    merged.sort(key=lambda c: c.start_ms)
    dst = Path(dst)
    return write_text(dst, dump_subtitle(merged, fmt), opts)


def _subtitle_plaintext(cues: list[Cue]) -> str:
    lines = []
    for cue in cues:
        lines.append(f"{_srt_ts(cue.start_ms)} → {_srt_ts(cue.end_ms)}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _subtitle_text_converter(dst_fmt: str):
    def conv(src: Path, dst: Path, opts) -> Path:
        fmt = src.suffix.lstrip(".").lower()
        if fmt not in _DUMPERS:
            fmt = "srt"
        cues = parse_subtitle(src, fmt, opts)
        text = _subtitle_plaintext(cues)
        if dst_fmt == "md":
            text = "\n".join(
                f"**{_srt_ts(c.start_ms)} → {_srt_ts(c.end_ms)}**\n\n{c.text}\n"
                for c in cues
            )
        return write_text(dst, text, opts)

    conv.__name__ = f"subtitle_to_{dst_fmt}"
    return conv


def register_subtitle_converters() -> int:
    count = 0
    for sf in SUBTITLE_FORMATS:
        for df in SUBTITLE_FORMATS:
            same = sf == df
            note = ("重写/平移/缩放时间轴（shift/scale 选项）" if same
                    else _REMARK.get((sf, df), ""))

            def make(sf=sf, df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_subtitle(src, dst, opts, sf, df)
                conv.__name__ = f"sub_{sf}_to_{df}"
                return conv

            REGISTRY.add(
                sf, df, make(),
                group="subtitle",
                note=note,
                allow_self=True,  # 自环 = 同格式重排（shift/scale/编码）
            )
            count += 1
    # 字幕 → 纯文本 / Markdown（docx 导出的链路基础：srt→txt→docx）
    for sf in SUBTITLE_FORMATS:
        for df in ("txt", "md"):
            REGISTRY.add(sf, df, _subtitle_text_converter(df),
                         group="subtitle", note="纯文本导出（含时间码）")
            count += 1
    return count
