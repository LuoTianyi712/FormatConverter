"""3D 模型互转：STL / OBJ / PLY（零依赖）。

统一内部模型是「三角面列表」——读取时把三种格式归一成
``[((x,y,z), (x,y,z), (x,y,z)), ...]``，写出时再按目标格式序列化。

覆盖了 3D 打印/建模最常见的转换需求（stl↔obj↔ply）。
材质、法线、顶点颜色不会保留（三角面几何完整保留），
这也是轻量转换器（AnyConv 等）同类功能的通行取舍。
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import opt_bool, read_bytes

Mesh = list  # list[tuple[Vec3, Vec3, Vec3]]

FORMATS_3D = ["stl", "obj", "ply"]

__all__ = ["read_stl", "write_stl", "read_obj", "write_obj", "read_ply",
           "write_ply", "convert_mesh"]


# --------------------------------------------------------------------------- #
# STL
# --------------------------------------------------------------------------- #
def read_stl(data: bytes) -> Mesh:
    """STL 读取：自动判别 ASCII / 二进制。"""
    if data[:5].lower() == b"solid" and b"facet" in data[:2048].lower():
        return _read_stl_ascii(data)
    return _read_stl_binary(data)


def _read_stl_binary(data: bytes) -> Mesh:
    if len(data) < 84:
        raise ConversionError("STL 文件不完整（缺少头部）")
    count = struct.unpack_from("<I", data, 80)[0]
    need = 84 + count * 50
    if len(data) < need:
        raise ConversionError(
            f"STL 文件不完整（声明 {count} 个三角面，需要 {need} 字节，实际 {len(data)}）"
        )
    tris = []
    off = 84
    for _ in range(count):
        vals = struct.unpack_from("<12f", data, off)
        v1, v2, v3 = vals[3:6], vals[6:9], vals[9:12]
        tris.append((v1, v2, v3))
        off += 50
    return tris


def _read_stl_ascii(data: bytes) -> Mesh:
    tris = []
    verts: list[tuple[float, float, float]] = []
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "vertex" and len(parts) >= 4:
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
            if len(verts) == 3:
                tris.append(tuple(verts))
                verts = []
    if not tris:
        raise ConversionError("ASCII STL 里没有解析出三角面")
    return tris


def write_stl(tris: Mesh, *, ascii_mode: bool = False, name: str = "uniconvert") -> bytes:
    if ascii_mode:
        lines = [f"solid {name}"]
        for v1, v2, v3 in tris:
            lines.append("  facet normal 0 0 0")
            lines.append("    outer loop")
            for v in (v1, v2, v3):
                lines.append(f"      vertex {v[0]:.6g} {v[1]:.6g} {v[2]:.6g}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
        return ("\n".join(lines) + "\n").encode("ascii")
    out = bytearray(b"\x00" * 80)
    out[:min(len(name), 80)] = name.encode("ascii", "replace")[:80]
    out += struct.pack("<I", len(tris))
    for v1, v2, v3 in tris:
        out += struct.pack("<12fH", 0.0, 0.0, 0.0, *v1, *v2, *v3, 0)
    return bytes(out)


# --------------------------------------------------------------------------- #
# OBJ
# --------------------------------------------------------------------------- #
def read_obj(data: bytes) -> Mesh:
    vertices: list[tuple[float, float, float]] = []
    tris: Mesh = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif parts[0] == "f" and len(parts) >= 4:
            idx = []
            for token in parts[1:]:
                num = token.split("/")[0]
                if not num:
                    continue
                try:
                    i = int(num)
                except ValueError:
                    continue
                idx.append(i - 1 if i > 0 else len(vertices) + i)
            # 扇形三角化（支持四边形及以上）
            for k in range(1, len(idx) - 1):
                try:
                    tris.append((vertices[idx[0]], vertices[idx[k]], vertices[idx[k + 1]]))
                except IndexError:
                    continue
    if not tris:
        raise ConversionError("OBJ 里没有解析出三角面（可能只有点/线）")
    return tris


def write_obj(tris: Mesh, *, name: str = "uniconvert") -> bytes:
    lines = [f"# 由 UniConvert 生成 · {len(tris)} 个三角面", f"o {name}"]
    index = 0
    for v1, v2, v3 in tris:
        for v in (v1, v2, v3):
            lines.append(f"v {v[0]:.6g} {v[1]:.6g} {v[2]:.6g}")
        lines.append(f"f {index + 1} {index + 2} {index + 3}")
        index += 3
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# PLY（ASCII）
# --------------------------------------------------------------------------- #
def read_ply(data: bytes) -> Mesh:
    header_end = data.find(b"end_header")
    if header_end < 0:
        raise ConversionError("PLY 缺少 end_header")
    if b"format ascii" not in data[:header_end]:
        raise ConversionError("暂只支持 ASCII 格式的 PLY（binary_little_endian 暂未支持）")
    lines = data[:header_end].decode("ascii", "replace").splitlines()
    vertex_count = face_count = 0
    vertex_props = 0
    face_props = 0
    current = ""
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element" and len(parts) >= 3:
            current = parts[1]
            if current == "vertex":
                vertex_count = int(parts[2])
            elif current == "face":
                face_count = int(parts[2])
        elif parts[0] == "property":
            if current == "vertex":
                vertex_props += 1
            elif current == "face":
                face_props += 1

    body = data[header_end + len(b"end_header"):].decode("ascii", "replace").split()
    pos = 0
    vertices: list[tuple[float, float, float]] = []
    for _ in range(vertex_count):
        if pos + 3 > len(body):
            raise ConversionError("PLY 顶点数据不完整")
        vertices.append((float(body[pos]), float(body[pos + 1]), float(body[pos + 2])))
        pos += max(3, vertex_props)
    tris: Mesh = []
    for _ in range(face_count):
        if pos >= len(body):
            raise ConversionError("PLY 面数据不完整")
        n = int(body[pos])
        pos += 1
        idx = [int(x) for x in body[pos:pos + n]]
        pos += max(n, face_props - 1) if face_props > 1 else n
        for k in range(1, len(idx) - 1):
            tris.append((vertices[idx[0]], vertices[idx[k]], vertices[idx[k + 1]]))
    if not tris:
        raise ConversionError("PLY 里没有解析出三角面")
    return tris


def write_ply(tris: Mesh) -> bytes:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for v1, v2, v3 in tris:
        base = len(verts)
        verts.extend([v1, v2, v3])
        faces.append((base, base + 1, base + 2))
    lines = [
        "ply",
        "format ascii 1.0",
        "comment generated by UniConvert",  # PLY 头保持 ASCII，兼容老解析器
        f"element vertex {len(verts)}",
        "property float x",
        "property float y",
        "property float z",
        f"element face {len(faces)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    for v in verts:
        lines.append(f"{v[0]:.6g} {v[1]:.6g} {v[2]:.6g}")
    for f in faces:
        lines.append(f"3 {f[0]} {f[1]} {f[2]}")
    return ("\n".join(lines) + "\n").encode("ascii")


# --------------------------------------------------------------------------- #
# 统一入口 + 注册
# --------------------------------------------------------------------------- #
_READERS = {"stl": read_stl, "obj": read_obj, "ply": read_ply}


def convert_mesh(src: Path, dst: Path, opts, src_fmt: str, dst_fmt: str) -> Path:
    data = read_bytes(src)
    reader = _READERS.get(src_fmt)
    if reader is None:  # pragma: no cover
        raise ConversionError(f"不支持读取 {src_fmt}")
    tris = reader(data)
    if dst_fmt == "stl":
        payload = write_stl(tris, ascii_mode=opt_bool(opts, "ascii", False),
                            name=src.stem or "mesh")
    elif dst_fmt == "obj":
        payload = write_obj(tris, name=src.stem or "mesh")
    elif dst_fmt == "ply":
        payload = write_ply(tris)
    else:  # pragma: no cover
        raise ConversionError(f"不支持写出 {dst_fmt}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(payload)
    return dst


def register_three_d_converters() -> int:
    count = 0
    for sf in FORMATS_3D:
        for df in FORMATS_3D:
            if sf == df:
                continue

            def make(sf=sf, df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_mesh(src, dst, opts, sf, df)
                conv.__name__ = f"mesh_{sf}_to_{df}"
                return conv

            REGISTRY.add(sf, df, make(), group="3d",
                         note="三角面几何转换（材质/法线不保留）")
            count += 1
    return count
