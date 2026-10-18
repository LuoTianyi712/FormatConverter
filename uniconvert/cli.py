"""UniConvert 命令行界面。

```text
uniconvert convert a.json a.yaml           # 单文件转换
uniconvert convert a.docx -t pdf           # 原地换格式
uniconvert batch *.png -t webp -o out/     # 批量转换（并发）
uniconvert route docx epub                 # 查看转换路径
uniconvert formats --category image        # 浏览支持的格式
uniconvert doctor                          # 检查可选依赖
```

命令风格：动词在前，中文帮助；退出码 0 成功 / 1 失败 / 2 用法错误。
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import sys
from pathlib import Path

from . import __version__
from .api import (
    can_convert,
    convert_batch,
    describe,
    extract,
    image_collage,
    image_compress_to_size,
    image_watermark,
    list_formats,
    options_for,
    pack,
    parse_size_bytes,
    pdf_compress,
    pdf_decrypt,
    pdf_encrypt,
    pdf_extract_images,
    pdf_merge,
    pdf_rotate,
    pdf_split,
    route,
    route_summary,
    routes_from,
)
from .core.deps import has_dep, install_hint, known_optional
from .core.formats import CATEGORY_LABELS
from .core.job import Options, Status
from .errors import UniconvertError
from .plugins import load_plugins
from .watch import Watcher, watch

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _fix_console() -> None:
    """Windows 控制台切到 UTF-8，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _human(n: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} TB"


def _fail(message: str, code: int = EXIT_FAIL) -> int:
    print(f"✗ {message}", file=sys.stderr)
    return code


def _load_plugins_once(args) -> None:
    if getattr(args, "no_plugins", False):
        return
    infos = load_plugins()
    failed = [i for i in infos if not i.ok]
    for info in failed:
        print(f"⚠ 插件加载失败：{info}", file=sys.stderr)


def _parse_options(items) -> Options:
    return Options.parse(items or [])


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def cmd_convert(args) -> int:
    _load_plugins_once(args)
    srcs = _expand_inputs([args.src])
    if not srcs:
        return _fail("没有找到要转换的文件")
    if len(srcs) > 1 and args.dst:
        return _fail(f"{args.src!r} 匹配到多个文件，请用 batch 命令或 --out 指定输出目录")

    outdir = Path(args.out) if args.out else None
    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)

    if len(srcs) > 1:
        jobs = [(str(s), outdir) for s in srcs]
        return _run_jobs(args, jobs, workers=args.jobs)

    src = srcs[0]
    if args.dst:
        dst = args.dst
    elif outdir is not None:
        dst = outdir
    else:
        dst = None
    return _run_jobs(args, [(str(src), dst)], workers=1)


def cmd_batch(args) -> int:
    _load_plugins_once(args)
    srcs = _expand_inputs(args.srcs)
    if not srcs:
        return _fail("没有找到要转换的文件")
    outdir = Path(args.out) if args.out else None
    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(str(s), outdir) for s in srcs]
    return _run_jobs(args, jobs, workers=args.jobs)


def _run_jobs(args, jobs: list, *, workers: int) -> int:
    quiet = args.quiet
    verbose = args.verbose

    def on_progress(event):
        if quiet:
            return
        if verbose and event.stage in ("step", "plan") and event.message:
            print(f"  · {event.message}")
        elif event.stage == "done" and event.result:
            print(f"  ✓ {event.result.summary()}")
        elif event.stage in ("skip", "dry-run") and event.result:
            print(f"  - {event.result.summary()}")

    results = convert_batch(
        [{"src": s, "dst": d, "to": args.to} for s, d in jobs],
        workers=workers if len(jobs) > 1 else 1,
        on_exists=args.on_exists,
        dry_run=args.dry_run,
        retry=args.retry,
        options=_parse_options(args.option),
        on_progress=on_progress,
    )

    failed = [r for r in results if r.status is Status.FAILED]
    if not quiet:
        print()
        for r in results:
            print(r.summary())
        ok = len(results) - len(failed)
        print(f"\n完成 {ok}/{len(results)} 个任务"
              + (f"，失败 {len(failed)}" if failed else ""))
    report = getattr(args, "report", "")
    if report:
        import json as _json

        payload = _json.dumps([r.as_dict() for r in results],
                              ensure_ascii=False, indent=2)
        Path(report).write_text(payload, encoding="utf-8")
        print(f"报告已写入：{report}")
    return EXIT_FAIL if failed else EXIT_OK


