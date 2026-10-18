"""格式注册表：UniConvert 认识的每一种格式及其元数据。

这里的数据结构是整个引擎的「世界地图」——
嗅探器按 :attr:`Format.magic` 判类型，
转换图按 :attr:`Format.category` 决定哪些转换是「同类无损」的，
CLI/GUI 按 :attr:`Format.exts` 决定文件对话框的过滤器。

新增格式只需要在 :data:`FORMATS` 里加一条，别处不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# --------------------------------------------------------------------------- #
# 分类常量
# --------------------------------------------------------------------------- #
VIDEO = "video"
AUDIO = "audio"
IMAGE = "image"
DOCUMENT = "document"
EBOOK = "ebook"
SPREADSHEET = "spreadsheet"
DATA = "data"
ARCHIVE = "archive"
SUBTITLE = "subtitle"
FONT = "font"
VECTOR = "vector"
EGOBOOK = "ebook"
THREED = "3d"
BINARY = "binary"
TEXT = "text"

CATEGORIES = (
    VIDEO, AUDIO, IMAGE, DOCUMENT, EBOOK, SPREADSHEET, DATA,
    ARCHIVE, SUBTITLE, FONT, VECTOR, THREED, BINARY, TEXT,
)

CATEGORY_LABELS = {
    VIDEO: "视频",
    AUDIO: "音频",
    IMAGE: "图片",
    DOCUMENT: "文档",
    EBOOK: "电子书",
    SPREADSHEET: "表格",
    DATA: "结构化数据",
    ARCHIVE: "压缩包",
    SUBTITLE: "字幕",
    FONT: "字体",
    VECTOR: "矢量图",
    THREED: "3D 模型",
    BINARY: "二进制/编码",
    TEXT: "纯文本",
}


# --------------------------------------------------------------------------- #
# Format
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Format:
    """一种文件格式的元数据。

    Attributes:
        name: 规范名（转换图里的节点 id），如 ``"png"``。
        category: 所属大类，见 :data:`CATEGORIES`。
        exts: 常见扩展名（小写，不含点），首个为主扩展名。
        mime: 常见 MIME 类型，用于 GUI 拖放 / HTTP 端推断。
        magic: ``(offset, bytes)`` 列表，用于文件头嗅探。
            ``offset`` 为 ``-1`` 表示「文件末尾往前数」。
        lossless: 同类互转是否无损（如 png↔bmp 无损，jpg↔png 有损）。
        text: 是否为文本格式（可被文本管道处理）。
        desc: 中文说明，展示给用户。
        aliases: 其他叫法，路由时归一到 ``name``。
    """

    name: str
    category: str
    exts: tuple[str, ...] = ()
    mime: str = "application/octet-stream"
    magic: tuple[tuple[int, bytes], ...] = ()
    lossless: bool = False
    text: bool = False
    desc: str = ""
    aliases: tuple[str, ...] = ()
    opts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary_ext(self) -> str:
        return self.exts[0] if self.exts else self.name

    def matches_ext(self, ext: str) -> bool:
        return ext.lower().lstrip(".") in self.exts


def _f(name, category, exts=(), mime="application/octet-stream", magic=(), **kw) -> Format:
    return Format(
        name=name,
        category=category,
        exts=tuple(exts),
        mime=mime,
        magic=tuple(magic),
        desc=kw.pop("desc", ""),
        lossless=kw.pop("lossless", False),
        text=kw.pop("text", False),
        aliases=tuple(kw.pop("aliases", ())),
        opts=tuple(kw.pop("opts", ())),
    )


# --------------------------------------------------------------------------- #
# 格式清单
# --------------------------------------------------------------------------- #
_FORMAT_LIST: list[Format] = [
    # ---------------- 图片 ----------------
    _f("jpg", IMAGE, ["jpg", "jpeg", "jpe"], "image/jpeg",
       [(0, b"\xff\xd8\xff")], lossless=False, desc="JPEG 有损照片，兼容性最好",
       aliases=["jpeg"], opts=["quality", "progressive", "exif"]),
    _f("png", IMAGE, ["png"], "image/png",
       [(0, b"\x89PNG\r\n\x1a\n")], lossless=True, desc="PNG 无损位图，支持透明",
       opts=["compress_level"]),
    _f("webp", IMAGE, ["webp"], "image/webp",
       [(0, b"RIFF"), ], lossless=False, desc="WebP，体积小，现代浏览器通用",
       opts=["quality", "lossless"]),
    _f("avif", IMAGE, ["avif"], "image/avif", [], lossless=False,
       desc="AVIF，新一代压缩，体积最小", opts=["quality"]),
    _f("heic", IMAGE, ["heic", "heif"], "image/heic", [], lossless=False,
       desc="HEIC/HEIF，苹果默认拍照格式", aliases=["heif"]),
    _f("bmp", IMAGE, ["bmp"], "image/bmp", [(0, b"BM")], lossless=True,
       desc="BMP 无压缩位图"),
    _f("gif", IMAGE, ["gif"], "image/gif", [(0, b"GIF87a"), (0, b"GIF89a")],
       lossless=True, desc="GIF 动图/低色位图"),
    _f("tiff", IMAGE, ["tif", "tiff"], "image/tiff",
       [(0, b"II*\x00"), (0, b"MM\x00*")], lossless=True,
       desc="TIFF 印刷/扫描常用", aliases=["tif"]),
    _f("ico", IMAGE, ["ico"], "image/x-icon", [(0, b"\x00\x00\x01\x00")],
       lossless=True, desc="ICO 图标，可含多尺寸"),
    _f("tga", IMAGE, ["tga"], "image/x-tga", [], lossless=True, desc="TGA 纹理图"),
    _f("psd", IMAGE, ["psd"], "image/vnd.adobe.photoshop",
       [(0, b"8BPS")], lossless=True, desc="Photoshop 工程文件（合成预览层）"),
    _f("jp2", IMAGE, ["jp2"], "image/jp2", [], lossless=True, desc="JPEG 2000"),
    _f("qoi", IMAGE, ["qoi"], "image/qoi", [(0, b"qoif")], lossless=True,
       desc="QOI 极速无损格式"),
    _f("dds", IMAGE, ["dds"], "image/vnd-ms.dds", [(0, b"DDS ")], lossless=False,
       desc="DDS 游戏纹理"),

    # ---------------- 矢量 ----------------
    _f("svg", VECTOR, ["svg"], "image/svg+xml", [], text=True, lossless=True,
       desc="SVG 矢量图"),
    _f("pdf", VECTOR, ["pdf"], "application/pdf", [(0, b"%PDF-")],
       desc="PDF 便携文档", opts=["dpi", "pages", "password", "linearize"]),
    _f("eps", VECTOR, ["eps"], "application/postscript",
       [(0, b"%!PS-Adobe")], desc="EPS 印刷矢量"),
    _f("emf", VECTOR, ["emf"], "image/emf", [(0, b"\x01\x00\x00\x00")],
       desc="EMF 矢量（Office 图表）"),

    # ---------------- 视频 ----------------
    _f("mp4", VIDEO, ["mp4", "m4v"], "video/mp4",
       [(4, b"ftyp")], desc="MP4，通用性最强的视频容器",
       opts=["vcodec", "acodec", "crf", "bitrate", "resolution", "fps",
             "trim", "rotate", "hwaccel", "two_pass", "faststart"]),
    _f("mkv", VIDEO, ["mkv"], "video/x-matroska",
       [(0, b"\x1aE\xdf\xa3")], desc="MKV，多轨/字幕封装能力最强",
       opts=["vcodec", "acodec", "crf", "resolution", "fps", "trim"]),
    _f("avi", VIDEO, ["avi"], "video/x-msvideo",
       [(0, b"RIFF")], desc="AVI 老牌容器"),
    _f("mov", VIDEO, ["mov"], "video/quicktime", [(4, b"ftyp")],
       desc="MOV，苹果容器"),
    _f("webm", VIDEO, ["webm"], "video/webm", [(0, b"\x1aE\xdf\xa3")],
       desc="WebM，网页视频"),
    _f("flv", VIDEO, ["flv"], "video/x-flv", [(0, b"FLV")], desc="FLV 直播/老网页"),
    _f("wmv", VIDEO, ["wmv", "asf"], "video/x-ms-wmv",
       [(0, b"0&\xb2u")], desc="WMV，Windows Media"),
    _f("mpg", VIDEO, ["mpg", "mpeg"], "video/mpeg",
       [(0, b"\x00\x00\x01\xba")], desc="MPEG-1/2 视频"),
    _f("ts", VIDEO, ["ts", "mts", "m2ts"], "video/mp2t",
       [(0, b"G"), (188, b"G")], desc="MPEG-TS 流"),
    _f("m3u8", VIDEO, ["m3u8"], "application/vnd.apple.mpegurl",
       [(0, b"#EXTM3U")], text=True,
       desc="HLS 流媒体索引（转换产出 .m3u8 + 分片 .ts）",
       opts=["hls_time", "crf", "resolution", "audio_bitrate"]),
    _f("3gp", VIDEO, ["3gp"], "video/3gpp", [(4, b"ftyp")], desc="3GP 手机视频"),
    _f("ogv", VIDEO, ["ogv"], "video/ogg", [(0, b"OggS")], desc="Ogg 视频"),
    _f("mxf", VIDEO, ["mxf"], "application/mxf", [], desc="MXF 广电专业格式"),
    _f("gifv", VIDEO, ["gif"], "video/mp4", [], desc="动图转视频（GIF→MP4）"),

    # ---------------- 音频 ----------------
    _f("mp3", AUDIO, ["mp3"], "audio/mpeg",
       [(0, b"ID3"), (0, b"\xff\xfb"), (0, b"\xff\xf3")], desc="MP3，兼容性最好",
       opts=["bitrate", "vbr", "quality", "trim", "id3"]),
    _f("aac", AUDIO, ["aac", "m4a"], "audio/aac", [(4, b"ftyp")],
       desc="AAC/M4A", aliases=["m4a"], opts=["bitrate", "trim"]),
    _f("flac", AUDIO, ["flac"], "audio/flac", [(0, b"fLaC")], lossless=True,
       desc="FLAC 无损", opts=["compress_level", "trim"]),
    _f("wav", AUDIO, ["wav"], "audio/wav",
       [(0, b"RIFF")], lossless=True, desc="WAV 未压缩，编辑友好",
       opts=["sample_rate", "channels", "bit_depth", "trim"]),
    _f("ogg", AUDIO, ["ogg", "oga"], "audio/ogg", [(0, b"OggS")],
       desc="Ogg Vorbis", opts=["quality", "trim"]),
    _f("opus", AUDIO, ["opus"], "audio/opus", [(0, b"OggS")], desc="Opus 低码率之王"),
    _f("wma", AUDIO, ["wma"], "audio/x-ms-wma", [(0, b"0&\xb2u")], desc="WMA"),
    _f("aiff", AUDIO, ["aif", "aiff"], "audio/aiff",
       [(0, b"FORM")], lossless=True, desc="AIFF，苹果无损"),
    _f("alac", AUDIO, ["alac"], "audio/mp4", [], lossless=True, desc="ALAC 苹果无损"),
    _f("ape", AUDIO, ["ape"], "audio/x-ape", [(0, b"MAC ")], lossless=True,
       desc="Monkey's Audio 无损"),
    _f("m4b", AUDIO, ["m4b"], "audio/mp4", [], desc="M4B 有声书（带章节）"),
    _f("amr", AUDIO, ["amr"], "audio/amr", [(0, b"#!AMR")], desc="AMR 手机录音"),
    _f("ac3", AUDIO, ["ac3"], "audio/ac3", [(0, b"\x0b\x77")], desc="AC-3 环绕声"),
    _f("dts", AUDIO, ["dts"], "audio/vnd.dts", [(0, b"\x7f\xfe\x80\x01")], desc="DTS 环绕声"),
    _f("mid", AUDIO, ["mid", "midi"], "audio/midi", [(0, b"MThd")],
       desc="MIDI 乐器指令", aliases=["midi"]),

    # ---------------- 文档 ----------------
    _f("docx", DOCUMENT, ["docx"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       [(0, b"PK\x03\x04")], desc="Word 文档（新版）", opts=["title", "toc"]),
    _f("doc", DOCUMENT, ["doc"], "application/msword",
       [(0, b"\xd0\xcf\x11\xe0")], desc="Word 文档（97-2003）"),
    _f("pptx", DOCUMENT, ["pptx"], "application/vnd.openxmlformats-officedocument.presentationml.presentation",
       [(0, b"PK\x03\x04")], desc="PowerPoint 演示文稿（新版）"),
    _f("ppt", DOCUMENT, ["ppt"], "application/vnd.ms-powerpoint",
       [(0, b"\xd0\xcf\x11\xe0")], desc="PowerPoint 演示文稿（97-2003）"),
    _f("odt", DOCUMENT, ["odt"], "application/vnd.oasis.opendocument.text",
       [(0, b"PK\x03\x04")], desc="OpenDocument 文本文档"),
    _f("odp", DOCUMENT, ["odp"], "application/vnd.oasis.opendocument.presentation",
       [(0, b"PK\x03\x04")], desc="OpenDocument 演示文稿"),
    _f("rtf", DOCUMENT, ["rtf"], "application/rtf", [(0, b"{\\rtf")],
       text=True, desc="RTF 富文本"),
    _f("txt", TEXT, ["txt"], "text/plain", [], text=True, lossless=True,
       desc="纯文本", opts=["encoding", "newline"]),
    _f("md", DOCUMENT, ["md", "markdown"], "text/markdown", [], text=True,
       desc="Markdown", aliases=["markdown"]),
    _f("html", DOCUMENT, ["html", "htm"], "text/html", [], text=True,
       desc="HTML 网页", opts=["standalone", "css", "toc"]),
    _f("tex", DOCUMENT, ["tex"], "application/x-tex", [], text=True,
       desc="LaTeX 源码"),
    _f("epub", EBOOK, ["epub"], "application/epub+zip", [(0, b"PK\x03\x04")],
       desc="EPUB 电子书（流式）", opts=["title", "author", "cover", "toc"]),
    _f("mobi", EBOOK, ["mobi"], "application/x-mobipocket-ebook",
       [(60, b"BOOKMOBI")], desc="Mobipocket（Kindle 旧版）"),
    _f("azw3", EBOOK, ["azw3"], "application/vnd.amazon.ebook",
       [], desc="AZW3（Kindle 新版）"),
    _f("fb2", EBOOK, ["fb2"], "application/x-fictionbook+xml", [], text=True,
       desc="FictionBook 2"),
    _f("cbz", EBOOK, ["cbz"], "application/vnd.comicbook+zip", [(0, b"PK\x03\x04")],
       desc="漫画包（ZIP 图集）"),
    _f("cbr", EBOOK, ["cbr"], "application/vnd.comicbook-rar", [(0, b"Rar!")],
       desc="漫画包（RAR 图集）"),

    # ---------------- 表格 ----------------
    _f("xlsx", SPREADSHEET, ["xlsx"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
       [(0, b"PK\x03\x04")], desc="Excel 工作簿", opts=["sheet", "header", "formulas"]),
    _f("xls", SPREADSHEET, ["xls"], "application/vnd.ms-excel",
       [(0, b"\xd0\xcf\x11\xe0")], desc="Excel 97-2003"),
    _f("ods", SPREADSHEET, ["ods"], "application/vnd.oasis.opendocument.spreadsheet",
       [(0, b"PK\x03\x04")], desc="OpenDocument 表格"),
    _f("csv", SPREADSHEET, ["csv"], "text/csv", [], text=True,
       desc="逗号分隔值", opts=["delimiter", "encoding", "quotechar"]),
    _f("tsv", SPREADSHEET, ["tsv"], "text/tab-separated-values", [], text=True,
       desc="制表符分隔值"),

    # ---------------- 结构化数据 ----------------
    _f("json", DATA, ["json"], "application/json", [], text=True,
       desc="JSON", opts=["indent", "sort_keys", "ensure_ascii"]),
    _f("yaml", DATA, ["yaml", "yml"], "application/yaml", [], text=True,
       desc="YAML", aliases=["yml"], opts=["indent", "flow_style"]),
    _f("toml", DATA, ["toml"], "application/toml", [], text=True, desc="TOML"),
    _f("xml", DATA, ["xml"], "application/xml", [], text=True,
       desc="XML", opts=["indent", "pretty"]),
    _f("ini", DATA, ["ini", "cfg"], "text/plain", [], text=True, desc="INI 配置"),
    _f("plist", DATA, ["plist"], "application/x-plist",
       [(0, b"bplist")], desc="Apple plist"),
    _f("jsonl", DATA, ["jsonl", "ndjson"], "application/x-ndjson", [], text=True,
       desc="JSON Lines（每行一条记录）", aliases=["ndjson"]),
    _f("parquet", DATA, ["parquet"], "application/vnd.apache.parquet",
       [(0, b"PAR1")], desc="Parquet 列式存储"),
    _f("sql", DATA, ["sql"], "application/sql", [], text=True, desc="SQL 脚本"),
    _f("vcf", DATA, ["vcf"], "text/vcard", [], text=True, desc="vCard 联系人"),
    _f("ics", DATA, ["ics"], "text/calendar", [], text=True, desc="iCalendar 日程"),

    # ---------------- 字幕 ----------------
    _f("srt", SUBTITLE, ["srt"], "application/x-subrip", [], text=True,
       desc="SubRip 字幕", opts=["encoding", "fps", "shift"]),
    _f("ass", SUBTITLE, ["ass"], "text/x-ssa", [], text=True,
       desc="ASS 高级字幕（带样式）", opts=["encoding", "fps", "shift"]),
    _f("vtt", SUBTITLE, ["vtt"], "text/vtt", [], text=True,
       desc="WebVTT 网页字幕", opts=["encoding", "fps", "shift"]),
    _f("ssa", SUBTITLE, ["ssa"], "text/x-ssa", [], text=True, desc="SSA 字幕",
       aliases=["sub"]),
    _f("lrc", SUBTITLE, ["lrc"], "text/plain", [], text=True, desc="LRC 歌词"),

    # ---------------- 压缩包 ----------------
    _f("zip", ARCHIVE, ["zip"], "application/zip", [(0, b"PK\x03\x04")],
       desc="ZIP 压缩包", opts=["level", "password", "encryption"]),
    _f("7z", ARCHIVE, ["7z"], "application/x-7z-compressed",
       [(0, b"7z\xbc\xaf\x27\x1c")], desc="7-Zip 高压缩比"),
    _f("tar", ARCHIVE, ["tar"], "application/x-tar", [(257, b"ustar")],
       desc="TAR 归档"),
    _f("gz", ARCHIVE, ["gz", "tgz"], "application/gzip", [(0, b"\x1f\x8b")],
       desc="Gzip 单文件压缩", aliases=["tgz"]),
    _f("bz2", ARCHIVE, ["bz2"], "application/x-bzip2", [(0, b"BZh")], desc="Bzip2"),
    _f("xz", ARCHIVE, ["xz"], "application/x-xz", [(0, b"\xfd7zXZ\x00")], desc="XZ"),
    _f("zst", ARCHIVE, ["zst"], "application/zstd", [(0, b"\x28\xb5\x2f\xfd")],
       desc="Zstandard 极速压缩"),
    _f("rar", ARCHIVE, ["rar"], "application/vnd.rar", [(0, b"Rar!")],
       desc="RAR（仅解压）"),
    _f("cab", ARCHIVE, ["cab"], "application/vnd.ms-cab-compressed",
       [(0, b"MSCF")], desc="CAB（仅解压）"),

    # ---------------- 字体 ----------------
    _f("ttf", FONT, ["ttf"], "font/ttf", [(0, b"\x00\x01\x00\x00")],
       desc="TrueType 字体"),
    _f("otf", FONT, ["otf"], "font/otf", [(0, b"OTTO")], desc="OpenType 字体"),
    _f("woff", FONT, ["woff"], "font/woff", [(0, b"wOFF")], desc="WOFF 网页字体"),
    _f("woff2", FONT, ["woff2"], "font/woff2", [(0, b"wOF2")], desc="WOFF2 网页字体"),

    # ---------------- 3D / CAD ----------------
    _f("stl", THREED, ["stl"], "model/stl", [], desc="STL 3D 打印模型（仅顶点）"),
    _f("obj", THREED, ["obj"], "model/obj", [], text=True, desc="OBJ 3D 模型（带材质）"),
    _f("ply", THREED, ["ply"], "model/ply", [(0, b"ply")], desc="PLY 点云/网格"),
    _f("gltf", THREED, ["gltf"], "model/gltf+json", [], text=True, desc="glTF（文本）"),
    _f("glb", THREED, ["glb"], "model/gltf-binary", [(0, b"glTF")], desc="GLB（二进制）"),
    _f("dae", THREED, ["dae"], "model/vnd.collada+xml", [], text=True, desc="COLLADA"),
    _f("3mf", THREED, ["3mf"], "model/3mf", [(0, b"PK\x03\x04")], desc="3MF 打印格式"),
    _f("dxf", VECTOR, ["dxf"], "image/vnd.dxf", [], text=True, desc="DXF CAD 图纸"),

    # ---------------- 二进制 / 编码 ----------------
    _f("base64", BINARY, ["b64"], "text/plain", [], text=True,
       desc="Base64 文本", aliases=["b64"]),
    _f("hex", BINARY, ["hex"], "text/plain", [], text=True, desc="十六进制文本"),
    _f("bin", BINARY, ["bin"], "application/octet-stream", [], desc="裸二进制"),
    _f("quoted-printable", BINARY, ["qp"], "text/plain", [], text=True,
       desc="Quoted-Printable 编码", aliases=["qp"]),
    _f("urlencode", BINARY, ["urlenc"], "text/plain", [], text=True,
       desc="URL 百分号编码", aliases=["urlenc"]),
    _f("rot13", BINARY, ["rot13"], "text/plain", [], text=True, desc="ROT13 凯撒移位"),
    _f("hash", BINARY, ["hash"], "text/plain", [], text=True,
       desc="散列摘要（md5/sha1/sha256…）", opts=["algorithm", "hmac_key"]),
]

#: 规范名 -> Format
FORMATS: dict[str, Format] = {f.name: f for f in _FORMAT_LIST}

#: 别名 / 扩展名 -> 规范名
_ALIAS: dict[str, str] = {}
for _fmt in _FORMAT_LIST:
    _ALIAS[_fmt.name] = _fmt.name
    for _e in _fmt.exts:
        _ALIAS.setdefault(_e, _fmt.name)
    for _a in _fmt.aliases:
        _ALIAS[_a] = _fmt.name

#: 需要消歧的同名扩展名：gif 既是静态图又是动图视频
_AMBIGUOUS_EXT = {"gif"}


def known_names() -> list[str]:
    """返回全部规范格式名（排序后）。"""
    return sorted(FORMATS)


def _normalize(fmt: str) -> str:
    return fmt.strip().lower().lstrip(".")


def guess_format(name_or_path: str) -> Format | None:
    """按名字 / 扩展名猜格式，不做文件内容嗅探。

    >>> guess_format("photo.JPEG").name
    'jpg'
    >>> guess_format("data.yml").name
    'yaml'
    >>> guess_format("unknown.zzz") is None
    True
    """
    key = _normalize(name_or_path)
    if key in FORMATS:
        return FORMATS[key]
    if "." in key:
        ext = key.rsplit(".", 1)[1]
        if ext in _ALIAS:
            return FORMATS[_ALIAS[ext]]
    elif key in _ALIAS:
        return FORMATS[_ALIAS[key]]
    return None


def get_format(name: str, *, required: bool = True) -> Format | None:
    """取格式，``required=True`` 时取不到就抛异常。"""
    fmt = guess_format(name)
    if fmt is None and required:
        from ..errors import UnsupportedFormatError

        raise UnsupportedFormatError(name, known=known_names())
    return fmt


def formats_in(category: str) -> list[Format]:
    """列出某大类下的全部格式。"""
    return [f for f in _FORMAT_LIST if f.category == category]


def all_exts(categories: Iterable[str] | None = None) -> list[str]:
    """列出扩展名列表（可限定大类），用于文件对话框过滤器。"""
    cats = set(categories) if categories else None
    out: list[str] = []
    for f in _FORMAT_LIST:
        if cats and f.category not in cats:
            continue
        out.extend(f.exts)
    return sorted(set(out))
