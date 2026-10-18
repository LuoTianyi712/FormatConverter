"""压缩包与压缩流。

两类语义刻意分开：

* **多文件容器**（zip / tar / 7z / cbz / rar）：重打包（repack）——
  逐个成员读出、写进目标容器，成员名保持不变；
* **单文件压缩流**（gz / bz2 / xz / zst）：任何格式都能压进去，
  也能解出来（``data.json.gz → data.json`` 就是走这条边）。

另外提供直接使用的 :func:`extract_archive`（解压到目录）
与 :func:`pack_archive`（把一组文件打包成压缩包）。
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterable, Iterator

from ..core.deps import has_dep
from ..core.formats import FORMATS
from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import (
    is_text_bytes,
    need_any,
    opt_bool,
    opt_int,
    opt_str,
    run_tool,
    write_bytes,
)

#: 多文件容器
CONTAINER_FORMATS = ["zip", "tar", "7z", "cbz"]

#: 单文件压缩流
STREAM_FORMATS = ["gz", "bz2", "xz", "zst"]

#: 压缩流探测（magic）
_STREAM_MAGIC = (
    (b"\x1f\x8b", "gz"),
    (b"BZh", "bz2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zst"),
)


# =========================================================================== #
# 容器：读取迭代器 / 写入器
# =========================================================================== #
class _Member:
    """归档里的一个成员（名字 + 惰性数据流 + 是否目录）。"""

    __slots__ = ("name", "stream", "size", "is_dir")

    def __init__(self, name: str, stream, size: int = 0, is_dir: bool = False) -> None:
        self.name = name
        self.stream = stream
        self.size = size
        self.is_dir = is_dir

    def read(self) -> bytes:
        data = self.stream.read()
        return data if isinstance(data, bytes) else data.encode("utf-8")


def _iter_zip(src: Path) -> Iterator[_Member]:
    """流式迭代 ZIP 成员（不整体载入内存，大文件也不会爆）。"""
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            if info.is_dir():
                yield _Member(info.filename, io.BytesIO(b""), 0, is_dir=True)
            else:
                # 注意：生成器挂起在 with 内，ZipFile 在消费完之前保持打开
                yield _Member(info.filename, zf.open(info), info.file_size)


def _iter_tar(src: Path) -> Iterator[_Member]:
    """流式迭代 TAR 成员。"""
    with tarfile.open(src, "r:*") as tf:
        for member in tf:
            if member.isdir():
                yield _Member(member.name, io.BytesIO(b""), 0, is_dir=True)
            elif member.isfile():
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                yield _Member(member.name, fh, member.size)


def _seven_backend() -> str:
    """7z 能力后端：py7zr 模块 或 7z 命令行。"""
    return need_any(["py:py7zr", "exe:7z", "7z"], why="7z 归档读写需要一个后端",
                    feature="7z 支持")


def _iter_7z(src: Path) -> Iterator[_Member]:
    backend = _seven_backend()
    if backend == "py:py7zr":
        import py7zr  # type: ignore

        with tempfile.TemporaryDirectory(prefix="uniconvert-7z-") as tmp:
            with py7zr.SevenZipFile(src, mode="r") as zf:
                zf.extractall(tmp)
            yield from _iter_dir(Path(tmp))
    else:
        with tempfile.TemporaryDirectory(prefix="uniconvert-7z-") as tmp:
            run_tool(["7z", "x", "-y", f"-o{tmp}", str(src)])
            yield from _iter_dir(Path(tmp))


def _iter_rar(src: Path) -> Iterator[_Member]:
    if has_dep("rarfile"):
        import rarfile  # type: ignore

        with rarfile.RarFile(src) as rf:
            for info in rf.infolist():
                if info.isdir():
                    yield _Member(info.filename, io.BytesIO(b""), 0, is_dir=True)
                else:
                    with rf.open(info) as fh:
                        yield _Member(info.filename, io.BytesIO(fh.read()), info.file_size)
        return
    if has_dep("7z") or has_dep("exe:7z"):
        yield from _iter_7z(src)
        return
    need_any(["py:rarfile", "exe:7z", "7z"], why="读取 RAR 需要 rarfile 或 7z",
             feature="RAR 读取")


def _iter_dir(root: Path) -> Iterator[_Member]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            yield _Member(rel, io.BytesIO(p.read_bytes()), p.stat().st_size)


_ITERATORS = {
    "zip": _iter_zip,
    "cbz": _iter_zip,
    "tar": _iter_tar,
    "7z": _iter_7z,
    "rar": _iter_rar,
}


# ---- 写入（流式，成员逐个搬，不整体占内存）----
def _write_zip(dst: Path, members: Iterable[_Member], opts) -> Path:
    level = max(0, min(9, opt_int(opts, "level", 6)))
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as zf:
        for m in members:
            if m.is_dir:
                zf.writestr(m.name.rstrip("/") + "/", b"")
            else:
                with zf.open(m.name, "w") as out:
                    shutil.copyfileobj(m.stream, out, 1 << 20)
    return dst


def _write_tar(dst: Path, members: Iterable[_Member], opts) -> Path:
    with tarfile.open(dst, "w") as tf:
        for m in members:
            info = tarfile.TarInfo(m.name.rstrip("/") if m.is_dir else m.name)
            info.mtime = int(time.time())
            if m.is_dir:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            else:
                info.size = m.size
                info.mode = 0o644
                tf.addfile(info, m.stream)
    return dst


def _write_7z(dst: Path, members: Iterable[_Member], opts) -> Path:
    backend = _seven_backend()
    if backend == "py:py7zr":
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(dst, mode="w") as zf:
            for m in members:
                if not m.is_dir:
                    zf.write(io.BytesIO(m.read()), m.name)
        return dst
    with tempfile.TemporaryDirectory(prefix="uniconvert-7z-") as tmp:
        stage = Path(tmp)
        for m in members:
            if m.is_dir:
                (stage / m.name).mkdir(parents=True, exist_ok=True)
            else:
                target = stage / m.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(m.read())
        run_tool(["7z", "a", "-y", str(dst), "."], cwd=stage)
    return dst


_WRITERS = {
    "zip": _write_zip,
    "cbz": _write_zip,
    "tar": _write_tar,
    "7z": _write_7z,
}


# --------------------------------------------------------------------------- #
# 重打包
# --------------------------------------------------------------------------- #
def repack(src: Path, dst: Path, opts, src_fmt: str, dst_fmt: str) -> Path:
    reader = _ITERATORS.get(src_fmt)
    writer = _WRITERS.get(dst_fmt)
    if reader is None or writer is None:  # pragma: no cover
        raise ConversionError(f"无法重打包：{src_fmt} -> {dst_fmt}")
    dry = opt_bool(opts, "dry_run_scan", False)
    if dry:  # 仅扫描校验
        count = sum(1 for _ in reader(src))
        return write_bytes(dst, f"{count} members\n".encode())
    return writer(dst, reader(src), opts)


# --------------------------------------------------------------------------- #
# 单文件压缩流
# --------------------------------------------------------------------------- #
def _open_decompress(data_path: Path):
    with data_path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(b"\x1f\x8b"):
        return gzip.open(data_path, "rb"), "gz"
    if head.startswith(b"BZh"):
        return bz2.open(data_path, "rb"), "bz2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return lzma.open(data_path, "rb"), "xz"
    if head.startswith(b"\x28\xb5\x2f\xfd"):
        need_any(["py:zstandard", "zstandard"], why="Zstandard 需要 zstandard 模块",
                 feature="Zstd 解压")
        import zstandard  # type: ignore

        fh = data_path.open("rb")
        dctx = zstandard.ZstdDecompressor()
        return _ZstdReader(fh, dctx), "zst"
    raise ConversionError("无法识别的压缩流（支持 gz/bz2/xz/zst）")


class _ZstdReader:
    """把 zstandard 的 copy_stream 包装成可读流。"""

    def __init__(self, fh, dctx) -> None:
        self._fh = fh
        self._buf = io.BytesIO()
        dctx.copy_stream(fh, self._buf)
        self._buf.seek(0)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compress_single(src: Path, dst: Path, opts, kind: str) -> Path:
    level = opt_int(opts, "level", 6)
    if kind == "zst":
        need_any(["py:zstandard", "zstandard"], why="Zstandard 需要 zstandard 模块",
                 feature="Zstd 压缩")
        import zstandard  # type: ignore

        cctx = zstandard.ZstdCompressor(level=max(1, min(22, level)))
        with src.open("rb") as fin, dst.open("wb") as fout:
            cctx.copy_stream(fin, fout)
        return dst
    if kind == "gz":
        # 写入原始文件名，便于 gzip -N / 解压还原
        with src.open("rb") as fin, dst.open("wb") as fout:
            gz = gzip.GzipFile(filename=src.name, mode="wb", fileobj=fout,
                               compresslevel=max(1, min(9, level)), mtime=int(time.time()))
            shutil.copyfileobj(fin, gz)
            gz.close()
        return dst
    opener = {"bz2": bz2.open, "xz": lzma.open}[kind]
    with src.open("rb") as fin, opener(dst, "wb", preset=level) as fout:
        shutil.copyfileobj(fin, fout)
    return dst


def _magic_match(head: bytes, magic: tuple[tuple[int, bytes], ...]) -> bool:
    """文件头是否命中该格式的任一 magic（offset == -1 的尾部特征跳过不查）。"""
    if not magic:
        return True  # 没有特征可查，放过
    for offset, payload in magic:
        if offset < 0:
            return True
        if head[offset:offset + len(payload)] == payload:
            return True
    return False


def _validate_text_target(text: str, dst_fmt: str) -> None:
    """对「有语法的文本格式」做轻量解析校验。

    压缩流既不携带格式信息，也不像压缩包那样有成员名可对；
    ``a.yaml.gz → a.srt`` 这种错误只有『解出来看着像文字』
    这一层校验时会被放过。这里对 json/toml/xml/字幕做一次真解析，
    把静默的错位结果变成明确报错。
    """
    try:
        if dst_fmt == "json":
            import json as _json

            _json.loads(text)
        elif dst_fmt == "toml":
            if _toml_read() is not None:
                import tomllib  # type: ignore

                tomllib.loads(text)
        elif dst_fmt == "xml":
            import xml.etree.ElementTree as _ET

            _ET.fromstring(text)
        elif dst_fmt == "obj":
            # OBJ：至少要有顶点/面行，否则大概率是别的文本被硬当成模型
            if not any(ln.startswith(("v ", "f ")) for ln in text.splitlines()[:5000]):
                raise ValueError("没有 v/f 顶点面数据")
        elif dst_fmt == "ply":
            if not text.lstrip().startswith("ply"):
                raise ValueError("缺少 ply 文件头")
        elif dst_fmt == "svg":
            if "<svg" not in text[:8192]:
                raise ValueError("缺少 <svg> 标签")
        elif dst_fmt in ("srt", "vtt", "ass", "ssa", "lrc"):
            from . import subtitle

            parser = subtitle._PARSERS[dst_fmt]
            if not parser(text):
                raise ValueError("没有解析出任何字幕条目")
    except ConversionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(
            f"解压出来的内容不符合 .{dst_fmt} 格式（{exc}）"
        ) from exc


def _toml_read():
    try:
        import tomllib  # type: ignore

        return tomllib
    except ImportError:  # pragma: no cover
        try:
            import tomli  # type: ignore

            return tomli
        except ImportError:
            return None


def decompress_single(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """解压单文件压缩流，并校验内容与目标格式相符。

    * 目标为文本格式 → 内容必须可按文本解码（结构化格式再做轻量解析）；
    * 目标格式有 magic 特征 → 解压出的文件头必须命中；
    * 目标为 ``bin`` 等无特征格式 → 原样写出。

    校验的意义：压缩流本身不携带格式信息，
    ``video.mp4.gz → out.gif`` 这类错误只有在解压后才发现得了，
    在这里拦住比放出损坏文件好。
    """
    stream, kind = _open_decompress(src)
    fmt = FORMATS.get(dst_fmt)
    check_text = bool(fmt and fmt.text)
    fmt_magic = fmt.magic if fmt else ()
    try:
        if check_text:
            data = stream.read()
            if not is_text_bytes(data[:8192]):
                raise ConversionError(
                    f"解压出来的内容不是文本，无法写入 {dst.name}"
                    f"（原始内容像是 {kind} 压缩的二进制数据；可改目标为 .bin）"
                )
            text = data.decode("utf-8", "replace")
            _validate_text_target(text, dst_fmt)
            return write_bytes(dst, data)

        with dst.open("wb") as fout:
            first = stream.read(1 << 20)
            if fmt_magic and first and not _magic_match(first[:4096], fmt_magic):
                raise ConversionError(
                    f"解压出来的内容不是有效的 {dst_fmt.upper()} 文件"
                    f"（文件头与 .{dst_fmt} 不符）。"
                    f"若你本意是音视频/图片等转码，请安装对应依赖：uniconvert doctor"
                )
            fout.write(first)
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                fout.write(chunk)
    except Exception:
        dst.unlink(missing_ok=True)  # 校验失败时不留半成品
        raise
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
    return dst


# --------------------------------------------------------------------------- #
# 解压 / 打包（工具函数，CLI 也用）
# --------------------------------------------------------------------------- #
def detect_stream_kind(path: Path) -> str | None:
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    for magic, kind in _STREAM_MAGIC:
        if head.startswith(magic):
            return kind
    return None


def _safe_member_path(outdir: Path, name: str) -> Path:
    """防 Zip Slip：成员路径必须落在 outdir 内。"""
    target = (outdir / name).resolve()
    root = outdir.resolve()
    if not str(target).startswith(str(root)):
        raise ConversionError(f"归档成员路径越界（可能是恶意压缩包）：{name}")
    return target


def extract_archive(src: str | Path, outdir: str | Path, *, fmt: str = "",
                    opts=None) -> list[Path]:
    """把归档解压到目录，返回写出的文件列表（防路径穿越）。"""
    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if not fmt:
        from ..core.detect import sniff

        sniffed = sniff(src)
        fmt = sniffed.name if sniffed else (src.suffix.lstrip(".").lower() or "zip")

    written: list[Path] = []

    if fmt in ("gz", "bz2", "xz", "zst"):
        # 单文件流：去掉压缩后缀作为输出名
        base = src.name
        for suffix in (".gz", ".bz2", ".xz", ".zst", ".tgz"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
                break
        else:
            base = src.stem or "extracted"
        out = outdir / base
        decompress_single(src, out, opts, "bin")
        written.append(out)
        return written

    # tar.gz / tar.xz 等复合后缀：先解流、再解 tar
    lower = src.name.lower()
    if fmt == "tar" or lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(src, "r:*") as tf:
            for member in tf:
                target = _safe_member_path(outdir, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                target.write_bytes(fh.read())
                written.append(target)
        return written

    # zip / cbz / 7z / rar 走成员迭代器，顺带防穿越
    reader = _ITERATORS.get(fmt)
    if reader is None:
        raise ConversionError(f"不支持的归档格式：{fmt}")
    for m in reader(src):
        target = _safe_member_path(outdir, m.name)
        if m.is_dir:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(m.read())
        written.append(target)
    return written


def pack_archive(files: Iterable[str | Path], dst: str | Path, *,
                 fmt: str = "", opts=None, base: str | Path | None = None) -> Path:
    """把一组文件打包为归档（默认按目标扩展名选容器）。"""
    dst = Path(dst)
    if not fmt:
        fmt = dst.suffix.lstrip(".").lower() or "zip"
        if fmt not in _WRITERS:
            raise ConversionError(
                f"无法打包为 {fmt}（容器支持：{', '.join(sorted(_WRITERS))}）"
            )
    writer = _WRITERS.get(fmt)
    if writer is None:  # pragma: no cover
        raise ConversionError(f"不支持的归档格式：{fmt}")
    base_dir = Path(base) if base else None

    def members() -> Iterator[_Member]:
        for f in files:
            p = Path(f)
            if not p.is_file():
                raise ConversionError(f"打包源不是文件：{p}")
            name = p.name
            if base_dir is not None:
                try:
                    name = p.resolve().relative_to(base_dir.resolve()).as_posix()
                except ValueError:
                    name = p.name
            yield _Member(name, io.BytesIO(p.read_bytes()), p.stat().st_size)

    return writer(dst, members(), opts)


# =========================================================================== #
# 注册
# =========================================================================== #
def register_archive_converters() -> int:
    count = 0

    # ---- 容器重打包（含 rar 只读）----
    readers = {"zip", "tar", "7z", "cbz", "rar"}
    writers = set(CONTAINER_FORMATS)
    for sf in readers:
        for df in writers:
            if sf == df:
                continue
            if sf == "rar" and df == "cbz":
                pass  # 允许 rar -> cbz（图片包转换很常见）

            def make(sf=sf, df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return repack(src, dst, opts, sf, df)
                conv.__name__ = f"repack_{sf}_to_{df}"
                return conv

            REGISTRY.add(
                sf, df, make(),
                group="archive",
                note="重打包（成员原样保留）",
            )
            count += 1

    # ---- 单文件压缩：任何格式 -> gz/bz2/xz/zst ----
    all_formats = [n for n in FORMATS if n not in STREAM_FORMATS]
    for kind in STREAM_FORMATS:
        deps: tuple[str, ...] = ()
        if kind == "zst":
            deps = ("zstandard",)

        def make(kind=kind):
            def conv(src: Path, dst: Path, opts) -> Path:
                return compress_single(src, dst, opts, kind)
            conv.__name__ = f"compress_{kind}"
            return conv

        REGISTRY.add(
            all_formats, kind, make(),
            deps=deps,
            group="archive",
            weight=2.0, quality=0.9,
            note="单文件压缩",
        )
        count += len(all_formats)

        # ---- 单文件解压：gz/bz2/xz/zst -> 可校验的目标格式 ----
        # 只向「内容可验证」的目标注册解压边：
        # 文本格式（可解析校验）+ 有 magic 特征的格式（可查文件头）+ bin（原样）。
        # 这样 midi -> gz -> stl 这类「字节重解释」的伪路径在规划期就被排除。
        def _decompressible(fmt) -> bool:
            return bool(fmt.text or fmt.magic) or fmt.name == "bin"

        def make_dec(kind=kind):
            def conv(src: Path, dst: Path, opts) -> Path:
                return decompress_single(src, dst, opts, _dst_fmt_from_name(dst))
            conv.__name__ = f"decompress_{kind}"
            return conv

        targets = [
            n for n, f in FORMATS.items()
            if n != kind and _decompressible(f)
        ]
        REGISTRY.add(
            kind, targets, make_dec(),
            deps=deps,
            group="archive",
            weight=2.0, quality=0.9,
            note="单文件解压",
        )
        count += len(targets)

    return count


def _dst_fmt_from_name(dst: Path) -> str:
    from ..core.formats import guess_format

    fmt = guess_format(dst.name)
    return fmt.name if fmt else "bin"
