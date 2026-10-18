"""执行引擎：规划路径 + 批量并发执行。

职责边界：

* **规划（plan）**：嗅探真实格式 → 找转换路径 → 依赖诊断；
* **执行（run）**：必要时拆成多步中转（临时文件），进度事件上报；
* **策略**：覆盖策略、失败重试、干跑、取消、并发。

前端（CLI/GUI/Web）一切行为都通过 :class:`Engine` 的参数与回调控制，
引擎本身不做任何 IO 展示。
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..errors import (
    CancelledError,
    ConversionError,
    DependencyMissingError,
    NoRouteError,
    UniconvertError,
    UnsupportedFormatError,
    ValidationError,
)
from .deps import install_hint
from .detect import detect_format
from .formats import FORMATS, guess_format
from .graph import Route, find_route, missing_for
from .job import Job, JobResult, Options, Status
from .registry import REGISTRY, Registry

__all__ = ["Engine", "ProgressEvent", "ON_EXISTS"]

#: 覆盖策略：重命名 / 跳过 / 覆盖 / 报错
ON_EXISTS = ("rename", "skip", "overwrite", "error")

ProgressHook = Callable[["ProgressEvent"], None]


@dataclass
class ProgressEvent:
    """进度事件。

    前端拿到它就能画出「第 x/n 个任务、第 s/S 步」的进度。
    """

    job: Job
    index: int          # 当前任务序号（1 开始）
    total: int          # 任务总数
    stage: str          # plan / step / done / fail / skip / cancel / dry-run
    step: int = 0       # 当前步序号（1 开始）
    step_total: int = 0
    message: str = ""
    fraction: float = 0.0
    result: JobResult | None = None


class Engine:
    """转换引擎。

    Args:
        registry: 转换器注册表（默认全局 ``REGISTRY``）。
        workers: 批量并发线程数（单任务转换始终在当前线程执行）。
        on_exists: 目标已存在时的策略，见 :data:`ON_EXISTS`。
        retry: 失败重试次数（仅对 :class:`ConversionError` 重试）。
        dry_run: 只规划不执行。
        keep_temp: 保留中转临时文件（调试用）。
        on_progress: 进度回调；线程池场景下回调可能来自多个线程。
    """

    def __init__(
        self,
        *,
        registry: Registry | None = None,
        workers: int = 4,
        on_exists: str = "rename",
        retry: int = 0,
        dry_run: bool = False,
        keep_temp: bool = False,
        on_progress: ProgressHook | None = None,
    ) -> None:
        if on_exists not in ON_EXISTS:
            raise ValidationError(f"未知覆盖策略：{on_exists}（可选 {', '.join(ON_EXISTS)}）")
        self.registry = registry or REGISTRY
        self.workers = max(1, int(workers))
        self.on_exists = on_exists
        self.retry = max(0, int(retry))
        self.dry_run = dry_run
        self.keep_temp = keep_temp
        self.on_progress = on_progress
        self._cancel = threading.Event()
        self._emit_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 取消
    # ------------------------------------------------------------------ #
    def request_cancel(self) -> None:
        """请求取消：未开始的任务直接标记取消，运行中的任务做完当前步。"""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ #
    # 规划
    # ------------------------------------------------------------------ #
    def plan(self, job: Job) -> Route:
        """嗅探格式 + 寻路 + 依赖诊断，返回 :class:`Route`。"""
        src = job.src
        if not src.exists():
            raise ValidationError(f"源文件不存在：{src}")
        if not src.is_file():
            raise ValidationError(f"源路径不是文件：{src}")

        # ---- 源格式 ----
        if not job.src_fmt:
            fmt = detect_format(src)
            if fmt is not None:
                job.src_fmt = fmt.name
            else:
                # 世界地图里没有的格式：用扩展名当节点名，交给转换图裁决
                # （插件可以自定义格式节点，无需先注册进 FORMATS）
                suffix = src.suffix.lower().lstrip(".")
                if not suffix:
                    raise UnsupportedFormatError(src.name)
                job.src_fmt = suffix

        # ---- 目标格式 ----
        if not job.dst_fmt:
            fmt = guess_format(job.dst.name)
            if fmt is not None:
                job.dst_fmt = fmt.name
            else:
                suffix = job.dst.suffix.lower().lstrip(".")
                if not suffix:
                    raise ValidationError(
                        f"无法从目标文件名 {job.dst.name!r} 推断格式，请用 --to 指定"
                    )
                job.dst_fmt = suffix

        # ---- 目标扩展名健壮化 ----
        dst_fmt_obj = FORMATS.get(job.dst_fmt)
        if dst_fmt_obj is not None and not job.dst.suffix:
            job.dst = job.dst.with_suffix("." + dst_fmt_obj.primary_ext)

        # ---- 寻路 ----
        route = find_route(job.src_fmt, job.dst_fmt, registry=self.registry)
        if route is None:
            miss = missing_for(job.src_fmt, job.dst_fmt, registry=self.registry)
            if miss:
                raise DependencyMissingError(
                    "、".join(miss),
                    why=f"{job.src_fmt} -> {job.dst_fmt} 的转换链路需要它们",
                    install="\n".join(f"  {d}: {install_hint(d)}" for d in miss),
                )
            raise NoRouteError(job.src_fmt, job.dst_fmt)
        return route

    # ------------------------------------------------------------------ #
    # 单任务执行
    # ------------------------------------------------------------------ #
    def run_one(self, job: Job, *, index: int = 1, total: int = 1) -> JobResult:
        """执行单个任务（永不抛异常，错误都装进 :class:`JobResult`）。"""
        result = JobResult(job=job)
        start = time.perf_counter()
        try:
            if self._cancel.is_set():
                raise CancelledError("已取消")

            result.src_size = _safe_size(job.src)

            # ---- 规划 ----
            self._emit(ProgressEvent(job, index, total, "plan", message="分析格式与路径…"))
            route = self.plan(job)
            result.route = route
            result.steps = tuple(f"{s.src}→{s.dst}" for s in route.steps)

            # ---- 格式一致性检查 ----
            result.warnings.extend(self._check_consistency(job))

            if self.dry_run:
                result.status = Status.SKIPPED
                result.dry_run = True
                result.error = "dry-run：仅规划，未执行"
                self._emit(ProgressEvent(job, index, total, "dry-run", message=str(route),
                                         result=result))
                return result

            # ---- 覆盖策略 ----
            target = self._resolve_target(job)
            if target is None:
                result.status = Status.SKIPPED
                result.error = f"目标已存在（策略：{self.on_exists}）"
                self._emit(ProgressEvent(job, index, total, "skip", message=result.error,
                                         result=result))
                return result
            job.dst = target

            # ---- 执行（带重试） ----
            attempts = self.retry + 1
            last_exc: Exception | None = None
            for attempt in range(attempts):
                try:
                    output = self._execute(job, route, index=index, total=total)
                    last_exc = None
                    result.output = output
                    result.status = Status.DONE
                    break
                except ConversionError as exc:
                    last_exc = exc
                    if attempt + 1 < attempts:
                        self._emit(ProgressEvent(
                            job, index, total, "step",
                            message=f"第 {attempt + 1} 次尝试失败，重试中：{exc}",
                        ))
            if last_exc is not None:
                raise last_exc

            result.dst_size = _safe_size(result.output)
            self._emit(ProgressEvent(job, index, total, "done", result=result,
                                     message=result.summary(), step=len(route.steps),
                                     step_total=len(route.steps),
                                     fraction=index / total))
        except CancelledError as exc:
            result.status = Status.CANCELLED
            result.error = str(exc)
            self._emit(ProgressEvent(job, index, total, "cancel", result=result,
                                     message=result.error))
        except UniconvertError as exc:
            result.status = Status.FAILED
            result.error = str(exc)
            self._emit(ProgressEvent(job, index, total, "fail", result=result,
                                     message=result.error))
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不让线程崩掉
            result.status = Status.FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            self._emit(ProgressEvent(job, index, total, "fail", result=result,
                                     message=result.error))
        finally:
            result.elapsed = time.perf_counter() - start
        return result

    def _execute(self, job: Job, route: Route, *, index: int, total: int) -> Path:
        """按路径逐步执行；多步时用临时目录中介。"""
        steps = route.steps
        step_total = max(len(steps), 1)

        # 0 步：同格式直通 → 复制
        if not steps:
            self._emit(ProgressEvent(job, index, total, "step", step=1, step_total=1,
                                     message="同格式：直接复制"))
            shutil.copy2(job.src, job.dst)
            return job.dst

        tmpdir: Path | None = None
        try:
            if len(steps) > 1:
                tmpdir = Path(tempfile.mkdtemp(prefix="uniconvert-"))

            current = job.src
            output = job.dst
            for i, step in enumerate(steps, 1):
                last = i == len(steps)
                if last:
                    out_path = job.dst
                else:
                    fmt = FORMATS.get(step.dst)
                    ext = fmt.primary_ext if fmt else step.dst
                    assert tmpdir is not None
                    out_path = tmpdir / f"step{i:02d}.{ext}"
                self._emit(ProgressEvent(
                    job, index, total, "step", step=i, step_total=step_total,
                    message=f"步骤 {i}/{step_total}：{step.src} → {step.dst}"
                            f"（{step.group}）",
                    fraction=(index - 1 + (i - 1) / step_total) / max(total, 1),
                ))

                # 给长任务（ffmpeg 等）注入细粒度进度钩子：
                # 转换器通过 _util.report_progress(opts, 0~1) 回调本步完成比例。
                def _hook(frac, note="", _i=i):  # noqa: ANN001
                    self._emit(ProgressEvent(
                        job, index, total, "step-progress", step=_i,
                        step_total=step_total, message=note,
                        fraction=(index - 1 + (_i - 1 + max(0.0, min(1.0, frac)))
                                  / step_total) / max(total, 1),
                    ))
                job.options.values["__progress__"] = _hook

                current = step.run(current, out_path, job.options)
                output = current
            return output
        finally:
            if tmpdir is not None and not self.keep_temp:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _resolve_target(self, job: Job) -> Path | None:
        """按覆盖策略解析最终目标路径；``None`` 表示应跳过。"""
        dst = job.dst
        if not dst.exists():
            return dst
        if self.on_exists == "overwrite":
            return dst
        if self.on_exists == "skip":
            return None
        if self.on_exists == "error":
            raise ConversionError(f"目标已存在：{dst}")
        # rename：自动加序号
        stem, suffix, parent = dst.stem, dst.suffix, dst.parent
        for n in range(1, 10_000):
            candidate = parent / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
        raise ConversionError(f"无法为目标文件生成唯一名字：{dst}")

    def _check_consistency(self, job: Job) -> list[str]:
        """源文件扩展名与实际内容不符时给出提醒。"""
        warned: list[str] = []
        fmt = guess_format(job.src.name)
        if fmt is not None and fmt.name != job.src_fmt:
            warned.append(f"源文件扩展名({fmt.name})与实际格式({job.src_fmt})不一致")
        return warned

    # ------------------------------------------------------------------ #
    # 批量执行
    # ------------------------------------------------------------------ #
    def run(
        self,
        jobs: Sequence[Job],
        *,
        on_progress: ProgressHook | None = None,
    ) -> list[JobResult]:
        """批量执行（有序返回，线程池并发）。"""
        jobs = list(jobs)
        if not jobs:
            return []
        hook = on_progress or self.on_progress
        if hook is not None:
            self.on_progress = hook

        total = len(jobs)
        results: list[JobResult | None] = [None] * total

        if total == 1 or self.workers <= 1:
            for i, job in enumerate(jobs, 1):
                results[i - 1] = self.run_one(job, index=i, total=total)
            return [r for r in results if r is not None]  # type: ignore[misc]

        with ThreadPoolExecutor(max_workers=min(self.workers, total),
                                thread_name_prefix="uniconvert") as pool:
            futures: dict[Future[JobResult], int] = {}
            for i, job in enumerate(jobs, 1):
                if self._cancel.is_set():
                    res = JobResult(job=job, status=Status.CANCELLED, error="已取消")
                    results[i - 1] = res
                    continue
                futures[pool.submit(self.run_one, job, index=i, total=total)] = i - 1
            for fut in futures:
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001 - run_one 理论上不抛
                    results[idx] = JobResult(job=jobs[idx], status=Status.FAILED,
                                             error=f"{type(exc).__name__}: {exc}")
        return [r for r in results if r is not None]  # type: ignore[misc]

    # ------------------------------------------------------------------ #
    # 便捷入口
    # ------------------------------------------------------------------ #
    def convert(
        self,
        src: str | Path,
        dst: str | Path | None = None,
        *,
        to: str = "",
        options: Options | dict | None = None,
        **kw_opts,
    ) -> JobResult:
        """单文件转换的便捷入口（同步执行）。"""
        job = Job.make(src, dst, to=to, options=options, **kw_opts)
        return self.run_one(job)

    # ------------------------------------------------------------------ #
    def _emit(self, event: ProgressEvent) -> None:
        hook = self.on_progress
        if hook is None:
            return
        with self._emit_lock:
            try:
                hook(event)
            except Exception:  # noqa: BLE001 - 前端回调故障不能影响转换
                pass


def _safe_size(path: Path | None) -> int:
    try:
        return path.stat().st_size if path and path.exists() else 0
    except OSError:
        return 0
