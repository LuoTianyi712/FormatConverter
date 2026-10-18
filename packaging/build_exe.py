"""一键打包 UniConvert 为独立 exe（PyInstaller）。

产物（默认输出到仓库根目录 ``dist/``）：

* ``UniConvert.exe``    —— 图形界面（双击即用，无控制台窗口）
* ``uniconvert-cli.exe`` —— 命令行（用法与 ``python -m uniconvert`` 一致）
  > Windows 文件系统大小写不敏感，两个 exe 名字必须区分，
  > 否则 ``UniConvert.exe`` 与 ``uniconvert.exe`` 会互相覆盖。

用法::

    python packaging/build_exe.py                 # 两个都打
    python packaging/build_exe.py --gui-only      # 只打 GUI
    python packaging/build_exe.py --cli-only      # 只打 CLI
    python packaging/build_exe.py --onedir        # 目录模式（启动更快，便于排查）

关于可选依赖的说明
------------------
PyInstaller 只打包**构建机器上已安装**的 Python 后端：

* 已装的（如 PyYAML / zstandard / brotli / Flask）自动内嵌进 exe；
* 未装的（如 Pillow / pypdf）不会被打进去——想要,先在构建机上
  ``pip install uniconvert[all]`` 再重新打包；
* FFmpeg / 7z / LibreOffice 等**外部程序**永远不进 exe，
  运行时会从 PATH 探测（用户装了就能用，uniconvert doctor 可体检）。
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

#: 运行时按需动态 import 的可选后端 —— 必须显式声明为 hidden-import，
#: 否则 PyInstaller 静态分析扫不到。
CANDIDATE_MODULES = [
    # 核心增强
    "yaml", "zstandard", "brotli",
    # Web 前端
    "flask",
    # 图片 / PDF / 文档
    "PIL", "pypdf", "pypdfium2", "openpyxl",
    "markdown", "markdownify", "bs4", "cairosvg",
    # 字体 / 归档
    "fontTools", "py7zr", "rarfile",
    # GUI 拖拽（可选）
    "tkinterdnd2",
]

#: 明确排除，减小体积
EXCLUDES = ["pytest", "numpy", "pandas", "matplotlib", "IPython"]


def _hidden_imports() -> list[str]:
    found: list[str] = []
    for name in CANDIDATE_MODULES:
        try:
            if importlib.util.find_spec(name) is not None:
                found.append(name)
        except (ImportError, ValueError):
            continue
    return found


def build(
    *,
    gui: bool = True,
    cli: bool = True,
    onedir: bool = False,
    clean: bool = True,
) -> int:
    hidden = _hidden_imports()
    mode = "--onedir" if onedir else "--onefile"
    common = [
        sys.executable, "-m", "PyInstaller",
        mode, "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build" / "pyinstaller"),
        "--paths", str(ROOT),
        *sum((["--hidden-import", m] for m in hidden), []),
        *sum((["--exclude-module", m] for m in EXCLUDES), []),
    ]
    if clean:
        common.append("--clean")

    print(f"发现可选后端：{', '.join(hidden) if hidden else '（无）'}")

    jobs: list[tuple[str, list[str]]] = []
    if gui:
        jobs.append(("UniConvert(GUI)", [
            *common, "--windowed", "--name", "UniConvert",
            str(PACKAGING / "gui_launcher.py"),
        ]))
    if cli:
        jobs.append(("uniconvert-cli(CLI)", [
            *common, "--console", "--name", "uniconvert-cli",
            str(PACKAGING / "cli_launcher.py"),
        ]))

    for label, cmd in jobs:
        print(f"\n=== 打包 {label} ===")
        proc = subprocess.run(cmd, cwd=str(ROOT))
        if proc.returncode != 0:
            print(f"✗ {label} 打包失败（退出码 {proc.returncode}）", file=sys.stderr)
            return proc.returncode
        print(f"✓ {label} 完成")

    print("\n产物：")
    for exe in sorted((ROOT / "dist").glob("*.exe")) + sorted((ROOT / "dist").glob("*/")):
        print(f"  {exe}")
    if not onedir:
        for exe in sorted((ROOT / "dist").glob("*.exe")):
            size = exe.stat().st_size / (1024 * 1024)
            print(f"  {exe.name}: {size:.1f} MB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 UniConvert 独立 exe")
    parser.add_argument("--gui-only", action="store_true", help="只打图形界面版")
    parser.add_argument("--cli-only", action="store_true", help="只打命令行版")
    parser.add_argument("--onedir", action="store_true",
                        help="目录模式（启动更快，适合内部排查）")
    parser.add_argument("--no-clean", action="store_true", help="复用上次的构建缓存")
    args = parser.parse_args()

    if shutil.which("pyinstaller") is None:  # 用 -m 调用也行，这里只做用户提示
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("缺少 PyInstaller：pip install pyinstaller", file=sys.stderr)
            return 2

    gui = not args.cli_only
    cli = not args.gui_only
    return build(gui=gui, cli=cli, onedir=args.onedir, clean=not args.no_clean)


if __name__ == "__main__":
    raise SystemExit(main())
