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

import os
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
                 "3gp", "ogv", "m3u8", "vob", "rm", "swf", "dv", "mod", "wtv"]
AUDIO_FORMATS = ["mp3", "aac", "flac", "wav", "ogg", "opus", "wma", "aiff", "m4b",
                 "ac3", "dts", "au", "caf", "wv", "tta", "w64", "dsf", "m4r"]

#: 只读格式（能解码不能编码）——不作为转换目标
READ_ONLY = {"rm", "dsf"}

#: 各容器默认视频编码
_VIDEO_CODEC = {
    "mp4": "libx264", "mkv": "libx264", "avi": "mpeg4", "mov": "libx264",
    "webm": "libvpx-vp9", "flv": "libx264", "wmv": "wmv2", "mpg": "mpeg2video",
    "ts": "libx264", "3gp": "libx264", "ogv": "libtheora", "m3u8": "libx264",
    "vob": "mpeg2video", "swf": "flv", "dv": "dvvideo", "mod": "mpeg2video",
    "wtv": "mpeg2video",
}

#: 各容器默认音频编码
_AUDIO_CODEC = {
    "mp3": "libmp3lame", "aac": "aac", "flac": "flac", "wav": "pcm_s16le",
    "ogg": "libvorbis", "opus": "libopus", "wma": "wmav2", "aiff": "pcm_s16be",
    "m4b": "aac", "ac3": "ac3", "dts": "dca",
    "mp4": "aac", "mkv": "aac", "avi": "libmp3lame", "mov": "aac",
    "webm": "libopus", "flv": "aac", "wmv": "wmav2", "mpg": "mp2",
    "ts": "aac", "3gp": "aac", "ogv": "libvorbis", "m3u8": "aac",
    "vob": "ac3", "swf": "libmp3lame", "dv": "pcm_s16le", "mod": "mp2",
    "wtv": "ac3", "au": "pcm_s16be", "caf": "pcm_s16le", "wv": "wavpack",
    "tta": "tta", "w64": "pcm_s16le", "m4r": "aac",
}

AUDIO_TARGETS = set(AUDIO_FORMATS) - READ_ONLY

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


def _find_font_path() -> str | None:
    """给 drawtext 找一个可用的字体文件（优先中文字体）。"""
    from .image import _FONT_CANDIDATES

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _esc_filter_text(text: str) -> str:
    """ffmpeg 滤镜参数转义（drawtext 的经典坑）。"""
    return (text.replace("\\", "\\\\").replace("'", "\\'")
            .replace(":", "\\:").replace(",", "\\,").replace("%", "\\%"))


def _esc_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


_GRID_LCR = {"tl": "l", "tc": "c", "tr": "r", "ml": "l", "mc": "c", "mr": "r",
             "bl": "l", "bc": "c", "br": "r"}
_GRID_TMB = {"tl": "t", "tc": "t", "tr": "t", "ml": "m", "mc": "m", "mr": "m",
             "bl": "b", "bc": "b", "br": "b"}


def _normalize_pos(pos: str) -> str:
    return pos.lower() if pos.lower() in _GRID_LCR else "br"


def _drawtext_xy(pos: str, margin: int) -> tuple[str, str]:
    """drawtext 坐标（小写 w/h = 画面，text_w/text_h = 文字）。"""
    lcr, tmb = _GRID_LCR[pos], _GRID_TMB[pos]
    x = {"l": str(margin), "c": "(w-text_w)/2", "r": f"w-text_w-{margin}"}[lcr]
    y = {"t": str(margin), "m": "(h-text_h)/2", "b": f"h-text_h-{margin}"}[tmb]
    return x, y


def _overlay_xy(pos: str, margin: int) -> tuple[str, str]:
    """overlay 坐标（大写 W/H = 主画面，w/h = 水印图）。"""
    lcr, tmb = _GRID_LCR[pos], _GRID_TMB[pos]
    x = {"l": str(margin), "c": "(W-w)/2", "r": f"W-w-{margin}"}[lcr]
    y = {"t": str(margin), "m": "(H-h)/2", "b": f"H-h-{margin}"}[tmb]
    return x, y