def _expand_inputs(patterns) -> list[Path]:
    out: list[Path] = []
    for p in patterns:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(f for f in path.rglob("*") if f.is_file()))
        elif any(ch in p for ch in "*?["):
            out.extend(sorted(Path(f) for f in _glob.glob(p, recursive=True)
                              if Path(f).is_file()))
        elif path.exists():
            out.append(path)
        else:
            print(f"⚠ 跳过不存在的路径：{p}", file=sys.stderr)
    # 去重保序
    seen = set()
    unique = []
    for f in out:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(f)
    return unique


def cmd_formats(args) -> int:
    formats = list_formats(args.category)
    if args.json:
        print(json.dumps(formats, ensure_ascii=False, indent=2))
        return EXIT_OK
    by_cat: dict[str, list[dict]] = {}
    for f in formats:
        by_cat.setdefault(f["category"], []).append(f)
    for cat, items in by_cat.items():
        label = CATEGORY_LABELS.get(cat, cat)
        print(f"\n== {label}（{cat}）==")
        for f in items:
            exts = "/".join(f["exts"][:4])
            flags = []
            if f["lossless"]:
                flags.append("无损")
            if f["text"]:
                flags.append("文本")
            flag_str = f"　[{','.join(flags)}]" if flags else ""
            print(f"  {f['name']:<10} .{exts:<22} {f['desc']}{flag_str}")
    print(f"\n共 {len(formats)} 种格式。用 `uniconvert route <源> <目标>` 查看转换路径。")
    return EXIT_OK


def cmd_route(args) -> int:
    _load_plugins_once(args)
    try:
        r = route(args.src, args.dst, available_only=not args.ignore_deps)
    except UniconvertError as exc:
        return _fail(str(exc))

    if r is None:
        print(f"✗ {args.src} -> {args.dst} 没有可用路径")
        if not args.ignore_deps:
            r2 = route(args.src, args.dst, available_only=False)
            if r2 is not None:
                print("  （忽略依赖时存在路径，可能是缺少可选项依赖；")
                print("    查看缺失：uniconvert route --ignore-deps）")
        return EXIT_FAIL

    for line in r.describe():
        print(line)
    if args.missing:
        from .core.graph import missing_for

        try:
            miss = missing_for(args.src, args.dst)
        except UniconvertError:
            miss = []
        if miss:
            print("\n缺失依赖：")
            for d in miss:
                print(f"  - {d}：{install_hint(d)}")
    return EXIT_OK


def cmd_reach(args) -> int:
    _load_plugins_once(args)
    try:
        targets = routes_from(args.src)
    except UniconvertError as exc:
        return _fail(str(exc))
    if not targets:
        print("没有可达的目标格式")
        return EXIT_OK
    print(f"从 {args.src} 出发可达 {len(targets)} 种格式：")
    for name, r in targets.items():
        steps = "直连" if r.direct else f"{len(r)} 步"
        print(f"  {name:<12} {steps:<6} {r}")
    return EXIT_OK


def cmd_can(args) -> int:
    _load_plugins_once(args)
    ok = can_convert(args.src, args.dst)
    print("可以转换" if ok else "不能转换")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_info(args) -> int:
    for f in _expand_inputs(args.files):
        info = describe(f)
        print(f"\n== {info.get('path')} ==")
        if "error" in info:
            print(f"  ✗ {info['error']}")
            continue
        print(f"  大小        : {info['size_human']} ({info['size']:,} 字节)")
        print(f"  扩展名格式  : {info['ext_format']}")
        print(f"  嗅探格式    : {info['sniffed_format']}")
        print(f"  大类        : {info['category']}")
        print(f"  文本        : {'是' if info.get('textual') else '否'}")
        if info.get("mismatch"):
            print("  ⚠ 扩展名与实际内容不一致（以嗅探结果为准）")
    return EXIT_OK


