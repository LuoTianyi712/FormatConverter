"""插件加载。

三种来源，按顺序加载：

1. ``UNICONVERT_PLUGIN_PATH`` 环境变量里的目录（``;`` 分隔）；
2. 用户目录 ``~/.uniconvert/plugins/*.py``；
3. 安装包注册的 entry point（组名 ``uniconvert.converters``）。

插件就是一个普通 ``.py`` 文件，导入时调用
:func:`uniconvert.core.registry.register` 注册转换器即可::

    from uniconvert.core import register
    from uniconvert.converters._util import run_tool

    @register("foo", "bar", group="myplugin")
    def foo_to_bar(src, dst, opts):
        ...
        return dst

加载失败的插件不会拖垮整个程序：错误被收集进 :class:`PluginInfo.error`。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PluginInfo", "load_plugins", "plugin_dirs"]

ENV_VAR = "UNICONVERT_PLUGIN_PATH"
USER_DIR = Path.home() / ".uniconvert" / "plugins"
ENTRY_GROUP = "uniconvert.converters"


@dataclass
class PluginInfo:
    """一个插件的加载结果。"""

    name: str
    origin: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def __str__(self) -> str:
        return f"{self.name} ({self.origin})" + (f" - 失败：{self.error}" if self.error else "")


def plugin_dirs(extra_dirs: list[str | Path] | None = None) -> list[Path]:
    """解析插件目录（存在且去重）。"""
    dirs: list[Path] = []
    env = os.environ.get(ENV_VAR, "")
    if env:
        dirs.extend(Path(p) for p in env.split(os.pathsep) if p.strip())
    if extra_dirs:
        dirs.extend(Path(p) for p in extra_dirs)
    dirs.append(USER_DIR)
    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        rp = d.resolve() if d.exists() else d
        if rp not in seen and d.is_dir():
            seen.add(rp)
            out.append(d)
    return out


def _load_file(path: Path) -> PluginInfo:
    name = f"uniconvert_plugin_{path.stem}"
    origin = str(path)
    if name in sys.modules:
        return PluginInfo(name, origin)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return PluginInfo(name, origin, error="无法创建模块规格")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return PluginInfo(name, origin)
    except Exception as exc:  # noqa: BLE001 - 插件错误要收集而不是崩溃
        sys.modules.pop(name, None)
        detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return PluginInfo(name, origin, error=f"{type(exc).__name__}: {exc} ({detail})")


def _load_entry_points() -> list[PluginInfo]:
    out: list[PluginInfo] = []
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return out
    try:
        eps = entry_points(group=ENTRY_GROUP)
    except TypeError:  # pragma: no cover - 老版本 Python
        eps = entry_points().get(ENTRY_GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            ep.load()
            out.append(PluginInfo(ep.name, f"entry-point:{ep.value}"))
        except Exception as exc:  # noqa: BLE001
            out.append(PluginInfo(ep.name, f"entry-point:{ep.value}",
                                  error=f"{type(exc).__name__}: {exc}"))
    return out


def load_plugins(
    extra_dirs: list[str | Path] | None = None,
    *,
    entry_points: bool = True,
    verbose: bool = False,
) -> list[PluginInfo]:
    """加载全部插件，返回每个插件的加载结果。"""
    results: list[PluginInfo] = []
    for d in plugin_dirs(extra_dirs):
        for py in sorted(d.glob("*.py")):
            if py.name.startswith("_"):
                continue
            info = _load_file(py)
            results.append(info)
            if verbose and not info.ok:
                print(f"[插件] {info}", file=sys.stderr)
    if entry_points:
        results.extend(_load_entry_points())
    return results
