"""批量重命名：正则提取 + 模板重组 + 序号。

```python
from uniconvert import rename_batch

# 预览（默认不动盘）
plan = rename_batch(["IMG_001.jpg", "IMG_002.jpg"], r"IMG_(\\d+)", "照片-{1}")
# 真正执行
rename_batch(files, r"IMG_(\\d+)", "照片-{1}", apply=True)

# 序号模式：data-{n} 生成 data-001, data-002...
rename_batch(files, "", "data-{n}", start=1, pad=3, apply=True)
```

模板占位符：

* ``{1}``~``{9}``：正则捕获组；``{0}`` 整个匹配；
* ``{name}``：原文件名（不含扩展名）；
* ``{ext}``：原扩展名（不含点）——模板里写了它则替换扩展名；
* ``{n}``：序号（按文件顺序自增，宽度由 ``pad`` 控制）。

安全策略：默认**干跑**；执行采用「两阶段重命名」（先改临时名再改目标名）
避免 a→b、b→c 这类链式冲突互相覆盖；目标已存在则跳过并标记错误。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .errors import ValidationError

__all__ = ["RenamePlan", "plan_rename", "apply_rename", "rename_batch"]


@dataclass
class RenamePlan:
    """一条重命名计划。"""

    src: Path
    dst: Path
    matched: bool = True
    error: str = ""

    @property
    def changed(self) -> bool:
        return self.matched and not self.error and self.src != self.dst

    def __str__(self) -> str:
        if not self.matched:
            return f"（无匹配）{self.src.name}"
        if self.error:
            return f"✗ {self.src.name} -> {self.dst.name}（{self.error}）"
        if not self.changed:
            return f"（不变）{self.src.name}"
        return f"{self.src.name} -> {self.dst.name}"


_GROUP_RE = re.compile(r"\{(\d)\}")
_PLACEHOLDER_RE = re.compile(r"\{(name|n|ext)\}")


def _expand(template: str, path: Path, match: re.Match | None, seq: int, pad: int) -> str:
    text = template

    def group_sub(m: re.Match) -> str:
        idx = int(m.group(1))
        if match is None:
            return ""
        try:
            return match.group(idx) or ""
        except (IndexError, re.error):
            return ""

    text = _GROUP_RE.sub(group_sub, text)
    text = text.replace("{name}", path.stem)
    text = text.replace("{ext}", path.suffix.lstrip("."))
    text = text.replace("{n}", str(seq).zfill(pad))
    return text


def plan_rename(
    files: Iterable[str | Path],
    pattern: str = "",
    replace: str = "{name}",
    *,
    start: int = 1,
    pad: int = 3,
) -> list[RenamePlan]:
    """生成重命名计划（不接触磁盘）。"""
    if not replace:
        raise ValidationError("替换模板不能为空")
    plans: list[RenamePlan] = []
    seq = start
    for f in files:
        p = Path(f)
        match: re.Match | None = None
        if pattern:
            match = re.search(pattern, p.stem)
            if match is None:
                plans.append(RenamePlan(p, p, matched=False))
                continue
        new_text = _expand(replace, p, match, seq, pad)
        if "{n}" in replace:
            seq += 1
        # {ext} 出现在模板里：整体决定扩展名（可能不带点）
        if "{ext}" in replace:
            if new_text and not new_text.startswith("."):
                new_name = new_text  # 模板自己管扩展名（如 "pic-{n}.{ext}" 展开后含点）
                if "." not in Path(new_name).name and p.suffix:
                    new_name = new_name + p.suffix
            else:
                new_name = new_text
        else:
            new_name = new_text + p.suffix
        dst = p.with_name(new_name)
        error = ""
        if dst != p and dst.exists():
            error = "目标已存在"
        if not new_name or dst.name in (".", ".."):
            error = "生成的名字为空"
        plans.append(RenamePlan(p, dst, matched=True, error=error))
    # 计划内部撞名检测（撞名的**双方**都标记错误，整组拒绝执行）
    seen: dict[str, RenamePlan] = {}
    for plan in plans:
        if not plan.changed:
            continue
        key = str(plan.dst).lower()
        if key in seen:
            first = seen[key]
            first.error = first.error or "与本次计划中的其他文件重名"
            plan.error = plan.error or "与本次计划中的其他文件重名"
        else:
            seen[key] = plan
    return plans


def apply_rename(plans: Iterable[RenamePlan]) -> tuple[int, int]:
    """执行计划（两阶段改名防链式覆盖），返回 (成功数, 跳过数)。"""
    todo = [p for p in plans if p.changed]
    succeeded = 0
    skipped = 0
    # 阶段 1：全部改到临时名
    temps: list[tuple[Path, Path]] = []
    for plan in todo:
        tmp = plan.src.with_name(f".uc-rename-{uuid.uuid4().hex[:10]}{plan.src.suffix}")
        try:
            plan.src.rename(tmp)
        except OSError as exc:
            plan.error = str(exc)
            skipped += 1
            continue
        temps.append((plan, tmp))
    # 阶段 2：临时名改到目标名
    for plan, tmp in temps:
        try:
            tmp.rename(plan.dst)
            succeeded += 1
        except OSError as exc:
            plan.error = str(exc)
            skipped += 1
            try:
                tmp.rename(plan.src)  # 尽力还原
            except OSError:
                pass
    return succeeded, skipped


def rename_batch(
    files: Iterable[str | Path],
    pattern: str = "",
    replace: str = "{name}",
    *,
    start: int = 1,
    pad: int = 3,
    apply: bool = False,
) -> list[RenamePlan]:
    """批量重命名的完整入口；``apply=False``（默认）只返回预览计划。"""
    plans = plan_rename(files, pattern, replace, start=start, pad=pad)
    if apply:
        apply_rename(plans)
    return plans
