"""核心子包：格式表、嗅探、转换图、注册表、任务与执行引擎。"""

from .formats import FORMATS, Format, get_format, guess_format, known_names
from .registry import REGISTRY, Converter, converter, register
from .detect import detect_format, sniff
from .graph import find_route, reachable_from, build_graph
from .job import Job, JobResult, Options, Status
from .engine import Engine, ProgressEvent

__all__ = [
    "FORMATS",
    "Format",
    "get_format",
    "guess_format",
    "known_names",
    "REGISTRY",
    "Converter",
    "converter",
    "register",
    "detect_format",
    "sniff",
    "find_route",
    "reachable_from",
    "build_graph",
    "Job",
    "JobResult",
    "Options",
    "Status",
    "Engine",
    "ProgressEvent",
]
