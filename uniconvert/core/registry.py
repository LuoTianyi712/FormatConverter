"""转换器注册表。

一个 :class:`Converter` 就是「一条有向边」：``src -> dst``。
引擎把全部转换器拼成图，再从图上找路径。

转换函数签名::

    def my_convert(src: Path, dst: Path, opts: Options) -> Path | None:
        ...

返回值可以是 ``dst``（或任何实际写出的路径）；返回 ``None`` 视为已写入 ``dst``。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from ..errors import ConversionError, UniconvertError

ConvertFunc = Callable[[Path, Path, "object"], Path | None]


@dataclass
class Converter:
    """一条转换边。

    Attributes:
        src: 源格式规范名。
        dst: 目标格式规范名。
        func: 转换函数。
        deps: 运行需要的依赖（外部程序名或 Python 模块名），缺失则跳过可用性检查。
        weight: 路由代价，越小越优先。默认 1.0；降级链路给更大值。
        quality: 质量评分 0~1，用于「质量优先」时排序（1.0 = 无损/原生）。
        opts: 该转换器支持的选项名，用于 --list-options。
        group: 所属转换器组（如 ``ffmpeg``），便于按组查询与统计。
        bidirectional: 注册时是否同时登记反向边。反向边会复用同一函数。
        note: 备注，例如「有损」「仅解压」。
    """

    src: str
    dst: str
    func: ConvertFunc
    deps: tuple[str, ...] = ()
    weight: float = 1.0
    quality: float = 1.0
    opts: tuple[str, ...] = ()
    group: str = "core"
    bidirectional: bool = False
    note: str = ""
    _sig: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self._sig = str(inspect.signature(self.func))
        except (TypeError, ValueError):
            self._sig = "(...)"

    @property
    def pair(self) -> tuple[str, str]:
        return (self.src, self.dst)

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.src}->{self.dst}"

    def run(self, src: Path, dst: Path, opts) -> Path:
        """执行转换，统一异常包装。

        引擎自己抛出的异常（依赖缺失 / 取消 / 参数不合法）原样透传，
        其余底层异常统一包成 :class:`ConversionError` 并带上边信息。
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            out = self.func(src, dst, opts)
        except (KeyboardInterrupt, SystemExit):
            raise
        except UniconvertError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为 ConversionError
            raise ConversionError(
                f"{type(exc).__name__}: {exc}",
                src=self.src,
                dst=self.dst,
                cause=exc,
            ) from exc
        return Path(out) if out else dst


class Registry:
    """转换器集合，按 (src, dst) 索引。"""

    def __init__(self) -> None:
        self._by_pair: dict[tuple[str, str], Converter] = {}
        self._by_src: dict[str, list[Converter]] = {}
        self._groups: dict[str, list[Converter]] = {}
        self._order: list[Converter] = []

    # ---------------- 注册 ----------------
    def add(
        self,
        src: str | Iterable[str],
        dst: str | Iterable[str],
        func: ConvertFunc,
        *,
        deps: Iterable[str] = (),
        weight: float = 1.0,
        quality: float = 1.0,
        opts: Iterable[str] = (),
        group: str = "core",
        bidirectional: bool = False,
        note: str = "",
        replace: bool = True,
        allow_self: bool = False,
    ) -> list[Converter]:
        """注册一条或多条转换边（支持笛卡尔积形式的多对多）。

        Args:
            allow_self: 是否允许 ``src == dst`` 的自环边。
                自环表示「原地重处理」，例如 ``json -> json`` 的格式化/压缩、
                ``txt -> txt`` 的换编码。默认跳过自环。
        """
        srcs = [src] if isinstance(src, str) else list(src)
        dsts = [dst] if isinstance(dst, str) else list(dst)
        made: list[Converter] = []
        for s in srcs:
            for d in dsts:
                if s == d and not allow_self:
                    continue
                if not replace and (s, d) in self._by_pair:
                    continue
                conv = Converter(
                    src=s, dst=d, func=func, deps=tuple(deps),
                    weight=weight, quality=quality, opts=tuple(opts),
                    group=group, bidirectional=bidirectional, note=note,
                )
                self._put(conv)
                made.append(conv)
                if bidirectional and d != s:
                    rev = Converter(
                        src=d, dst=s, func=func, deps=tuple(deps),
                        weight=weight, quality=quality, opts=tuple(opts),
                        group=group, bidirectional=True, note=note,
                    )
                    self._put(rev)
                    made.append(rev)
        return made

    def _put(self, conv: Converter) -> None:
        key = conv.pair
        if key in self._by_pair:
            old = self._by_pair[key]
            self._order = [c for c in self._order if c is not old]
            lst = self._by_src.get(old.src, [])
            self._by_src[old.src] = [c for c in lst if c is not old]
            gl = self._groups.get(old.group, [])
            self._groups[old.group] = [c for c in gl if c is not old]
        self._by_pair[key] = conv
        self._by_src.setdefault(conv.src, []).append(conv)
        self._groups.setdefault(conv.group, []).append(conv)
        self._order.append(conv)

    # ---------------- 查询 ----------------
    def get(self, src: str, dst: str) -> Converter | None:
        return self._by_pair.get((src, dst))

    def from_format(self, src: str) -> list[Converter]:
        return list(self._by_src.get(src, ()))

    def group(self, name: str) -> list[Converter]:
        return list(self._groups.get(name, ()))

    def groups(self) -> dict[str, int]:
        return {g: len(c) for g, c in sorted(self._groups.items())}

    def __iter__(self) -> Iterator[Converter]:
        return iter(self._order)

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, pair: object) -> bool:
        return isinstance(pair, tuple) and pair in self._by_pair

    def stats(self) -> dict[str, object]:
        pairs = set(self._by_pair)
        return {
            "converters": len(self._order),
            "pairs": len(pairs),
            "sources": len(self._by_src),
            "groups": self.groups(),
        }


#: 全局注册表
REGISTRY = Registry()


def register(
    src: str | Iterable[str],
    dst: str | Iterable[str],
    *,
    deps: Iterable[str] = (),
    weight: float = 1.0,
    quality: float = 1.0,
    opts: Iterable[str] = (),
    group: str = "core",
    bidirectional: bool = False,
    note: str = "",
    registry: Registry | None = None,
) -> Callable[[ConvertFunc], ConvertFunc]:
    """装饰器形式注册转换器。

    >>> @register("json", "yaml")
    ... def _j2y(src, dst, opts):
    ...     return dst
    """

    def deco(func: ConvertFunc) -> ConvertFunc:
        (registry or REGISTRY).add(
            src, dst, func, deps=deps, weight=weight, quality=quality,
            opts=opts, group=group, bidirectional=bidirectional, note=note,
        )
        return func

    return deco


def converter(
    src: str | Iterable[str],
    dst: str | Iterable[str],
    **kw,
) -> Callable[[ConvertFunc], ConvertFunc]:
    """``register`` 的别名，读起来更顺。"""
    return register(src, dst, **kw)
