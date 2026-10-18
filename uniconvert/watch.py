"""目录监控：新文件出现或内容变更时自动转换。

```bash
uniconvert watch ~/手机相册 -t webp --out ~/压缩相册 --interval 2
uniconvert watch ./收件目录 -t pdf --once      # 只扫一遍（脚本用）
```

实现是零依赖的轮询（默认 2 秒）：
记录每个文件的 ``(修改时间, 大小)`` 签名，签名变化才触发转换；
启动时默认**只登记存量不转换**（``initial=False``），避免误转历史文件；
转换输出写到 ``outdir``（缺省与源文件同目录），不会回环触发自己。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .core.engine import Engine
from .core.formats import guess_format
from .core.job import Job, JobResult, Options
from .errors import ValidationError

__all__ = ["Watcher", "watch"]

#: 明显的临时/半成品文件名，监控时忽略
_IGNORE_SUFFIXES = (".tmp", ".part", ".crdownload", ".download", ".swp", ".swx", "~")
_IGNORE_PREFIXES = ("~$", ".~", ".goutputstream", "uniconvert-")


@dataclass
class Watcher:
    """一轮一轮扫描目录的转换器。

    Args:
        srcdir: 监视目录（递归子目录）。
        target: 目标格式名（如 webp / pdf / yaml）。
        outdir: 输出目录；缺省输出到源文件旁边。
        only: 只处理这些扩展名（如 ``("jpg", "png")``）；空 = 全部已知格式。
        options: 转换选项。
        initial: 启动第一时间是否转换**已有**文件（默认否，只登记）。
        engine_kwargs: 传给 :class:`Engine` 的参数（workers/on_exists/retry...）。
    """

    srcdir: Path
    target: str = ""
    outdir: Path | None = None
    only: tuple[str, ...] = ()
    options: Options = field(default_factory=Options)
    initial: bool = False
    engine_kwargs: dict = field(default_factory=dict)
    _seen: dict[str, tuple[float, int]] = field(default_factory=dict, init=False, repr=False)
    _primed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.srcdir = Path(self.srcdir)
        if not self.srcdir.is_dir():
            raise ValidationError(f"监视目录不存在：{self.srcdir}")
        if self.outdir is not None:
            self.outdir = Path(self.outdir)
        if not self.target:
            raise ValidationError("watch 需要指定目标格式（--to/-t）")
        fmt = guess_format(self.target)
        if fmt is None:
            raise ValidationError(f"无法识别的目标格式：{self.target!r}")
        self.target = fmt.name
        self.options = Options.from_any(self.options)
        self.only = tuple(
            e.lower().lstrip(".") for e in self.only if e.strip()
        )

    # ------------------------------------------------------------------ #
    def _iter_candidates(self) -> Iterable[Path]:
        for f in self.srcdir.rglob("*"):
            if not f.is_file():
                continue
            name = f.name.lower()
            if name.startswith(_IGNORE_PREFIXES) or name.endswith(_IGNORE_SUFFIXES):
                continue
            if self.outdir is not None:
                try:
                    f.resolve().relative_to(self.outdir.resolve())
                    continue  # 监视目录套着输出目录时，别转自己产出的文件
                except ValueError:
                    pass
            if self.only:
                if f.suffix.lower().lstrip(".") not in self.only:
                    continue
            elif guess_format(f.name) is None:
                continue
            yield f

    def _make_job(self, path: Path) -> Job:
        fmt = guess_format(self.target)
        assert fmt is not None
        if self.outdir is not None:
            dst = self.outdir / f"{path.stem}.{fmt.primary_ext}"
        else:
            dst = path.with_suffix("." + fmt.primary_ext)
        return Job(src=path, dst=dst, dst_fmt=fmt.name,
                   options=Options(dict(self.options.values)),
                   label=path.name)

    # ------------------------------------------------------------------ #
    def scan_once(self) -> list[JobResult]:
        """扫描一轮：转换新增/变更的文件，返回本轮结果（无则空列表）。"""
        jobs: list[Job] = []
        # 首扫「只登记不转换」；注意空目录也要把 _primed 置位，
        # 否则之后新建的文件会被永远当成首扫存量而漏转。
        first_scan = not self._primed
        for f in self._iter_candidates():
            try:
                st = f.stat()
            except OSError:
                continue
            key = str(f.resolve())
            sig = (st.st_mtime, st.st_size)
            if self._seen.get(key) == sig:
                continue
            self._seen[key] = sig
            if first_scan and not self.initial:
                continue  # 首扫只登记存量
            if self.outdir is not None and f.resolve() == self._make_job(f).dst.resolve():
                continue  # 目标就是自己（同格式原地），跳过防回环
            jobs.append(self._make_job(f))
        self._primed = True
        if not jobs:
            return []
        engine = Engine(**{"workers": min(2, len(jobs)), **self.engine_kwargs})
        return engine.run(jobs)

    @property
    def seen_count(self) -> int:
        return len(self._seen)


def watch(
    watcher: Watcher,
    *,
    interval: float = 2.0,
    on_result: Callable[[JobResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """阻塞式监控循环；``should_stop`` 返回 True 或 KeyboardInterrupt 时退出。"""
    while True:
        if should_stop is not None and should_stop():
            return
        try:
            results = watcher.scan_once()
        except Exception as exc:  # noqa: BLE001 - 监控循环不能因单轮故障退出
            if on_result is not None:
                print(f"[watch] 本轮扫描出错：{exc}")
            results = []
        if on_result is not None:
            for r in results:
                on_result(r)
        time.sleep(interval)
