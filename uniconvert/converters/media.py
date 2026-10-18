"""音视频转换（FFmpeg 后端）。

音视频世界的「万能」几乎等价于 FFmpeg 的参数翻译：
视频 ⇄ 视频、视频 → 音频、音频 ⇄ 音频、视频 → 字幕（抽取）、
视频 → GIF、图片 → 视频（延时/幻灯片要配多图，单图生成循环视频）。

本模块在规划期用 ``deps=("ffmpeg",)`` 过滤路由；
ffmpeg 缺失时引擎直接抛出带安装提示的依赖错误，而不是在运行时炸掉。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import need_dep, opt_bool, opt_float, opt_int, opt_str, run_tool

VIDEO_FORMATS = ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg", "ts", "3gp", "ogv"]
AUDIO_FORMATS = ["mp3", "aac", "flac", "wav", "ogg", "opus", "wma", "aiff", "m4b", "ac3", "dts"]

#: 各容器默认视频编码
_VIDEO_CODEC = {
    "mp4": "libx264", "mkv": "libx264", "avi": "mpeg4", "mov": "libx264",
    "webm": "libvpx-vp9", "flv": "libx264", "wmv": "wmv2", "mpg": "mpeg2video",
    "ts": "libx264", "3gp": "libx264", "ogv": "libtheora",
}

#: 各容器默认音频编码
_AUDIO_CODEC = {
    "mp3": "libmp3lame", "aac": "aac", "flac": "flac", "wav": "pcm_s16le",
    "ogg": "libvorbis", "opus": "libopus", "wma": "wmav2", "aiff": "pcm_s16be",
    "m4b": "aac", "ac3": "ac3", "dts": "dca",
    "mp4": "aac", "mkv": "aac", "avi": "libmp3lame", "mov": "aac",
    "webm": "libopus", "flv": "aac", "wmv": "wmav2", "mpg": "mp2",
    "ts": "aac", "3gp": "aac", "ogv": "libvorbis",
}

AUDIO_TARGETS = set(AUDIO_FORMATS)


def _ffmpeg() -> str:
    need_dep("ffmpeg", why="音视频转换需要 FFmpeg")
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise ConversionError("找不到 ffmpeg 可执行文件")


def _build_filters(opts, *, to_gif: bool = False) -> str:
    """把 scale/rotate/subtitle/fps 等选项翻译成 -vf 滤镜串。"""
    filters: list[str] = []

    resolution = opt_str(opts, "resolution", "")
    if resolution:
        scale = resolution.replace("×", "x").replace("*", "x")
        filters.append(f"scale={scale}")

    rotate = opt_float(opts, "rotate", 0.0)
    if rotate:
        deg = int(rotate) % 360
        if rotate % 90 != 0:
            filters.append(f"rotate={rotate}*PI/180")
        elif deg == 90:
            filters.append("transpose=clock")
        elif deg == 180:
            filters.append("transpose=clock,transpose=clock")
        elif deg == 270:
            filters.append("transpose=cclock")

    sub = opt_str(opts, "sub", "") or opt_str(opts, "subtitles", "")
    if sub:
        escaped = sub.replace("\\", "/").replace(":", "\\:")
        filters.append(f"subtitles='{escaped}'")

    if to_gif:
        fps = opt_int(opts, "fps", 10)
        width = opt_int(opts, "width", 480)
        filters.append(f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")

    return ",".join(filters)


def convert_media(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """音视频转换主入口（视频/音频/图片 → 视频/音频/GIF）。"""
    ffmpeg = _ffmpeg()
    args: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    src_is_image = src.suffix.lower().lstrip(".") in (
        "png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif",
    )

    # 图片 → 视频：单图循环
    if src_is_image:
        duration = opt_float(opts, "duration", 5.0)
        fps = opt_int(opts, "fps", 25)
        args += ["-loop", "1", "-framerate", str(fps), "-t", str(duration), "-i", str(src)]
    else:
        args += ["-i", str(src)]

    # 裁剪
    start = opt_str(opts, "start", "") or opt_str(opts, "ss", "")
    end = opt_str(opts, "end", "") or opt_str(opts, "to", "")
    if start:
        args += ["-ss", str(start)]
    if end:
        args += ["-to", str(end)]

    to_gif = dst_fmt == "gif"
    vf = _build_filters(opts, to_gif=to_gif)
    if vf:
        args += ["-vf", vf]

    if dst_fmt in AUDIO_TARGETS or opt_bool(opts, "mute", False) or opt_bool(opts, "no_video", False):
        args += ["-vn"]

    # 视频编码
    if dst_fmt not in AUDIO_TARGETS and not opt_bool(opts, "no_video", False):
        vcodec = opt_str(opts, "vcodec", "") or _VIDEO_CODEC.get(dst_fmt, "libx264")
        if vcodec != "copy":
            args += ["-c:v", vcodec]
            bitrate = opt_str(opts, "bitrate", "")
            has_crf = opts is not None and "crf" in opts
            if has_crf or not bitrate:
                crf = opt_int(opts, "crf", 23)
                if "x264" in vcodec or "x265" in vcodec:
                    args += ["-crf", str(crf), "-preset", opt_str(opts, "preset", "medium")]
                elif "vp9" in vcodec:
                    args += ["-crf", str(crf), "-b:v", "0"]
            if bitrate:
                args += ["-b:v", bitrate]
            pix = opt_str(opts, "pix_fmt", "yuv420p")
            if pix and dst_fmt in ("mp4", "mov", "webm", "flv", "ts", "3gp", "mkv"):
                args += ["-pix_fmt", pix]
        else:
            args += ["-c:v", "copy"]

    # 音频编码
    if not opt_bool(opts, "mute", False) and not opt_bool(opts, "no_audio", False):
        acodec = opt_str(opts, "acodec", "") or _AUDIO_CODEC.get(dst_fmt, "aac")
        if acodec != "copy":
            args += ["-c:a", acodec]
            ab = opt_str(opts, "audio_bitrate", "") or opt_str(opts, "ab", "")
            if ab:
                args += ["-b:a", ab]
            elif dst_fmt == "mp3":
                args += ["-q:a", "2"]
            elif acodec == "aac" and dst_fmt in AUDIO_TARGETS:
                args += ["-b:a", "192k"]
        else:
            args += ["-c:a", "copy"]

    fps = opt_int(opts, "fps", 0)
    if fps and not src_is_image and not to_gif:
        args += ["-r", str(fps)]

    if dst_fmt == "mp4" and opt_bool(opts, "faststart", True):
        args += ["-movflags", "+faststart"]

    args += [str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_tool(args, timeout=7200)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("ffmpeg 没有产出有效文件（检查流是否存在，例如该视频可能不含音轨）")
    return dst


# --------------------------------------------------------------------------- #
# 视频 → 字幕抽取
# --------------------------------------------------------------------------- #
def extract_subtitle(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    ffmpeg = _ffmpeg()
    stream = opt_int(opts, "stream", 0)
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-map", f"0:s:{stream}", str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_tool(args, timeout=600)
    except ConversionError as exc:
        raise ConversionError(
            "抽取字幕失败（该文件可能没有字幕轨；可用 stream 选项选第几条轨）"
        ) from exc
    return dst


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register_media_converters() -> int:
    count = 0
    AV = VIDEO_FORMATS + AUDIO_FORMATS

    for sf in AV:
        for df in AV:
            if sf == df:
                continue

            def make(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_media(src, dst, opts, df)
                conv.__name__ = f"media_to_{df}"
                return conv

            REGISTRY.add(
                sf, df, make(),
                deps=("ffmpeg",),
                group="ffmpeg",
                weight=1.2, quality=0.85,
                note="FFmpeg 转码",
            )
            count += 1

    # 视频 → GIF（带调色板优化）
    for sf in ("mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg", "ts", "3gp"):
        REGISTRY.add(sf, "gif", convert_media, deps=("ffmpeg",), group="ffmpeg",
                     weight=1.1, quality=0.9, note="GIF（调色板优化）")
        count += 1

    # 视频 → 单帧图片
    for sf in ("mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg", "ts", "3gp"):
        for df in ("png", "jpg"):
            def make_frame(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_frame(src, dst, opts, df)
                conv.__name__ = f"frame_to_{df}"
                return conv

            REGISTRY.add(sf, df, make_frame(), deps=("ffmpeg",), group="ffmpeg",
                         weight=1.5, quality=0.9, note="抽帧（first frame / time 选项）")
            count += 1

    # 图片 → 视频
    for sf in ("png", "jpg", "webp", "bmp", "tiff"):
        for df in ("mp4", "webm", "mkv"):
            def make_loop(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return convert_media(src, dst, opts, df)
                conv.__name__ = f"image_loop_{df}"
                return conv

            REGISTRY.add(sf, df, make_loop(), deps=("ffmpeg",), group="ffmpeg",
                         weight=1.8, quality=0.8,
                         note="图片循环成视频（duration/fps 选项）")
            count += 1

    # 视频 → 字幕
    for sf in ("mp4", "mkv", "ts", "mov", "avi", "webm"):
        for df in ("srt", "ass", "vtt"):
            def make_sub(df=df):
                def conv(src: Path, dst: Path, opts) -> Path:
                    return extract_subtitle(src, dst, opts, df)
                conv.__name__ = f"video_sub_{df}"
                return conv

            REGISTRY.add(sf, df, make_sub(), deps=("ffmpeg",), group="ffmpeg",
                         weight=1.5, quality=0.9, note="抽取内嵌字幕轨")
            count += 1

    return count


def convert_frame(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """视频 → 单帧图片（默认第一帧，``time`` 选项指定时间点）。"""
    ffmpeg = _ffmpeg()
    time_point = opt_str(opts, "time", "")
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if time_point:
        args += ["-ss", time_point]
    args += ["-i", str(src), "-frames:v", "1"]
    scale = opt_str(opts, "resolution", "")
    if scale:
        args += ["-vf", f"scale={scale}"]
    args += [str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_tool(args, timeout=600)
    return dst
