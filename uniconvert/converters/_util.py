"""转换器公共工具：选项读取、文件 IO、外部命令调用、依赖报错。"""

from __future__ import annotations

import codecs
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from ..core.deps import INSTALL_HINTS, has_dep
from ..errors import ConversionError, DependencyMissingError

__all__ = [
    "need_dep", "need_any", "run_tool", "read_text", "write_text",
    "read_bytes", "write_bytes", "is_text_bytes", "opt_bool", "opt_int",
    "opt_float", "opt_str", "decode_text", "newline_for", "report_progress",
]

#: 依赖名 -> pip 包名（提示安装用）
_PIP_ALIAS = {
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "fitz": "PyMuPDF",
}


def need_dep(name: str, *, why: str = "") -> None:
    """断言某个依赖可用，否则抛出带安装提示的异常。"""
    if has_dep(name):
        return
    key = name[4:] if name.startswith("exe:") else name
    hint = INSTALL_HINTS.get(key) or INSTALL_HINTS.get(name)
    if hint is None:
        pkg = _PIP_ALIAS.get(name, name)
        if name.startswith("exe:"):
            hint = f"安装 {key} 并加入 PATH"
        else:
            hint = f"pip install {pkg}"
    raise DependencyMissingError(name, why=why or "该转换需要此依赖", install=hint)


def need_any(names: Iterable[str], *, why: str = "", feature: str = "") -> str:
    """一组「任选其一」的依赖里至少一个可用，返回选中的那个。"""
    names = list(names)
    for name in names:
        if has_dep(name):
            return name
    hints = " 或 ".join(INSTALL_HINTS.get(n, f"安装 {n}") for n in names)
    raise DependencyMissingError(
        " / ".join(names),
        why=why or f"{feature or '该功能'}需要以下任意一个",
        install=hints,
    )


# --------------------------------------------------------------------------- #
# 文件 IO
# --------------------------------------------------------------------------- #
def read_bytes(path: str | Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ConversionError(f"读取失败：{exc}") from exc


def write_bytes(path: str | Path, data: bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_bytes(data)
    except OSError as exc:
        raise ConversionError(f"写入失败：{exc}") from exc
    return p


def decode_text(data: bytes, encoding: str = "utf-8-sig") -> str:
    """按指定编码解码，失败时给出干净的中文错误。"""
    if data.startswith(codecs.BOM_UTF8) and encoding.lower().replace("_", "-") in (
        "auto", "utf-8", "utf-8-sig"
    ):
        data = data[len(codecs.BOM_UTF8):]
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise ConversionError(
            f"按 {encoding} 解码失败（可用 -o encoding=gb18030 指定编码）：{exc}"
        ) from exc


def read_text(path: str | Path, opts=None, *, default_encoding: str = "utf-8-sig") -> str:
    """读文本文件；``opts.encoding`` 优先，默认自动识别 UTF-8 BOM。"""
    raw = read_bytes(path)
    enc = opt_str(opts, "encoding", default_encoding) or default_encoding
    if enc.lower() in ("auto", "detect"):
        enc = _detect_encoding(raw)
    return decode_text(raw, enc)


def _detect_encoding(raw: bytes) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"


def newline_for(opts=None) -> str:
    """换行风格：lf / crlf / native（默认 native）。"""
    style = (opt_str(opts, "newline", "") or "").lower()
    if style in ("lf", "\n", "unix"):
        return "\n"
    if style in ("crlf", "\r\n", "windows", "dos"):
        return "\r\n"
    return os.linesep


def write_text(path: str | Path, text: str, opts=None, *, default_encoding: str = "utf-8") -> Path:
    """写文本文件；``opts.to_encoding`` 优先，换行按 ``opts.newline`` 归一。"""
    enc = opt_str(opts, "to_encoding", "") or opt_str(opts, "encoding", default_encoding)
    if enc.lower() in ("auto", "detect", ""):
        enc = default_encoding
    nl = newline_for(opts)
    if nl != "\n":
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", nl)
    data = text.encode(enc, errors="replace" if enc.lower() == "ascii" else "strict")
    if opt_bool(opts, "bom", False) and enc.lower().startswith("utf-8"):
        data = codecs.BOM_UTF8 + data
    return write_bytes(path, data)


def is_text_bytes(sample: bytes) -> bool:
    """字节样本是否像文本（给「解压后校验」「bin→txt」用）。"""
    if not sample:
        return True
    if b"\x00" in sample:
        return sample[:2] in (b"\xff\xfe", b"\xfe\xff")
    for enc in ("utf-8", "gb18030"):
        try:
            sample.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


# --------------------------------------------------------------------------- #
# 选项读取（容忍 opts 为 None）
# --------------------------------------------------------------------------- #
def opt_str(opts, key: str, default: str = "") -> str:
    if opts is None:
        return default
    try:
        v = opts.get(key, default)
    except AttributeError:
        v = default
    return default if v is None else str(v)


def opt_int(opts, key: str, default: int) -> int:
    try:
        return int(float(opt_str(opts, key, str(default))))
    except (TypeError, ValueError):
        return default


def opt_float(opts, key: str, default: float) -> float:
    try:
        return float(opt_str(opts, key, str(default)))
    except (TypeError, ValueError):
        return default


def opt_bool(opts, key: str, default: bool = False) -> bool:
    if opts is None:
        return default
    try:
        v = opts.get(key, default)
    except AttributeError:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(v)


def report_progress(opts, fraction: float, note: str = "") -> None:
    """向引擎汇报细粒度进度（转换器内部调用；没有钩子时静默忽略）。

    引擎执行每个步骤前会在 ``opts["__progress__"]`` 放一个回调，
    长任务（ffmpeg 转码等）周期性调用它，前端就能画出真实进度。
    """
    hook = None
    if opts is not None:
        try:
            hook = opts.get("__progress__")
        except AttributeError:
            hook = None
    if callable(hook):
        try:
            hook(max(0.0, min(1.0, float(fraction))), str(note))
        except Exception:  # noqa: BLE001 - 回调故障不影响转换
            pass


# --------------------------------------------------------------------------- #
# 外部命令
# --------------------------------------------------------------------------- #
def run_tool(
    cmd: list[str] | str,
    *,
    timeout: float | None = None,
    check: bool = True,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess:
    """跑外部命令；失败时把 stderr 尾部带进 :class:`ConversionError`。"""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd, posix=False)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"找不到外部程序：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"外部程序超时（>{timeout}s）：{cmd[0]}") from exc
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = "\n    ".join(err[-6:]) if err else "(无 stderr 输出)"
        raise ConversionError(f"{cmd[0]} 退出码 {proc.returncode}：\n    {tail}")
    return proc