def cmd_options(args) -> int:
    info = options_for(args.format)
    print(f"格式：{info['format']}（{info['category']}）")
    declared = info["declared"]
    from_conv = info["from_converters"]
    if declared:
        print(f"  格式声明的选项：{', '.join(declared)}")
    if from_conv:
        print(f"  转换器声明的选项：{', '.join(from_conv)}")
    if not declared and not from_conv:
        print("  （无专项选项）")
    print("\n通用选项：encoding（读写编码）、newline（lf/crlf）、to_encoding")
    return EXIT_OK


def cmd_stats(args) -> int:
    _load_plugins_once(args)
    s = route_summary()
    print("转换图统计：")
    print(f"  节点（格式）    : {s['nodes']}（当前可用 {s['nodes_available']}）")
    print(f"  边（转换器）    : {s['edges']}（当前可用 {s['edges_available']}）")
    from .converters import registered_counts

    print("\n各转换器组：")
    for g, n in sorted(registered_counts().items()):
        print(f"  {g:<12} {n} 条边")
    if args.json:
        print(json.dumps({"graph": s, "groups": registered_counts()},
                         ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_doctor(args) -> int:
    checks = known_optional()
    rows = []
    for name, hint in sorted(checks.items()):
        ok = has_dep(name)
        rows.append((name, ok, hint))
    if args.json:
        print(json.dumps([{"name": n, "ok": ok, "hint": h} for n, ok, h in rows],
                         ensure_ascii=False, indent=2))
        return EXIT_OK
    print("可选依赖体检（✗ 不影响零依赖功能，只影响对应能力）：\n")
    for name, ok, hint in rows:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:<14} {hint if not ok else ''}")
    missing = [n for n, ok, _ in rows if not ok]
    print(f"\n{len(rows) - len(missing)}/{len(rows)} 已就绪。"
          f"缺失：{', '.join(missing) if missing else '无'}")
    return EXIT_OK


def cmd_extract(args) -> int:
    _load_plugins_once(args)
    try:
        files = extract(args.archive, args.outdir, options=_parse_options(args.option))
    except UniconvertError as exc:
        return _fail(str(exc))
    print(f"解压完成，共 {len(files)} 个文件 -> {args.outdir}")
    for f in files[:50]:
        print(f"  {f}")
    if len(files) > 50:
        print(f"  … 其余 {len(files) - 50} 个省略")
    return EXIT_OK


def cmd_pdf(args) -> int:
    """pdf 子命令组：合并/拆分/旋转/加密/解密/抽图/压缩。"""
    action = args.pdf_action
    try:
        if action == "merge":
            out = pdf_merge(args.files, args.output, password=args.password)
            print(f"合并完成：{out}（{len(args.files)} 个文件）")
        elif action == "split":
            files = pdf_split(args.file, args.out, every=args.every,
                              ranges=args.pages, password=args.password)
            print(f"拆分完成：{len(files)} 个文件 -> {args.out}")
            for f in files:
                print(f"  {f.name}")
        elif action == "rotate":
            out = pdf_rotate(args.file, args.output, angle=args.angle,
                             pages=args.pages, password=args.password)
            print(f"旋转完成：{out}")
        elif action == "encrypt":
            out = pdf_encrypt(args.file, args.output, password=args.password,
                              owner_password=args.owner_password,
                              algorithm=args.algorithm,
                              open_password=args.open_password)
            print(f"加密完成：{out}（算法 {args.algorithm}）")
        elif action == "decrypt":
            out = pdf_decrypt(args.file, args.output, password=args.password)
            print(f"解密完成：{out}")
        elif action == "images":
            files = pdf_extract_images(args.file, args.out,
                                       min_size=args.min_size,
                                       password=args.password)
            print(f"提取完成：{len(files)} 张图片 -> {args.out}")
            for f in files[:30]:
                print(f"  {f.name}")
            if len(files) > 30:
                print(f"  … 其余 {len(files) - 30} 张省略")
        elif action == "compress":
            before = Path(args.file).stat().st_size
            out = pdf_compress(args.file, args.output, dpi=args.dpi,
                               quality=args.quality, grayscale=args.grayscale)
            after = out.stat().st_size
            ratio = (1 - after / before) * 100 if before else 0
            print(f"压缩完成：{out}  {_human(before)} -> {_human(after)}"
                  f"（减少 {ratio:.0f}%）")
        else:  # pragma: no cover
            return _fail(f"未知 pdf 操作：{action}", EXIT_USAGE)
    except OSError as exc:
        return _fail(str(exc))
    except UniconvertError as exc:
        return _fail(str(exc))
    return EXIT_OK


def cmd_image(args) -> int:
    """image 子命令组：水印/拼接/压到指定大小。"""
    action = args.image_action
    opts = _parse_options(getattr(args, "option", []))
    try:
        if action == "watermark":
            for key in ("text", "image", "pos", "color", "stroke_color"):
                value = getattr(args, key.replace("-", "_"), "")
                if value:
                    opts[key] = value
            for key in ("opacity", "size", "margin", "gap", "quality"):
                value = getattr(args, key, None)
                if value is not None:
                    opts[key] = value
            if args.scale is not None:
                opts["scale"] = args.scale
            if args.rotate is not None:
                opts["rotate"] = args.rotate
            if args.stroke is not None:
                opts["stroke"] = args.stroke
            if args.tile:
                opts["tile"] = True
            out = image_watermark(Path(args.src), Path(args.dst), opts)
            print(f"水印完成：{out}")
        elif action == "collage":
            for key in ("mode", "bg"):
                value = getattr(args, key, "")
                if value:
                    opts[key] = value
            for key in ("cols", "gap", "width", "cell_width", "cell_height", "quality"):
                value = getattr(args, key, None)
                if value is not None:
                    opts[key] = value
            out = image_collage(args.files, args.output, opts)
            print(f"拼接完成：{out}（{len(args.files)} 张）")
        elif action == "compress":
            opts["target"] = args.target
            if args.min_quality is not None:
                opts["min_quality"] = args.min_quality
            if args.max_quality is not None:
                opts["max_quality"] = args.max_quality
            src = Path(args.src)
            if args.dst:
                dst = Path(args.dst)
            else:
                suffix = src.suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".webp"):
                    suffix = ".jpg"
                dst = src.with_name(f"{src.stem}-compressed{suffix}")
            before = src.stat().st_size
            out = image_compress_to_size(src, dst, opts)
            after = out.stat().st_size
            target = parse_size_bytes(args.target)
            status = "✓ 达标" if after <= target else "⚠ 未达标"
            print(f"压缩完成：{out}  {_human(before)} -> {_human(after)}"
                  f"（目标 {args.target}）{status}")
        else:  # pragma: no cover
            return _fail(f"未知 image 操作：{action}", EXIT_USAGE)
    except OSError as exc:
        return _fail(str(exc))
    except UniconvertError as exc:
        return _fail(str(exc))
    return EXIT_OK


