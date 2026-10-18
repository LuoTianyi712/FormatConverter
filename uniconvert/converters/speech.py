"""TTS：文字 → 语音（Windows 系统语音引擎 SAPI，零额外依赖）。

对标格式工厂的 "Text → WAV"：调用系统自带 ``System.Speech``
把文本合成为 WAV；装了 FFmpeg 还能顺手转 MP3/M4A。

```bash
uniconvert tts "你好，世界" -o 你好.wav
uniconvert tts --file 小说.txt -o 小说.mp3 -o voice=Microsoft Huihui -o rate=1
uniconvert convert 文稿.txt 文稿.wav        # 走转换图（txt→wav 边）
```

注意：音色/中文发音取决于系统已安装的语音包
（设置 → 时间和语言 → 语音）；本命令仅支持 Windows。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from ..core.registry import REGISTRY
from ..errors import ConversionError, DependencyMissingError
from ._util import opt_int, opt_str, read_text, run_tool

__all__ = ["tts"]

#: 单次合成字符上限（避免卡死/爆内存；长文建议分段）
MAX_CHARS = 20000

_PS_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$TextFile,
    [Parameter(Mandatory=$true)][string]$OutFile,
    [string]$Voice = "",
    [int]$Rate = 0,
    [int]$Volume = 100
)
Add-Type -AssemblyName System.Speech
$sp = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { try { $sp.SelectVoice($Voice) } catch { } }
$sp.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
$sp.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))
$text = Get-Content -LiteralPath $TextFile -Raw -Encoding UTF8
$sp.SetOutputToWaveFile($OutFile)
$sp.Speak($text)
$sp.Dispose()
"""


def _powershell() -> str:
    if sys.platform != "win32":
        raise DependencyMissingError(
            "Windows 系统语音",
            why="TTS 使用 Windows 自带的 System.Speech 引擎",
            install="本命令仅支持 Windows；其他平台可用 `pip install edge-tts` 等方案转换",
        )
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe:
        return exe
    raise DependencyMissingError(
        "powershell",
        why="TTS 需要 PowerShell 调用系统语音引擎",
        install="Windows 自带 powershell.exe；如缺失请检查系统环境变量 PATH",
    )


def synthesize_wav(text: str, dst: Path, opts) -> Path:
    """调系统 SAPI 合成 WAV。"""
    powershell = _powershell()
    if not text.strip():
        raise ConversionError("没有可合成的文本")
    if len(text) > MAX_CHARS:
        raise ConversionError(
            f"文本超过 {MAX_CHARS} 字上限（{len(text)} 字），请分段合成"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uniconvert-tts-") as tmp:
        tmp = Path(tmp)
        script = tmp / "tts.ps1"
        script.write_text(_PS_SCRIPT, encoding="ascii")
        text_file = tmp / "text.txt"
        text_file.write_text(text, encoding="utf-8")
        args = [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-TextFile", str(text_file),
            "-OutFile", str(dst),
            "-Voice", opt_str(opts, "voice", ""),
            "-Rate", str(opt_int(opts, "rate", 0)),
            "-Volume", str(opt_int(opts, "volume", 100)),
        ]
        run_tool(args, timeout=600)
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("语音合成没有产出文件（系统可能缺少语音包）")
    return dst


def tts(text: str = "", dst: str | Path = "speech.wav", *,
        file: str | Path | None = None, options=None, **opts) -> Path:
    """文字 → 音频。``text`` 或 ``file``（文本文件）二选一。

    目标是 WAV 时直接合成；目标是 mp3/m4a 等则先合成 WAV 再经 FFmpeg 转换。
    """
    from ..core.job import Options

    options = Options.from_any(options)
    options.update(opts)
    if file is not None:
        text = read_text(Path(file), options)
    dst = Path(dst)
    suffix = dst.suffix.lower().lstrip(".")
    if suffix == "wav":
        return synthesize_wav(text, dst, options)

    with tempfile.TemporaryDirectory(prefix="uniconvert-tts-") as tmp:
        wav = Path(tmp) / "speech.wav"
        synthesize_wav(text, wav, options)
        from .media import convert_media

        return convert_media(wav, dst, options, suffix or "mp3")


def txt_to_wav(src: Path, dst: Path, opts) -> Path:
    """转换图边：txt → wav（系统 TTS）。"""
    text = read_text(src, opts)
    return synthesize_wav(text, dst, opts)


def register_speech_converters() -> int:
    REGISTRY.add("txt", "wav", txt_to_wav, deps=("exe:powershell",),
                 group="speech", weight=2.0, quality=0.6,
                 note="系统 TTS 朗读（Windows）")
    return 1