def _build_text_watermark(opts) -> str:
    """文字水印 → drawtext 滤镜（视频水印的核心实现）。"""
    text = opt_str(opts, "wm_text", "") or opt_str(opts, "watermark_text", "")
    if not text:
        return ""
    size = opt_int(opts, "wm_size", 24)
    color = opt_str(opts, "wm_color", "white") or "white"
    opacity = max(1, min(100, opt_int(opts, "wm_opacity", 60)))
    margin = opt_int(opts, "wm_margin", 16)
    pos = _normalize_pos(opt_str(opts, "wm_pos", "br"))
    x, y = _drawtext_xy(pos, margin)

    parts = [f"drawtext=text='{_esc_filter_text(text)}'",
             f"fontsize={size}",
             f"fontcolor={color}@{opacity / 100:.2f}",
             f"x={x}", f"y={y}"]
    font = _find_font_path()
    if font:
        parts.insert(1, f"fontfile='{_esc_filter_path(font)}'")
    elif not all(ord(ch) < 128 for ch in text):
        raise ConversionError(
            "加水印的中文需要系统中的中文字体文件，但没找到"
            "（可用 wm_font=字体路径 显式指定）"
        )
    font_opt = opt_str(opts, "wm_font", "")
    if font_opt:
        # 用户显式指定优先
        parts = [p for p in parts if not p.startswith("fontfile=")]
        parts.insert(1, f"fontfile='{_esc_filter_path(font_opt)}'")
    return ":".join(parts)


def _build_video_filters(opts, *, to_gif: bool = False) -> str:
    """视频滤镜串：scale / fit / crop / rotate / 镜像 / speed / 字幕 / 淡入出 / GIF。"""
    filters: list[str] = []

    crop = opt_str(opts, "crop", "")
    if crop:
        expr = crop.replace(":", ":").replace("*", "x")
        filters.append(f"crop={expr}")

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

    mirror = opt_str(opts, "mirror", "").lower()
    if mirror in ("h", "horizontal", "水平"):
        filters.append("hflip")
    elif mirror in ("v", "vertical", "垂直"):
        filters.append("vflip")
    elif mirror in ("both", "hv", "180"):
        filters.append("hflip,vflip")

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
    else:
        wm = _build_text_watermark(opts)
        if wm:
            filters.append(wm)

    return ",".join(filters)


def _build_audio_filters(opts) -> str:
    """音频滤镜串：speed / volume / fade / 响度归一 / 降噪 / 变调 / 声道 / 回响。"""
    filters: list[str] = []

    speed = opt_float(opts, "speed", 1.0)
    if speed and abs(speed - 1.0) > 1e-6:
        filters.append(_atempo_chain(speed))

    pitch = opt_float(opts, "pitch", 0.0)
    if pitch:
        # 半音 → 频率比；asetrate 改采样率 + atempo 补偿回原速
        ratio = 2 ** (pitch / 12)
        sample_rate = opt_int(opts, "sample_rate", 44100)
        new_rate = int(sample_rate * ratio)
        filters.append(f"asetrate={new_rate},aresample={sample_rate},"
                       f"{_atempo_chain(1 / ratio)}")

    volume = opt_float(opts, "volume", 0.0)
    if volume:
        filters.append(f"volume={volume:g}dB")

    bass = opt_float(opts, "bass", 0.0)
    if bass:
        filters.append(f"bass=g={bass:g}")
    treble = opt_float(opts, "treble", 0.0)
    if treble:
        filters.append(f"treble=g={treble:g}")

    if opt_bool(opts, "loudnorm", False):
        target = opt_str(opts, "loudnorm_target", "-16") or "-16"
        filters.append(f"loudnorm=I={target}:TP=-1.5:LRA=11")

    if opt_bool(opts, "denoise", False):
        filters.append("afftdn")

    if opt_bool(opts, "karaoke", False):
        # 中置相位抵消（规范确认：pan 负值即可；仅立体声有效）
        filters.append("pan=stereo|c0=c0-c1|c1=c1-c0")

    channels = opt_str(opts, "channels", "").lower()
    if channels in ("mono", "1", "单声道"):
        filters.append("pan=mono|c0=0.5*c0+0.5*c1")
    elif channels in ("left", "左"):
        filters.append("pan=mono|c0=c0")
    elif channels in ("right", "右"):
        filters.append("pan=mono|c0=c1")

    if opt_bool(opts, "reverse", False):
        filters.append("areverse")

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
    if opt_bool(opts, "rewrap", False):
        from ..core.job import Options

        merged = Options(dict(opts.values))
        merged["vcodec"] = "copy"
        merged["acodec"] = "copy"
        opts = merged
    return _build_args_impl(src, dst, opts, dst_fmt, ffmpeg)


