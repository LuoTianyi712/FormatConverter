"""UniConvert 公共 API —— 其他代码只需要 import 这一层。

```python
from uniconvert import convert, convert_batch, list_formats, can_convert, route

convert("a.json", "a.yaml")                      # 文件对文件
convert("a.docx", to="pdf")                      # 原地换格式
convert("a.mp4", "b.gif", crf=18)                # 附带选项
```

所有函数在首次导入时自动注册全部内置转换器；
插件通过 :func:`uniconvert.plugins.load_plugins` 显式加载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import converters  # noqa: F401 - 导入即注册内置转换器
from .converters.image import (
    image_collage,
    image_compress_to_size,
    image_favicon,
    image_watermark,
    parse_size_bytes,
)
from .converters.mailfmt import extract_attachments
from .converters.media import (
    concat_media,
    convert_media,
    frame_sequence,
    ringtone_media,
    separate_audio,
)
from .converters.ocr import ocr
from .converters.pdf import (
    pdf_add_cover,
    pdf_add_page_numbers,
    pdf_add_watermark,
    pdf_compress,
    pdf_crop,
    pdf_decrypt,
    pdf_encrypt,
    pdf_extract_images,
    pdf_longpic,
    pdf_merge,
    pdf_metadata,
    pdf_pages_organize,
    pdf_remove_blank,
    pdf_rotate,
    pdf_split,
)
from .converters.speech import tts
from .converters.font import font_subset
from .converters.subtitle import merge_subtitles
from .core.detect import describe as _describe
from .core.detect import detect_format
from .core.engine import Engine, ProgressEvent
from .core.formats import (
    CATEGORY_LABELS,
    FORMATS,
    Format,
    all_exts,
    formats_in,
    guess_format,
)
from .core.graph import Route, find_route, reachable_from, routes_summary
from .core.job import Job, JobResult, Options
from .errors import UnsupportedFormatError, ValidationError
from .plugins import load_plugins
from .queue import JobQueue
from .rename import RenamePlan, rename_batch
from .watch import Watcher, watch

__all__ = [
    "convert",
    "convert_batch",
    "list_formats",
    "can_convert",
    "route",
    "routes_from",
    "route_summary",
    "options_for",
    "describe",
    "extract",
    "pack",
    "load_plugins",
    "watch",
    "Watcher",
    "ocr",
    "rename_batch",
    "RenamePlan",
    "JobQueue",
    "separate_audio",
    "tts",
    "font_subset",
    "merge_subtitles",
    "extract_attachments",
    "download",
    "concat_media",
    "ringtone_media",
    "frame_sequence",
    "image_favicon",
    # PDF 工具箱
    "pdf_merge",
    "pdf_split",
    "pdf_rotate",
    "pdf_encrypt",
    "pdf_decrypt",
    "pdf_extract_images",
    "pdf_compress",
    "pdf_add_page_numbers",
    "pdf_add_watermark",
    "pdf_pages_organize",
    "pdf_remove_blank",
    "pdf_add_cover",
    "pdf_metadata",
    "pdf_crop",
    "pdf_longpic",
    # 图片工具
    "image_watermark",
    "image_collage",
    "image_compress_to_size",
    "parse_size_bytes",
    "Engine",
    "Job",
    "JobResult",
    "Options",
    "ProgressEvent",
    "Route",
]


# --------------------------------------------------------------------------- #
# URL 输入（Aconvert 式：直接转换网上的文件）
# --------------------------------------------------------------------------- #
_URL_DOWNLOAD_DIR: Path | None = None


def download(url: str, dest_dir: str | Path | None = None) -> Path:
    """下载 URL 到本地（文件名从 URL/Content-Disposition/Content-Type 推断）。"""
    import urllib.parse
    import urllib.request

    if not url.lower().startswith(("http://", "https://")):
        raise ValidationError(f"只支持 http/https URL：{url}")
    global _URL_DOWNLOAD_DIR
    if dest_dir is None:
        import tempfile

        if _URL_DOWNLOAD_DIR is None:
            _URL_DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="uniconvert-url-"))
        dest_dir = _URL_DOWNLOAD_DIR
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": "UniConvert/1.3"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - 用户显式提供 URL
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        name = ""
        disposition = resp.headers.get("Content-Disposition") or ""
        if "filename=" in disposition:
            name = disposition.split("filename=", 1)[1].strip().strip('"\'')
        if not name:
            name = Path(urllib.parse.urlparse(url).path).name
        if not name:
            ext = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                "image/gif": ".gif", "application/pdf": ".pdf",
                "video/mp4": ".mp4", "audio/mpeg": ".mp3",
                "text/html": ".html", "text/plain": ".txt",
                "application/zip": ".zip", "application/json": ".json",
            }.get(ctype, ".bin")
            name = "download" + ext
        if not Path(name).suffix and ctype:
            name += {
                "image/jpeg": ".jpg", "image/png": ".png",
                "application/pdf": ".pdf", "video/mp4": ".mp4",
                "audio/mpeg": ".mp3", "text/plain": ".txt",
            }.get(ctype, "")
        target = dest_dir / Path(name).name
        data = resp.read()
    target.write_bytes(data)
    return target


def _materialize(src):
    if isinstance(src, str) and src.lower().startswith(("http://", "https://")):
        return download(src)
    return src


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _resolve_format(name_or_path: str | Path, *, content: bool = True) -> Format:
    """把「格式名 或 文件名 或 存在的文件路径」解析成 Format。"""
    if isinstance(name_or_path, Path) or (
        isinstance(name_or_path, str)
        and ("/" in name_or_path or "\\" in name_or_path
             or name_or_path.lower().endswith(tuple("." + e for e in all_exts())))
    ):
        p = Path(name_or_path)
        if p.exists() and content:
            fmt = detect_format(p)
            if fmt is not None:
                return fmt
        fmt = guess_format(p.name)
        if fmt is not None:
            return fmt
    fmt = guess_format(str(name_or_path))
    if fmt is None:
        raise UnsupportedFormatError(str(name_or_path), known=sorted(FORMATS))
    return fmt


def _fmt_name(name_or_path: str | Path, *, content: bool = True) -> str:
    """宽松解析成格式节点名。

    世界地图里查不到时，退回「扩展名」或「原始词」本身——
    插件可以注册任意自定义格式节点，查询层不应比引擎更严格。
    """
    try:
        return _resolve_format(name_or_path, content=content).name
    except UnsupportedFormatError:
        p = Path(str(name_or_path))
        suffix = p.suffix.lower().lstrip(".")
        if suffix:
            return suffix
        raw = str(name_or_path).strip().lower().lstrip(".")
        if raw and not any(ch in raw for ch in "\\/ "):
            return raw
        raise


# --------------------------------------------------------------------------- #
# 转换
# --------------------------------------------------------------------------- #
def convert(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    to: str = "",
    options: Options | Mapping[str, Any] | None = None,
    workers: int = 1,
    on_exists: str = "rename",
    dry_run: bool = False,
    retry: int = 0,
    on_progress=None,
    **kw_opts: Any,
) -> JobResult:
    """转换单个文件，返回 :class:`JobResult`（不抛转换异常）。"""
    engine = Engine(
        workers=workers, on_exists=on_exists, dry_run=dry_run,
        retry=retry, on_progress=on_progress,
    )
    job = Job.make(_materialize(src), dst, to=to, options=options, **kw_opts)
    return engine.run_one(job)


def convert_batch(
    jobs: Iterable[Job | tuple | Mapping[str, Any]],
    *,
    workers: int = 4,
    on_exists: str = "rename",
    dry_run: bool = False,
    retry: int = 0,
    options: Options | Mapping[str, Any] | None = None,
    on_progress=None,
) -> list[JobResult]:
    """批量转换。

    ``jobs`` 里每一项可以是：

    * :class:`Job` 对象；
    * ``(src, dst)`` 或 ``(src, dst, options)`` 元组；
    * ``{"src": ..., "dst": ..., "to": ..., "options": {...}}`` 字典。

    ``options`` 参数是叠加在每一项之上的公共选项。
    """
    common = Options.from_any(options)
    built: list[Job] = []
    for item in jobs:
        if isinstance(item, Job):
            if common:
                item.options = common.merged(item.options)
            built.append(item)
        elif isinstance(item, Mapping):
            d = dict(item)
            src = _materialize(d.pop("src"))
            dst = d.pop("dst", None)
            to = d.pop("to", "")
            item_opts = d.pop("options", None)
            job = Job.make(src, dst, to=to,
                           options=common.merged(Options.from_any(item_opts)))
            built.append(job)
        else:  # 元组 / 列表
            seq = list(item)
            src = _materialize(seq[0])
            dst = seq[1] if len(seq) > 1 else None
            item_opts = seq[2] if len(seq) > 2 else None
            built.append(Job.make(src, dst,
                                  options=common.merged(Options.from_any(item_opts))))
    engine = Engine(workers=workers, on_exists=on_exists, dry_run=dry_run,
                    retry=retry, on_progress=on_progress)
    return engine.run(built)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_formats(category: str | None = None) -> list[dict[str, Any]]:
    """列出格式（可按大类过滤），保证 JSON 可序列化。"""
    items = formats_in(category) if category else list(FORMATS.values())
    if category and not items:
        raise ValidationError(
            f"未知大类：{category}（可选：{', '.join(sorted(CATEGORY_LABELS))}）"
        )
    return [
        {
            "name": f.name,
            "category": f.category,
            "category_label": CATEGORY_LABELS.get(f.category, f.category),
            "exts": list(f.exts),
            "mime": f.mime,
            "lossless": f.lossless,
            "text": f.text,
            "desc": f.desc,
            "aliases": list(f.aliases),
            "opts": list(f.opts),
        }
        for f in sorted(items, key=lambda x: (x.category, x.name))
    ]


def can_convert(src: str | Path, dst: str | Path, *, available_only: bool = True) -> bool:
    """两个格式（或两个文件）之间是否有可用路径。"""
    try:
        sf = _fmt_name(src)
        df = _fmt_name(dst, content=False)
    except UnsupportedFormatError:
        return False
    return find_route(sf, df, available_only=available_only) is not None


def route(
    src: str | Path,
    dst: str | Path,
    *,
    available_only: bool = True,
) -> Route | None:
    """找转换路径；``src``/``dst`` 可以是格式名或文件路径。"""
    sf = _fmt_name(src)
    df = _fmt_name(dst, content=False)
    return find_route(sf, df, available_only=available_only)


def routes_from(src: str | Path, *, limit: int = 200) -> dict[str, Route]:
    """从某格式出发能直达/中转到的全部格式及最优路径。"""
    sf = _fmt_name(src)
    result = reachable_from(sf)
    return dict(list(result.items())[:limit])


def route_summary() -> dict[str, Any]:
    """转换图统计（节点/边/可用性）。"""
    return routes_summary()


def options_for(fmt: str) -> dict[str, list[str]]:
    """某个格式相关的可选项合集：格式元数据上的 + 所有相邻转换器声明的。"""
    f = _resolve_format(fmt, content=False)
    from .core.registry import REGISTRY

    declared: dict[str, None] = {}
    for conv in REGISTRY:
        if conv.src == f.name or conv.dst == f.name:
            for o in conv.opts:
                declared.setdefault(o, None)
    return {
        "format": f.name,
        "category": CATEGORY_LABELS.get(f.category, f.category),
        "declared": list(f.opts),
        "from_converters": list(declared),
    }


def describe(path: str | Path) -> dict[str, Any]:
    """文件体检报告（格式、大小、扩展名一致性）。"""
    return _describe(path)


# --------------------------------------------------------------------------- #
# 归档便捷
# --------------------------------------------------------------------------- #
def extract(
    archive: str | Path,
    outdir: str | Path = ".",
    *,
    options: Options | Mapping[str, Any] | None = None,
) -> list[Path]:
    """解压归档到目录。"""
    from .converters.archive import extract_archive

    return extract_archive(archive, outdir, opts=Options.from_any(options))


def pack(
    files: Sequence[str | Path],
    dst: str | Path,
    *,
    options: Options | Mapping[str, Any] | None = None,
    base: str | Path | None = None,
) -> Path:
    """把一组文件打包为归档（按 dst 扩展名选容器）。"""
    from .converters.archive import pack_archive

    return pack_archive(files, dst, opts=Options.from_any(options), base=base)
