"""UniConvert —— 万能格式转换器。

设计目标
--------
一个可扩展的、跨模态的格式转换引擎：文档 / 数据 / 图像 / 音频 / 视频 /
压缩包 / 字幕 / 编码，统一到一个「转换图」上，自动寻路、批量执行。

核心特性
--------
* **零依赖内核**：核心引擎只用 Python 标准库，装完即用。
* **自动路由**：A→B 没有直连转换器时，沿转换图 BFS 找最短链路（A→X→B）。
* **格式嗅探**：优先按文件头（magic bytes）判断真实格式，扩展名不可信。
* **批量 + 并发**：线程池并行，支持失败重试、覆盖策略、干跑预览。
* **插件系统**：`plugins/` 目录热加载，或通过 entry-point 注册。
* **三种前端**：CLI（argparse）、GUI（tkinter）、Web（可选 Flask）。

典型用法
--------
>>> from uniconvert import convert
>>> convert("a.docx", "a.pdf")          # doctest: +SKIP
>>> convert("in.json", "out.yaml")      # doctest: +SKIP
"""

from .api import (
    can_convert,
    convert,
    convert_batch,
    describe,
    extract,
    list_formats,
    options_for,
    pack,
    route,
    route_summary,
    routes_from,
)
from .core.job import Job, JobResult, Options
from .core.graph import Route
from .errors import (
    ConversionError,
    DependencyMissingError,
    NoRouteError,
    UniconvertError,
    UnsupportedFormatError,
)

__version__ = "1.0.0"
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
    "Job",
    "JobResult",
    "Options",
    "Route",
    "UniconvertError",
    "UnsupportedFormatError",
    "NoRouteError",
    "ConversionError",
    "DependencyMissingError",
    "__version__",
]