def _build_args_impl(src, dst, opts, dst_fmt, ffmpeg) -> list[str]:
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

    # 附加输入：replace_audio / mux_sub
    replace_audio = opt_str(opts, "replace_audio", "") or opt_str(opts, "audio_track", "")
    mux_sub = opt_str(opts, "mux_sub", "") or opt_str(opts, "embed_sub", "")
    if replace_audio:
        if not Path(replace_audio).is_file():
            raise ConversionError(f"替换音轨文件不存在：{replace_audio}")
        args += ["-i", replace_audio]
    if mux_sub:
        if not Path(mux_sub).is_file():
            raise ConversionError(f"字幕文件不存在：{mux_sub}")
        if dst_fmt not in ("mp4", "mov", "mkv"):
            raise ConversionError("封装字幕仅支持 mp4/mov/mkv 输出容器")
        args += ["-i", mux_sub]

    start = opt_str(opts, "start", "") or opt_str(opts, "ss", "")
    end = opt_str(opts, "end", "") or opt_str(opts, "to", "")
    if start:
        args += ["-ss", str(start)]
    if end:
        args += ["-to", str(end)]

    to_gif = dst_fmt == "gif"
    vf = _build_video_filters(opts, to_gif=to_gif)

    # 图片水印：需要第二输入 + filter_complex
    wm_image = opt_str(opts, "wm_image", "") or opt_str(opts, "watermark_image", "")
    use_wm_image = bool(wm_image) and not src_is_image and not to_gif
    if use_wm_image:
        if not Path(wm_image).is_file():
            raise ConversionError(f"水印图片不存在：{wm_image}")
        args += ["-i", wm_image]
        wm_scale = opt_float(opts, "wm_scale", 15.0)
        alpha = max(0.01, min(1.0, opt_float(opts, "wm_opacity", 60.0) / 100.0))
        margin = opt_int(opts, "wm_margin", 16)
        ox, oy = _overlay_xy(_normalize_pos(opt_str(opts, "wm_pos", "br")), margin)
        main_chain = f"[0:v]{vf},null[base]" if vf else "[0:v]null[base]"
        graph = (
            f"[1:v]format=rgba,colorchannelmixer=aa={alpha:.2f},"
            f"scale=iw*{wm_scale:g}/100:-1[wm];"
            f"{main_chain};"
            f"[base][wm]overlay={ox}:{oy}:format=auto[vout]"
        )
        args += ["-filter_complex", graph, "-map", "[vout]"]
    elif vf:
        args += ["-vf", vf]

    audio_only = (dst_fmt in AUDIO_TARGETS
                  or opt_bool(opts, "mute", False)
                  or opt_bool(opts, "no_video", False)
                  or opt_bool(opts, "vn", False))
    if audio_only:
        args += ["-vn"]

    af = _build_audio_filters(opts)
    has_audio = not opt_bool(opts, "mute", False) and not opt_bool(opts, "no_audio", False)
    if use_wm_image and has_audio:
        # filter_complex 模式下视频流已被显式 map，音频也要显式带上
        args += ["-map", "0:a?"]
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
    elif dst_fmt == "m4r":
        args += ["-f", "ipod"]
    elif dst_fmt == "mp4" and opt_bool(opts, "faststart", True):
        args += ["-movflags", "+faststart"]

    # 音轨替换 / 字幕封装：显式映射
    if replace_audio:
        args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest",
                 "-metadata:s:a:0", f"language={opt_str(opts, 'audio_lang', 'chi')}"]
    if mux_sub:
        sub_codec = "mov_text" if dst_fmt in ("mp4", "mov") else "srt"
        args += ["-map", "0", "-map", "1:0", "-c:s", sub_codec,
                 "-metadata:s:s:0", f"language={opt_str(opts, 'sub_lang', 'chi')}"]

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


def _kbps(text: str, default: int = 96) -> int:
    """``"96k"`` / ``"128000"`` / ``"192K"`` → kbps。"""
    text = (text or "").strip().lower()
    if not text:
        return default
    try:
        if text.endswith("k"):
            return int(float(text[:-1]))
        return int(float(text) / 1000)
    except ValueError:
        return default