def cmd_watch(args) -> int:
    _load_plugins_once(args)
    only = tuple(e.strip() for e in (args.only or "").split(",") if e.strip())
    try:
        watcher = Watcher(
            args.directory,
            target=args.to,
            outdir=args.out or None,
            only=only,
            options=_parse_options(args.option),
            initial=args.initial,
            engine_kwargs={"on_exists": args.on_exists, "retry": args.retry,
                           "workers": args.jobs},
        )
    except UniconvertError as exc:
        return _fail(str(exc))

    def show(result) -> None:
        print(result.summary())

    if args.once:
        results = watcher.scan_once()
        if not results:
            print("扫描完成：没有发现需要转换的新文件。")
        return EXIT_FAIL if any(r.status is Status.FAILED for r in results) else EXIT_OK

    print(f"开始监视：{args.directory}")
    print(f"  目标格式：{watcher.target}　输出：{args.out or '（与源文件同目录）'}")
    print(f"  间隔 {args.interval}s　Ctrl+C 停止")
    try:
        watch(watcher, interval=args.interval, on_result=show)
    except KeyboardInterrupt:
        print("\n监控已停止。")
    return EXIT_OK


def cmd_pack(args) -> int:
    _load_plugins_once(args)
    files = _expand_inputs(args.files)
    if not files:
        return _fail("没有找到要打包的文件")
    base = Path(args.base) if args.base else None
    try:
        out = pack(files, args.output, options=_parse_options(args.option), base=base)
    except UniconvertError as exc:
        return _fail(str(exc))
    print(f"打包完成：{out}（{len(files)} 个文件，{_human(out.stat().st_size)}）")
    return EXIT_OK


