"""音视频转换（FFmpeg 后端）。

音视频世界的「万能」几乎等价于 FFmpeg 的参数翻译：
视频 ⇄ 视频、视频 → 音频、音频 ⇄ 音频、视频 → 字幕（抽取）、
视频 → GIF、图片 → 视频、视频 → HLS(m3u8)、抽帧。

本模块在规划期用 ``deps=("ffmpeg",)`` 过滤路由；
ffmpeg 缺失时引擎直接抛出带安装提示的依赖错误。

增强能力（对标格式工厂/市面在线工具）：

* ``profile=wechat|douyin|bilibili|tiny`` —— 平台预设（微信 720p 省流量、
  抖音竖屏 1080x1920 填充、B 站 1080p 高画质、tiny 极致压缩）；
* ``speed=1.5`` 变速（视频+音频联动）、``volume=-6`` 音量增益、
  ``fade_in=1``/``fade_out=1``（配合 end 使用）淡入淡出；
* ``cfr``/``resolution``/``crf``/``bitrate``/``trim``/``rotate``/``sub``（字幕压制）；
* 真实进度：解析 ffmpeg ``-progress`` 输出，前进到引擎进度条。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError
from ._util import (
    need_dep,
    opt_bool,
    opt_float,
    opt_int,
    opt_str,
    report_progress,
    run_tool,
)

VIDEO_FORMATS = ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg", "ts",
                 "3gp", "ogv", "m3u8"]
AUDIO_FORMATS = ["mp3", "aac", "flac", "wav", "ogg", "opus", "wma", "aiff", "m4b", "ac3", "dts"]

#: 各容器默认视频编码
_VIDEO_CODEC = {
    "mp4": "libx264", "mkv": "libx264", "avi": "mpeg4", "mov": "libx264",
    "webm": "libvpx-vp9", "flv": "libx264", "wmv": "wmv2", "mpg": "mpeg2video",
    "ts": "libx264", "3gp": "libx264", "ogv": "libtheora", "m3u8": "libx264",
}

#: 各容器默认音频编码
_AUDIO_CODEC = {
    "mp3": "libmp3lame", "aac": "aac", "flac": "flac", "wav": "pcm_s16le",
    "ogg": "libvorbis", "opus": "libopus", "wma": "wmav2", "aiff": "pcm_s16be",
    "m4b": "aac", "ac3": "ac3", "dts": "dca",
    "mp4": "aac", "mkv": "aac", "avi": "libmp3lame", "mov": "aac",
    "webm": "libopus", "flv": "aac", "wmv": "wmav2", "mpg": "mp2",
    "ts": "aac", "3gp": "aac", "ogv": "libvorbis", "m3u8": "aac",
}

AUDIO_TARGETS = set(AUDIO_FORMATS)

#: 平台/场景预设 —— 命中市场高频需求（发微信、发抖音、上传 B 站、极致压缩）
PROFILES: dict[str, dict] = {
    "wechat": {
        "resolution": "1280x720", "crf": 28, "preset": "fast",
        "audio_bitrate": "96k", "faststart": True,
    },
    "douyin": {
        "fit": "1080x1920", "crf": 23, "fps": 30,
        "audio_bitrate": "128k", "faststart": True,
    },
    "bilibili": {
        "resolution": "1920x1080", "crf": 21, "preset": "slow",
        "audio_bitrate": "192k", "faststart": True,
    },
    "tiny": {
        "resolution": "854x480", "crf": 30, "preset": "fast",
        "audio_bitrate": "64k",
    },
    "audio": {
        "no_video": True, "audio_bitrate": "192k",
    },
}


def _ffmpeg() -> str:
    need_dep("ffmpeg", why="音视频转换需要 FFmpeg")
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise ConversionError("找不到 ffmpeg 可执行文件")


def _apply_profile(opts):
    profile = opt_str(opts, "profile", "").lower()
    if not profile:
        return opts
    preset = PROFILES.get(profile)
    if preset is None:
        raise ConversionError(
            f"未知预设 profile={profile!r}（可选：{', '.join(sorted(PROFILES))}）"
        )
    from ..core.job import Options

    merged = Options(dict(opts.values) if opts is not None else {})
    for key, value in preset.items():
        if key not in merged:
            merged[key] = value
    return merged


def _seconds(value: str) -> float | None:
    """``"90"`` / ``"1:30"`` / ``"01:30:00"`` → 秒。"""
    if not value:
        return None
    try:
        if ":" not in value:
            return float(value)
        parts = [float(p) for p in value.split(":")]
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds
    except ValueError:
        return None


def _atempo_chain(speed: float) -> str:
    """把任意倍速拆成 atempo 滤镜链（单个 atempo 限制 0.5~2.0）。"""
    factors: list[str] = []
    s = speed
    while s > 2.0:
        factors.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        factors.append("atempo=0.5")
        s *= 2.0
    factors.append(f"atempo={s:.4f}")
    return ",".join(factors)


def _build_video_filters(opts, *, to_gif: bool = False) -> str:
    """视频滤镜串：scale / fit(填充) / rotate / speed / 字幕 / 淡入淡出 / GIF。"""
    filters: list[str] = []

    fit = opt_str(opts, "fit", "")
    resolution = opt_str(opts, "resolution", "")
    if fit:
        fit = fit.replace("×", "x").replace("*", "x")
        w, _, h = fit.partition("x")
        if w and h:
            filters.append(
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
            )
    elif resolution:
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

    speed = opt_float(opts, "speed", 1.0)
    if speed and abs(speed - 1.0) > 1e-6:
        filters.append(f"setpts=PTS/{speed:g}")

    fade_in = opt_float(opts, "fade_in", 0.0)
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in:g}")
    fade_out = opt_float(opts, "fade_out", 0.0)
    end = _seconds(opt_str(opts, "end", "") or opt_str(opts, "to", ""))
    if fade_out > 0 and end is not None:
        filters.append(f"fade=t=out:st={max(0.0, end - fade_out):g}:d={fade_out:g}")

    sub = opt_str(opts, "sub", "") or opt_str(opts, "subtitles", "")
    if sub:
        escaped = sub.replace("\\", "/").replace(":", "\\:")
        filters.append(f"subtitles='{escaped}'")

    if to_gif:
        fps = opt_int(opts, "fps", 10)
        width = opt_int(opts, "width", 480)
        filters.append(
            f"fps={fps},scale={width}:-1:flags=lanczos,"
            f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
        )

    return ",".join(filters)


def _build_audio_filters(opts) -> str:
    """音频滤镜串：speed(atempo) / volume /  fade。"""
    filters: list[str] = []
    speed = opt_float(opts, "speed", 1.0)
    if speed and abs(speed - 1.0) > 1e-6:
        filters.append(_atempo_chain(speed))
    volume = opt_float(opts, "volume", 0.0)
    if volume:
        filters.append(f"volume={volume:g}dB")
    fade_in = opt_float(opts, "fade_in", 0.0)
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:g}")
    fade_out = opt_float(opts, "fade_out", 0.0)
    end = _seconds(opt_str(opts, "end", "") or opt_str(opts, "to", ""))
    if fade_out > 0 and end is not None:
        filters.append(f"afade=t=out:st={max(0.0, end - fade_out):g}:d={fade_out:g}")
    return ",".join(filters)


def build_media_args(
    src: Path,
    dst: Path,
    opts,
    dst_fmt: str,
    *,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """把转换意图翻译成 ffmpeg 参数（纯函数，便于单测）。"""
    opts = _apply_profile(opts)
    args: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                       "-progress", "pipe:1", "-nostats"]

    src_is_image = src.suffix.lower().lstrip(".") in (
        "png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif",
    )

    if src_is_image and dst_fmt not in ("gif",):
        duration = opt_float(opts, "duration", 5.0)
        fps = opt_int(opts, "fps", 25)
        args += ["-loop", "1", "-framerate", str(fps), "-t", str(duration), "-i", str(src)]
    else:
        args += ["-i", str(src)]

    start = opt_str(opts, "start", "") or opt_str(opts, "ss", "")
    end = opt_str(opts, "end", "") or opt_str(opts, "to", "")
    if start:
        args += ["-ss", str(start)]
    if end:
        args += ["-to", str(end)]

    to_gif = dst_fmt == "gif"
    vf = _build_video_filters(opts, to_gif=to_gif)
    if vf:
        args += ["-vf", vf]

    audio_only = (dst_fmt in AUDIO_TARGETS
                  or opt_bool(opts, "mute", False)
                  or opt_bool(opts, "no_video", False)
                  or opt_bool(opts, "vn", False))
    if audio_only:
        args += ["-vn"]

    af = _build_audio_filters(opts)
    has_audio = not opt_bool(opts, "mute", False) and not opt_bool(opts, "no_audio", False)
    if af and has_audio:
        args += ["-af", af]

    # ---- 视频编码 ----
    if not audio_only:
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
            if pix and dst_fmt in ("mp4", "mov", "webm", "flv", "ts", "3gp", "mkv", "m3u8"):
                args += ["-pix_fmt", pix]
        else:
            args += ["-c:v", "copy"]

    # ---- 音频编码 ----
    if has_audio:
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

    # ---- 容器特殊项 ----
    if dst_fmt == "m3u8":
        hls_time = opt_int(opts, "hls_time", 6)
        seg = dst.with_suffix("")
        args += ["-f", "hls", "-hls_time", str(hls_time), "-hls_list_size", "0",
                 "-hls_segment_filename", f"{seg}_%03d.ts"]
    elif dst_fmt == "mp4" and opt_bool(opts, "faststart", True):
        args += ["-movflags", "+faststart"]

    args += [str(dst)]
    return args


def _probe_duration(src: Path, ffprobe: str) -> float:
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        return float(proc.stdout.decode("utf-8", "replace").strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def _run_ffmpeg(args: list[str], *, duration: float, opts) -> None:
    """执行 ffmpeg，解析 -progress 输出实时上报进度。"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    t = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                report_progress(opts, min(1.0, t / duration))
            elif line.startswith("progress=") and line.endswith("end"):
                report_progress(opts, 1.0)
    finally:
        stderr = proc.stderr.read() if proc.stderr else b""
        proc.wait()
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip().splitlines()
        detail = "\n    ".join(tail[-6:]) if tail else "(无 stderr 输出)"
        raise ConversionError(f"ffmpeg 退出码 {proc.returncode}：\n    {detail}")


def convert_media(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """音视频转换主入口（视频/音频/图片 → 视频/音频/GIF/HLS）。"""
    ffmpeg = _ffmpeg()
    args = build_media_args(src, dst, opts, dst_fmt, ffmpeg=ffmpeg)

    duration = 0.0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        duration = _probe_duration(src, ffprobe)
        if opts is not None and "start" in opts:
            start_s = _seconds(opt_str(opts, "start", "")) or 0.0
            end_s = _seconds(opt_str(opts, "end", "") or opt_str(opts, "to", ""))
            if end_s is not None:
                duration = max(0.1, end_s - start_s)
            elif duration > 0:
                duration = max(0.1, duration - start_s)

    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(args, duration=duration, opts=opts)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("ffmpeg 没有产出有效文件（检查流是否存在，例如该视频可能不含音轨）")
    return dst


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