def convert_media(src: Path, dst: Path, opts, dst_fmt: str) -> Path:
    """音视频转换主入口（视频/音频/图片 → 视频/音频/GIF/HLS/铃声容器）。"""
    ffmpeg = _ffmpeg()

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

    # ---- 目标大小模式（2-pass，微信 25MB 这类需求）----
    target = opt_str(opts, "target_size", "")
    if target:
        from .image import parse_size_bytes

        if duration <= 0:
            raise ConversionError("无法探测视频时长，不能按目标大小压缩")
        target_bytes = parse_size_bytes(target)
        audio_kb = _kbps(opt_str(opts, "audio_bitrate", "") or opt_str(opts, "ab", ""))
        total_kbps = target_bytes * 8 / 1000
        video_kbps = int(total_kbps / duration * 0.97) - audio_kb
        if video_kbps < 50:
            raise ConversionError(
                f"目标 {target} 对 {duration:.0f} 秒的视频太小"
                f"（视频码率仅 {video_kbps}kbps），请放宽目标或先裁剪时长"
            )
        from ..core.job import Options

        pass_opts = Options(dict(opts.values))
        pass_opts["bitrate"] = f"{video_kbps}k"
        pass_opts["audio_bitrate"] = f"{audio_kb}k"
        pass_opts.pop("crf", None)  # 二压用固定码率
        base = build_media_args(src, dst, pass_opts, dst_fmt, ffmpeg=ffmpeg)
        # 第一遍：分析（丢弃输出）
        pass1 = list(base)
        pass1.insert(-1, "-pass")
        pass1.insert(-1, "1")
        pass1.insert(-1, "-an")
        pass1.insert(-1, "-f")
        pass1.insert(-1, "null")
        pass1[-1] = os.devnull
        dst.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(pass1, duration=duration, opts=None)
        # 第二遍：正式输出
        pass2 = list(base)
        pass2.insert(-1, "-pass")
        pass2.insert(-1, "2")
        _run_ffmpeg(pass2, duration=duration, opts=opts)
        if not dst.exists() or dst.stat().st_size == 0:
            raise ConversionError("二压没有产出有效文件")
        return dst

    args = build_media_args(src, dst, opts, dst_fmt, ffmpeg=ffmpeg)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(args, duration=duration, opts=opts)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("ffmpeg 没有产出有效文件（检查流是否存在，例如该视频可能不含音轨）")
    return dst