def cmd_gui(args) -> int:
    try:
        from .gui import launch
    except ImportError as exc:
        return _fail(f"GUI 需要 tkinter：{exc}")
    launch()
    return EXIT_OK


def cmd_web(args) -> int:
    try:
        from .web import launch
    except ImportError as exc:
        return _fail(f"Web 界面需要 Flask：pip install flask（{exc}）")
    launch(host=args.host, port=args.port, debug=args.debug)
    return EXIT_OK


def cmd_plugins(args) -> int:
    infos = load_plugins(verbose=False)
    if not infos:
        print("没有发现插件。")
        print("放置位置：~/.uniconvert/plugins/*.py 或设置 UNICONVERT_PLUGIN_PATH")
        return EXIT_OK
    print(f"发现 {len(infos)} 个插件：")
    for info in infos:
        mark = "✓" if info.ok else "✗"
        print(f"  {mark} {info}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# 解析器
# --------------------------------------------------------------------------- #
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    p.add_argument("-v", "--verbose", action="store_true", help="显示每一步细节")
    p.add_argument("--no-plugins", action="store_true", help="不加载用户插件")


def _add_convert_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("src", help="源文件（支持 glob；多个文件用 batch）")
    p.add_argument("dst", nargs="?", help="目标文件或目录（可省略，用 -t 换格式）")
    p.add_argument("-t", "--to", default="", help="目标格式名（如 pdf、webp、yaml）")
    p.add_argument("--out", default="", help="输出目录（配合 -t 使用）")
    p.add_argument("-o", "--option", action="append", metavar="K=V",
                   help="转换选项，可多次，如 -o crf=18 -o quality=95")
    p.add_argument("--on-exists", choices=("rename", "skip", "overwrite", "error"),
                   default="rename", help="目标存在时的策略（默认 rename）")
    p.add_argument("--retry", type=int, default=0, help="失败重试次数")
    p.add_argument("-j", "--jobs", type=int, default=4, help="批量并发数（默认 4）")
    p.add_argument("--dry-run", action="store_true", help="只规划不执行")
    p.add_argument("--report", default="", metavar="FILE",
                   help="把逐任务结果写成 JSON 报告（适合脚本/流水线）")
    _add_common(p)


def _add_batch_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("srcs", nargs="+", metavar="源文件", help="源文件、glob 或目录")
    p.add_argument("-t", "--to", default="", help="目标格式名（批量时必需）")
    p.add_argument("--out", default="", help="输出目录（缺省输出到各源文件旁边）")
    p.add_argument("-o", "--option", action="append", metavar="K=V",
                   help="转换选项，可多次")
    p.add_argument("--on-exists", choices=("rename", "skip", "overwrite", "error"),
                   default="rename", help="目标存在时的策略（默认 rename）")
    p.add_argument("--retry", type=int, default=0, help="失败重试次数")
    p.add_argument("-j", "--jobs", type=int, default=4, help="并发数（默认 4）")
    p.add_argument("--dry-run", action="store_true", help="只规划不执行")
    p.add_argument("--report", default="", metavar="FILE",
                   help="把逐任务结果写成 JSON 报告（适合脚本/流水线）")
    _add_common(p)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uniconvert",
        description="UniConvert —— 万能格式转换器（零依赖内核 + 可选增强）",
        epilog="示例：uniconvert convert a.docx -t pdf　|　uniconvert batch *.png -t webp --out out/",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="命令")

    p = sub.add_parser("convert", help="转换文件（单个或多个）")
    _add_convert_args(p)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("batch", help="批量转换（glob/目录，多并发）")
    _add_batch_args(p)
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("formats", help="列出支持的格式")
    p.add_argument("--category", choices=sorted(CATEGORY_LABELS), help="只看某大类")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.set_defaults(func=cmd_formats)

    p = sub.add_parser("route", help="查看两个格式之间的转换路径")
    p.add_argument("src", help="源格式名或文件")
    p.add_argument("dst", help="目标格式名或文件")
    p.add_argument("--ignore-deps", action="store_true", help="忽略缺失的依赖")
    p.add_argument("--missing", action="store_true", help="列出缺失依赖及安装方式")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("reach", help="列出某格式可达的全部目标")
    p.add_argument("src", help="源格式名或文件")
    p.set_defaults(func=cmd_reach)

    p = sub.add_parser("can", help="判断能否转换（脚本用退出码）")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(func=cmd_can)

    p = sub.add_parser("info", help="查看文件格式体检报告")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("options", help="查看某格式的可用选项")
    p.add_argument("format")
    p.set_defaults(func=cmd_options)

    p = sub.add_parser("stats", help="转换图统计")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("doctor", help="检查可选依赖安装情况")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("extract", help="解压归档到目录")
    p.add_argument("archive")
    p.add_argument("-d", "--outdir", default=".", help="输出目录（默认当前目录）")
    p.add_argument("-o", "--option", action="append", metavar="K=V")
    p.set_defaults(func=cmd_extract)

    # ---- PDF 工具箱 ----
    pdf_parser = sub.add_parser("pdf", help="PDF 工具箱（合并/拆分/旋转/加密/抽图/压缩）")
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_action", metavar="操作")

    p = pdf_sub.add_parser("merge", help="合并多个 PDF")
    p.add_argument("files", nargs="+", metavar="PDF文件")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--password", default="", help="源文件打开密码（如有）")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("split", help="拆分 PDF（默认每页一个文件）")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    p.add_argument("--every", type=int, default=0, metavar="N", help="每 N 页一个文件")
    p.add_argument("--pages", default="", metavar="范围",
                   help="按页范围拆，如 1-3,5,7-9（每段一个文件）")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("rotate", help="旋转页面")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--angle", type=int, default=90, choices=(90, 180, 270))
    p.add_argument("--pages", default="", metavar="范围", help="只转这些页（默认全部）")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("encrypt", help="设置打开密码（加密）")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--password", required=True, help="要设置的打开密码")
    p.add_argument("--owner-password", default="")
    p.add_argument("--algorithm", default="AES-256",
                   choices=("AES-256", "AES-128", "RC4-128", "RC4-40"))
    p.add_argument("--open-password", default="", help="源文件已有密码")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("decrypt", help="去掉打开密码")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--password", default="", help="当前打开密码")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("images", help="提取内嵌图片")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("--out", default=".", help="输出目录")
    p.add_argument("--min-size", type=int, default=0, metavar="字节",
                   help="忽略小于该字节数的图片（滤掉小图标）")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_pdf)

    p = pdf_sub.add_parser("compress", help="压缩 PDF（重渲染，有损）")
    p.add_argument("file", metavar="PDF文件")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--dpi", type=int, default=120, help="渲染精度（默认 120，越小越省）")
    p.add_argument("--quality", type=int, default=75, help="JPEG 质量（默认 75）")
    p.add_argument("--grayscale", action="store_true", help="转灰度（省得更多）")
    p.set_defaults(func=cmd_pdf)

    # ---- 图片工具 ----
    img_parser = sub.add_parser("image", help="图片工具（水印/拼接/压到指定大小）")
    img_sub = img_parser.add_subparsers(dest="image_action", metavar="操作")

    p = img_sub.add_parser("watermark", help="加水印（文字或图片）")
    p.add_argument("src", metavar="输入图片")
    p.add_argument("dst", metavar="输出图片")
    p.add_argument("--text", default="", help="水印文字")
    p.add_argument("--image", default="", help="水印图片路径（与 --text 二选一）")
    p.add_argument("--pos", default="br", help="位置 tl/tc/tr/ml/mc/mr/bl/bc/br（默认右下）")
    p.add_argument("--opacity", type=int, default=None, help="透明度 0-100（默认 40）")
    p.add_argument("--size", type=int, default=None, help="字号像素（默认图宽 1/20）")
    p.add_argument("--scale", type=float, default=None, help="水印图占原图宽度百分比（默认 20）")
    p.add_argument("--tile", action="store_true", help="平铺重复水印")
    p.add_argument("--rotate", type=float, default=None, help="水印旋转角度")
    p.add_argument("--color", default="#FFFFFF", help="文字颜色（默认白）")
    p.add_argument("--stroke", type=int, default=None, help="描边宽度（0 无描边）")
    p.add_argument("--stroke-color", default="#000000")
    p.add_argument("--margin", type=int, default=None)
    p.add_argument("--gap", type=int, default=None, help="平铺间距")
    p.add_argument("--quality", type=int, default=None)
    p.set_defaults(func=cmd_image)

    p = img_sub.add_parser("collage", help="拼接多张图片（长图/网格）")
    p.add_argument("files", nargs="+", metavar="图片")
    p.add_argument("-o", "--output", required=True, help="输出图片")
    p.add_argument("--mode", default="grid", choices=("grid", "h", "v"),
                   help="grid 网格 / h 横长图 / v 竖长图")
    p.add_argument("--cols", type=int, default=None, help="网格列数")
    p.add_argument("--gap", type=int, default=None, help="间距像素（默认 8）")
    p.add_argument("--bg", default="white", help="背景色（默认白）")
    p.add_argument("--width", type=int, default=None, help="总宽度像素")
    p.add_argument("--cell-width", type=int, default=None)
    p.add_argument("--cell-height", type=int, default=None)
    p.add_argument("--quality", type=int, default=None)
    p.set_defaults(func=cmd_image)

    p = img_sub.add_parser("compress", help="压到指定大小（如 500KB）")
    p.add_argument("src", metavar="输入图片")
    p.add_argument("dst", nargs="?", metavar="输出图片",
                   help="缺省输出为 原名-compressed.jpg")
    p.add_argument("--target", required=True, metavar="大小", help="目标大小，如 500KB / 1.5MB")
    p.add_argument("--min-quality", type=int, default=None)
    p.add_argument("--max-quality", type=int, default=None)
    p.set_defaults(func=cmd_image)

    # ---- 目录监控 ----
    p = sub.add_parser("watch", help="监视目录，新文件自动转换")
    p.add_argument("directory", help="要监视的目录")
    p.add_argument("-t", "--to", required=True, help="目标格式（如 webp/pdf/yaml）")
    p.add_argument("--out", default="", help="输出目录（缺省与源文件同目录）")
    p.add_argument("--only", default="", metavar=".jpg,.png",
                   help="只处理这些扩展名（默认全部已知格式）")
    p.add_argument("--interval", type=float, default=2.0, help="扫描间隔秒（默认 2）")
    p.add_argument("--initial", action="store_true",
                   help="启动时也转换目录里已有的文件（默认只登记不转换）")
    p.add_argument("--once", action="store_true", help="只扫描一轮就退出")
    p.add_argument("--on-exists", choices=("rename", "skip", "overwrite", "error"),
                   default="overwrite", help="目标存在时策略（watch 默认 overwrite）")
    p.add_argument("--retry", type=int, default=0)
    p.add_argument("-j", "--jobs", type=int, default=2)
    p.add_argument("-o", "--option", action="append", metavar="K=V")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("pack", help="把文件打包为归档")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--output", required=True, help="输出归档路径（.zip/.tar/.7z）")
    p.add_argument("--base", default="", help="相对路径基准目录")
    p.add_argument("--option", action="append", metavar="K=V")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("gui", help="启动图形界面（tkinter）")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("web", help="启动网页界面（Flask）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("plugins", help="列出发现的插件")
    p.set_defaults(func=cmd_plugins)

    return parser


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except UniconvertError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
