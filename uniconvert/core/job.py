"""任务模型：Options（转换选项）、Job（一次转换）、JobResult（结果）、Status。

三者是引擎、CLI、GUI、Web 之间共享的「通用语言」——
前端只负责把用户输入装进 :class:`Job`，引擎只负责把 :class:`JobResult` 交回来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import ValidationError
from .formats import guess_format
from .graph import Route

__all__ = ["Status", "Options", "Job", "JobResult", "parse_option_value"]


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    """任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "pending": "等待中",
            "running": "转换中",
            "done": "完成",
            "failed": "失败",
            "skipped": "跳过",
            "cancelled": "已取消",
        }[self.value]


# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #
def parse_option_value(raw: str) -> Any:
    """把命令行里的 ``key=value`` 值猜成合适的 Python 类型。

    依次尝试 JSON 解析（覆盖数字/布尔/数组/对象/带引号字符串），
    失败则原样保留为字符串。

    >>> parse_option_value("23")
    23
    >>> parse_option_value("true")
    True
    >>> parse_option_value("1280x720")
    '1280x720'
    """
    s = raw.strip()
    if s == "":
        return ""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return s


@dataclass
class Options:
    """转换选项：一层宽松的键值表，转换器按需读取。

    设计成宽松类型是刻意的——不同格式族的选项天差地别（``quality`` /
    ``crf`` / ``delimiter`` / ``pages``…），统一成固定字段只会互相污染。
    各格式的合法选项名见 :class:`~uniconvert.core.formats.Format.opts`
    与 ``uniconvert options <fmt>``。
    """

    values: dict[str, Any] = field(default_factory=dict)

    # ---------------- 构造 ----------------
    @classmethod
    def from_any(cls, obj: "Options | Mapping[str, Any] | Iterable[tuple[str, Any]] | None") -> "Options":
        """把 dict / Options / (k, v) 序列 / None 统一成 Options。"""
        if obj is None:
            return cls()
        if isinstance(obj, Options):
            return cls(dict(obj.values))
        if isinstance(obj, Mapping):
            return cls({str(k): v for k, v in obj.items()})
        return cls({str(k): v for k, v in obj})

    @classmethod
    def parse(cls, items: Iterable[str]) -> "Options":
        """解析 ``["quality=90", "delimiter=;"]`` 这类命令行选项。"""
        values: dict[str, Any] = {}
        for item in items:
            if "=" not in item:
                raise ValidationError(f"选项格式应为 key=value：{item!r}")
            key, _, raw = item.partition("=")
            key = key.strip()
            if not key:
                raise ValidationError(f"选项缺少键名：{item!r}")
            values[key] = parse_option_value(raw)
        return cls(values)

    # ---------------- 读写 ----------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        return self.values.pop(key, default)

    def setdefault(self, key: str, default: Any) -> Any:
        return self.values.setdefault(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self.values

    def __len__(self) -> int:
        return len(self.values)

    def __bool__(self) -> bool:
        return bool(self.values)

    def __iter__(self):
        return iter(self.values)

    def items(self):
        return self.values.items()

    def keys(self):
        return self.values.keys()

    def update(self, other: "Options | Mapping[str, Any]" = None, **kw: Any) -> "Options":
        """原地合并，返回自身方便链式调用。"""
        if other is None:
            pass
        elif isinstance(other, Options):
            self.values.update(other.values)
        else:
            self.values.update(other)
        self.values.update(kw)
        return self

    def merged(self, other: "Options | Mapping[str, Any] | None") -> "Options":
        """返回合并后的新对象（不改自身）。"""
        return Options(dict(self.values)).update(Options.from_any(other))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)

    # ---------------- 类型化读取 ----------------
    def as_int(self, key: str, default: int) -> int:
        try:
            return int(self.values.get(key, default))
        except (TypeError, ValueError):
            return default

    def as_float(self, key: str, default: float) -> float:
        try:
            return float(self.values.get(key, default))
        except (TypeError, ValueError):
            return default

    def as_bool(self, key: str, default: bool) -> bool:
        v = self.values.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(v)

    def as_str(self, key: str, default: str = "") -> str:
        v = self.values.get(key, default)
        return default if v is None else str(v)

    def flat(self) -> str:
        """``"a=1 b=2"`` 形式的展示串。"""
        if not self.values:
            return ""
        return " ".join(f"{k}={v}" for k, v in self.values.items())


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    """一次转换任务。

    Attributes:
        src: 源文件路径。
        dst: 目标路径（可以是文件，也可以是目录）。
        src_fmt: 源格式规范名（留空则让引擎嗅探）。
        dst_fmt: 目标格式规范名（留空则按 ``dst`` 扩展名推断）。
        options: 转换选项。
        label: 展示名（默认取文件名）。
    """

    src: Path
    dst: Path
    src_fmt: str = ""
    dst_fmt: str = ""
    options: Options = field(default_factory=Options)
    label: str = ""

    def __post_init__(self) -> None:
        self.src = Path(self.src)
        self.dst = Path(self.dst)
        self.options = Options.from_any(self.options)
        if not self.label:
            self.label = self.src.name or str(self.src)

    @classmethod
    def make(
        cls,
        src: str | Path,
        dst: str | Path | None = None,
        *,
        to: str = "",
        options: "Options | Mapping[str, Any] | None" = None,
        **kw_opts: Any,
    ) -> "Job":
        """构造任务，自动补全目标路径。

        * ``dst`` 省略时按 ``--to`` 生成的格式放在源文件旁边
          （``a.docx --to pdf`` → ``a.pdf``）；
        * ``dst`` 是目录时，输出为 ``目录/源文件名.目标扩展名``。
        """
        src_p = Path(src)
        dst_fmt = ""
        if to:
            fmt = guess_format(to)
            if fmt is None:
                raise ValidationError(f"无法识别的目标格式：{to!r}")
            dst_fmt = fmt.name

        if dst is None:
            if not to:
                raise ValidationError("缺少目标路径或目标格式（dst 或 to 二选一）")
            fmt = guess_format(to)
            assert fmt is not None
            dst_p = src_p.with_suffix("." + fmt.primary_ext)
        else:
            dst_p = Path(dst)
            if dst_p.is_dir():
                if not to:
                    raise ValidationError(
                        f"目标 {dst_p} 是目录，必须用 --to/-t 指定目标格式"
                    )
                fmt = guess_format(to)
                assert fmt is not None
                dst_p = dst_p / f"{src_p.stem}.{fmt.primary_ext}"
            elif not dst_p.suffix and to:
                fmt = guess_format(to)
                assert fmt is not None
                dst_p = dst_p.with_suffix("." + fmt.primary_ext)
            elif not dst_fmt:
                fmt = guess_format(dst_p.name)
                if fmt is not None:
                    dst_fmt = fmt.name

        opts = Options.from_any(options).update(kw_opts)
        return cls(src=src_p, dst=dst_p, dst_fmt=dst_fmt, options=opts)


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #
@dataclass
class JobResult:
    """一次转换的结果快照。"""

    job: Job
    status: Status = Status.PENDING
    route: Route | None = None
    output: Path | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    src_size: int = 0
    dst_size: int = 0
    steps: tuple[str, ...] = ()
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status is Status.DONE

    @property
    def src(self) -> Path:
        return self.job.src

    @property
    def dst(self) -> Path:
        return self.job.dst

    def summary(self) -> str:
        """单行中文摘要，CLI 批量模式直接用。"""
        parts: list[str] = [f"[{self.status.label}]"]
        if self.dry_run:
            parts.append("(dry-run)")
        parts.append(self.job.label)
        if self.route is not None and not self.route.identity:
            parts.append(f"({self.route})")
        if self.status is Status.DONE and self.output is not None:
            parts.append(f"-> {self.output}")
            parts.append(f"{_human(self.src_size)} -> {_human(self.dst_size)}")
            parts.append(f"{self.elapsed:.2f}s")
        elif self.status is Status.SKIPPED and self.error:
            parts.append(f"（{self.error}）")
        elif self.error:
            parts.append(f"✗ {self.error}")
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """给 Web/GUI/JSON 输出用的可序列化形式。"""
        return {
            "src": str(self.src),
            "dst": str(self.dst),
            "status": self.status.value,
            "route": str(self.route) if self.route else None,
            "steps": list(self.steps),
            "output": str(self.output) if self.output else None,
            "error": self.error,
            "warnings": list(self.warnings),
            "elapsed": round(self.elapsed, 4),
            "src_size": self.src_size,
            "dst_size": self.dst_size,
            "dry_run": self.dry_run,
        }


def _human(n: int) -> str:
    from .detect import human_size

    return human_size(n)