def concat_media(files: list[str | Path], dst: str | Path, opts=None) -> Path:
    """把多个媒体文件无缝拼接（同编码走流拷贝，不同编码自动重编码）。"""
    import tempfile

    ffmpeg = _ffmpeg()
    paths = [Path(f) for f in files]
    if len(paths) < 2:
        raise ConversionError("拼接至少需要 2 个文件")
    for p in paths:
        if not p.is_file():
            raise ConversionError(f"文件不存在：{p}")
    dst = Path(dst)

    with tempfile.TemporaryDirectory(prefix="uniconvert-concat-") as tmp:
        listfile = Path(tmp) / "list.txt"
        lines = []
        for p in paths:
            escaped = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        listfile.write_text("\n".join(lines), encoding="utf-8")

        def build(codec_args: list[str]) -> list[str]:
            return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(listfile),
                    *codec_args, str(dst)]

        from ._util import run_tool

        try:
            run_tool(build(["-c", "copy"]), timeout=7200)
        except ConversionError:
            # 参数不一致的流拷贝会失败 → 回退到统一重编码
            run_tool(build(["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                            "-c:a", "aac", "-b:a", "192k"]), timeout=7200)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("拼接没有产出有效文件")
    return dst


def ringtone_media(src: Path, outdir: Path, opts) -> dict[str, Path]:
    """铃声制作：截取片段 + 淡入淡出，导出 iPhone 铃声(.m4r) 与 .mp3。"""
    ffmpeg = _ffmpeg()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    start = opt_str(opts, "start", "0") or "0"
    duration = opt_float(opts, "duration", 30.0)
    duration = min(duration, 40.0)  # iPhone 铃声上限 40 秒
    fade = opt_float(opts, "fade", 0.5)

    outputs: dict[str, Path] = {}
    specs = [("m4r", ["-c:a", "aac", "-b:a", "256k", "-f", "ipod"]),
             ("mp3", ["-c:a", "libmp3lame", "-q:a", "2"])]
    wanted = [s.strip().lower() for s in
              (opt_str(opts, "formats", "m4r,mp3") or "m4r,mp3").split(",") if s.strip()]
    for ext, codec_args in specs:
        if ext not in wanted:
            continue
        target = outdir / f"{src.stem}.{ext}"
        args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src), "-ss", start, "-t", str(duration),
                "-af", f"afade=t=in:st=0:d={fade:g},"
                       f"afade=t=out:st={max(0.0, duration - fade):g}:d={fade:g}",
                *codec_args, str(target)]
        run_tool(args, timeout=600)
        if target.exists() and target.stat().st_size > 0:
            outputs[ext] = target
    if not outputs:
        raise ConversionError("铃声没有产出有效文件")
    return outputs


def frame_sequence(src: Path, dst: Path, opts, fmt: str = "png") -> Path:
    """视频 → 图片序列（按 fps 抽帧，输出 名字-0001.png ...）。"""
    ffmpeg = _ffmpeg()
    fps = opt_float(opts, "fps", 1.0) or 1.0
    dst = Path(dst)
    if dst.is_dir():
        outdir = dst
        stem = src.stem
    else:
        outdir = dst.parent
        stem = dst.stem
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = outdir / f"{stem}-%04d.{fmt}"
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src)]
    start = opt_str(opts, "start", "")
    if start:
        args += ["-ss", start]
    scale = opt_str(opts, "resolution", "")
    vf = f"fps={fps:g}" + (f",scale={scale}" if scale else "")
    args += ["-vf", vf, "-q:v", str(opt_int(opts, "quality", 3)), str(pattern)]
    run_tool(args, timeout=7200)
    produced = sorted(outdir.glob(f"{stem}-*.{fmt}"))
    if not produced:
        raise ConversionError("抽帧没有产出任何图片")
    return produced[0]


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


def _demucs_cmd() -> list[str]:
    """人声分离后端命令（demucs 可执行文件或 python -m demucs）。"""
    exe = shutil.which("demucs")
    if exe:
        return [exe]
    try:
        import importlib.util

        if importlib.util.find_spec("demucs") is not None:
            return [sys.executable, "-m", "demucs"]
    except (ImportError, ValueError):
        pass
    raise ConversionError(
        "人声/伴奏分离需要 Demucs：pip install demucs（首次运行会自动下载模型）"
    )


def separate_audio(src: Path, outdir: Path, opts) -> dict[str, Path]:
    """人声/伴奏分离（Demucs 后端）。

    使用 ``--two-stems=vocals`` 模式，直接产出：

    * ``vocals.wav``   —— 人声
    * ``no_vocals.wav`` —— 伴奏

    选项：``model``（默认 htdemucs）、``format=mp3``（用 ffmpeg 把结果转成 mp3）。
    """
    base = _demucs_cmd()
    model = opt_str(opts, "model", "htdemucs") or "htdemucs"
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    run_tool(
        base + ["-n", model, "--two-stems", "vocals", "-o", str(outdir), str(src)],
        timeout=2 * 3600,
    )

    stem_dir = outdir / model / src.stem
    vocals = stem_dir / "vocals.wav"
    instrumental = stem_dir / "no_vocals.wav"
    if not vocals.exists() or not instrumental.exists():
        raise ConversionError(
            f"Demucs 没有产出预期文件（检查 {stem_dir}）——可能是模型下载失败或音频无法解码"
        )

    fmt = opt_str(opts, "format", "").lower()
    if fmt in ("mp3", "m4a", "flac", "ogg", "opus", "aac"):
        converted: dict[str, Path] = {}
        for label, wav in (("vocals", vocals), ("instrumental", instrumental)):
            target = wav.with_suffix("." + fmt)
            try:
                convert_media(wav, target, opts, fmt)
                converted[label] = target
            except ConversionError:
                converted[label] = wav  # 转码失败（如缺 ffmpeg）保留 wav
        return converted
    return {"vocals": vocals, "instrumental": instrumental}


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register_media_converters() -> int:
    count = 0
    AV = VIDEO_FORMATS + AUDIO_FORMATS

    for sf in AV:
        for df in AV:
            if sf == df or df in READ_ONLY:
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
