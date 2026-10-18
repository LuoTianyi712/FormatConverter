"""转换图与寻路。

把注册表里全部的 :class:`~uniconvert.core.registry.Converter` 拼成一张
有向图：**节点 = 格式，边 = 转换器**。A→B 没有直连转换器时，
沿图找一条代价最小的链路（A→X→Y→B），这就是「万能」的核心机制。

寻路用 Dijkstra，单条边代价::

    cost(e) = e.weight / max(e.quality, 1e-3)

即「权重小、质量高」的边优先。多步路径天然比直连代价高，
所以引擎总是尽量走直连，降级链路只在必要时出现。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Iterator

from .deps import has_dep
from .registry import REGISTRY, Converter, Registry

__all__ = ["Route", "build_graph", "find_route", "reachable_from", "missing_for"]


@dataclass(frozen=True)
class Route:
    """一条转换路径：0 步表示「同格式直通」，1 步表示直连，多步表示中转链路。"""

    steps: tuple[Converter, ...] = ()
    src: str = ""
    dst: str = ""

    # ---------------- 属性 ----------------
    @property
    def direct(self) -> bool:
        return len(self.steps) <= 1

    @property
    def identity(self) -> bool:
        return not self.steps

    @property
    def weight(self) -> float:
        return sum(s.weight for s in self.steps)

    @property
    def quality(self) -> float:
        return min((s.quality for s in self.steps), default=1.0)

    @property
    def nodes(self) -> list[str]:
        """路径经过的格式序列，如 ``["docx", "html", "epub"]``。"""
        if not self.steps:
            return [self.src] if self.src == self.dst else []
        return [self.steps[0].src] + [s.dst for s in self.steps]

    @property
    def groups(self) -> list[str]:
        return [s.group for s in self.steps]

    @property
    def deps(self) -> list[str]:
        """路径上所有边的依赖合集（去重保序）。"""
        seen: dict[str, None] = {}
        for s in self.steps:
            for d in s.deps:
                seen.setdefault(d, None)
        return list(seen)

    # ---------------- 协议 ----------------
    def __iter__(self) -> Iterator[Converter]:
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    def __str__(self) -> str:
        if self.identity:
            return f"{self.src}（同格式直通）"
        return " → ".join(self.nodes)

    # ---------------- 展示 ----------------
    def describe(self, *, indent: str = "  ") -> list[str]:
        """给 CLI 展示的多行描述。"""
        lines = [f"路径：{self}"]
        if not self.steps:
            lines.append(f"{indent}0 步：直接复制（同格式）")
            return lines
        for i, step in enumerate(self.steps, 1):
            extras = []
            if step.note:
                extras.append(step.note)
            extras.append(f"weight={step.weight:g}")
            extras.append(f"quality={step.quality:g}")
            if step.deps:
                miss = [d for d in step.deps if not has_dep(d)]
                extras.append(f"依赖={','.join(step.deps)}"
                              + (f"（缺：{','.join(miss)}）" if miss else "（已就绪）"))
            lines.append(
                f"{indent}{i}. {step.src} → {step.dst}"
                f"　[{step.group}]　" + "　".join(extras)
            )
        lines.append(f"{indent}总代价={self.weight:g}　最低质量={self.quality:g}")
        return lines


# --------------------------------------------------------------------------- #
# 建图
# --------------------------------------------------------------------------- #
def build_graph(
    registry: Registry | None = None,
    *,
    available_only: bool = False,
) -> dict[str, list[Converter]]:
    """把注册表展开成邻接表：``格式名 -> 出边列表``。

    Args:
        available_only: 为真时过滤掉依赖缺失的边（用 :func:`deps.has_dep` 判断）。
    """
    reg = registry or REGISTRY
    adj: dict[str, list[Converter]] = {}
    for conv in reg:
        if available_only and not all(has_dep(d) for d in conv.deps):
            continue
        adj.setdefault(conv.src, []).append(conv)
    return adj


def _edge_cost(conv: Converter) -> float:
    return conv.weight / max(conv.quality, 1e-3)


# --------------------------------------------------------------------------- #
# 寻路
# --------------------------------------------------------------------------- #
def find_route(
    src: str,
    dst: str,
    *,
    registry: Registry | None = None,
    available_only: bool = True,
    graph: dict[str, list[Converter]] | None = None,
) -> Route | None:
    """找 ``src -> dst`` 代价最小的路径；找不到了返回 ``None``。

    * ``src == dst``：优先用可用的自环边（重编码/重格式化），否则返回 0 步直通。
    * 中间链路绝不自环（自环不帮助到达新节点，直接剪掉）。
    """
    if src == dst:
        adj0 = graph or build_graph(registry, available_only=available_only)
        self_edges = [e for e in adj0.get(src, ()) if e.dst == src]
        if self_edges:
            best = min(self_edges, key=_edge_cost)
            return Route((best,), src, dst)
        return Route((), src, dst)

    adj = graph or build_graph(registry, available_only=available_only)

    # (累计代价, 序号, 当前节点, 路径元组)；序号用于同代价时稳定排序
    heap: list[tuple[float, int, str, tuple[Converter, ...]]] = []
    heapq.heappush(heap, (0.0, 0, src, ()))
    best_seen: dict[str, float] = {src: 0.0}
    seq = 0

    while heap:
        cost, _, node, path = heapq.heappop(heap)
        if node == dst:
            return Route(path, src, dst)
        if cost > best_seen.get(node, float("inf")):
            continue
        for edge in adj.get(node, ()):
            if edge.dst == node:  # 自环在中间链路里没有意义
                continue
            nxt = edge.dst
            ncost = cost + _edge_cost(edge)
            if ncost < best_seen.get(nxt, float("inf")):
                best_seen[nxt] = ncost
                seq += 1
                heapq.heappush(heap, (ncost, seq, nxt, path + (edge,)))
    return None


def reachable_from(
    src: str,
    *,
    registry: Registry | None = None,
    available_only: bool = True,
    graph: dict[str, list[Converter]] | None = None,
    max_depth: int | None = None,
) -> dict[str, Route]:
    """从 ``src`` 出发能到达的全部格式及最优路径（Dijkstra 泛洪）。

    Returns:
        ``{目标格式名: Route}``，按路径代价排序的普通 dict。
    """
    adj = graph or build_graph(registry, available_only=available_only)
    best: dict[str, tuple[float, tuple[Converter, ...]]] = {src: (0.0, ())}
    heap: list[tuple[float, int, str, tuple[Converter, ...]]] = [(0.0, 0, src, ())]
    seq = 0

    while heap:
        cost, _, node, path = heapq.heappop(heap)
        if cost > best.get(node, (float("inf"), ()))[0]:
            continue
        if max_depth is not None and len(path) >= max_depth:
            continue
        for edge in adj.get(node, ()):
            if edge.dst == node:
                continue
            nxt = edge.dst
            ncost = cost + _edge_cost(edge)
            if ncost < best.get(nxt, (float("inf"), ()))[0]:
                best[nxt] = (ncost, path + (edge,))
                seq += 1
                heapq.heappush(heap, (ncost, seq, nxt, path + (edge,)))

    out = {
        node: Route(path, src, node)
        for node, (_, path) in sorted(best.items(), key=lambda kv: kv[1][0])
        if node != src
    }
    return out


def missing_for(
    src: str,
    dst: str,
    *,
    registry: Registry | None = None,
) -> list[str]:
    """诊断「为什么不可达」：忽略依赖时可达 → 返回缺的依赖名列表。

    引擎用它把 ``NoRouteError`` 升级成带安装提示的
    :class:`~uniconvert.errors.DependencyMissingError`。
    完全不可达时返回空列表。
    """
    full = find_route(src, dst, registry=registry, available_only=False)
    if full is None:
        return []
    miss: dict[str, None] = {}
    for step in full.steps:
        for dep in step.deps:
            if not has_dep(dep):
                miss.setdefault(dep, None)
    return list(miss)


def routes_summary(registry: Registry | None = None) -> dict[str, object]:
    """给 CLI ``stats`` 用的图统计。"""
    reg = registry or REGISTRY
    adj = build_graph(reg, available_only=False)
    avail = build_graph(reg, available_only=True)
    return {
        "nodes": len(adj),
        "edges": sum(len(v) for v in adj.values()),
        "nodes_available": len(avail),
        "edges_available": sum(len(v) for v in avail.values()),
        "converters": len(reg),
    }
