"""UniConvert 异常体系。

所有对外抛出的异常都继承自 :class:`UniconvertError`，便于调用方一次捕获。
"""

from __future__ import annotations


class UniconvertError(Exception):
    """所有 UniConvert 异常的基类。"""


class UnsupportedFormatError(UniconvertError):
    """源格式或目标格式无法识别。"""

    def __init__(self, fmt: str, known: list[str] | None = None) -> None:
        self.fmt = fmt
        self.known = known or []
        hint = ""
        if self.known:
            near = [k for k in self.known if fmt[:2] and k.startswith(fmt[:2])][:6]
            if near:
                hint = f"（是不是想用：{', '.join(near)}？）"
        super().__init__(f"无法识别的格式：{fmt!r}{hint}")


class NoRouteError(UniconvertError):
    """转换图里找不到从源格式到目标格式的通路。"""

    def __init__(self, src: str, dst: str) -> None:
        self.src, self.dst = src, dst
        super().__init__(f"没有可用的转换路径：{src} -> {dst}（可能是缺少可选依赖或插件）")


class ConversionError(UniconvertError):
    """转换过程中出错。"""

    def __init__(self, message: str, *, src: str = "", dst: str = "", cause: BaseException | None = None) -> None:
        self.src, self.dst, self.cause = src, dst, cause
        prefix = f"[{src}->{dst}] " if src or dst else ""
        super().__init__(prefix + message)


class DependencyMissingError(UniconvertError):
    """该转换需要的外部依赖（软件或 Python 包）未安装。"""

    def __init__(self, need: str, *, why: str = "", install: str = "") -> None:
        self.need, self.why, self.install = need, why, install
        msg = f"缺少依赖：{need}"
        if why:
            msg += f"（{why}）"
        if install:
            msg += f"\n  安装：{install}"
        super().__init__(msg)


class ValidationError(UniconvertError):
    """输入文件或参数不合法。"""


class CancelledError(UniconvertError):
    """任务被用户取消。"""
