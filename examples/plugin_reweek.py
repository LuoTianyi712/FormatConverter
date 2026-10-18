"""示例插件：把 CSV 转成固定模板的 Markdown 周报。

把本文件复制到 ``~/.uniconvert/plugins/`` 即生效
（或设置环境变量 ``UNICONVERT_PLUGIN_PATH`` 指向本项目 examples 目录）：

    set UNICONVERT_PLUGIN_PATH=F:\FormatConverter\examples
    uniconvert route csv reweek
    uniconvert convert data.csv data.reweek
"""

from pathlib import Path

from uniconvert.core import register, guess_format


@register("csv", "reweek", group="example", note="CSV → 周报模板")
def csv_to_reweek(src: Path, dst: Path, opts) -> Path:
    import csv

    encoding = (opts.get("encoding") if hasattr(opts, "get") else None) or "utf-8-sig"
    title = (opts.get("title") if hasattr(opts, "get") else None) or src.stem

    with open(src, "r", encoding=encoding, newline="") as fh:
        rows = list(csv.DictReader(fh))

    lines = [f"# {title} 周报", ""]
    for i, row in enumerate(rows, 1):
        lines.append(f"## {i}. " + (row.get("name") or row.get("标题") or f"事项 {i}"))
        for key, value in row.items():
            if key in ("name", "标题") or not value:
                continue
            lines.append(f"- {key}：{value}")
        lines.append("")

    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst
