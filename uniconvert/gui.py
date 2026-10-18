"""图形界面（tkinter，零额外依赖）。

功能对齐市面桌面转换器的基础体验：

* 文件列表（名称/大小/识别格式/目标格式/状态）；
* 目标格式下拉（按大类分组）；
* 输出目录（默认与源文件同目录）；
* 选项输入（``key=value`` 每行一条）；
* 进度条 + 日志区；后台线程转换，界面不卡。

拖拽支持：装过 tkinterdnd2 时自动启用；没装则用「添加文件」按钮。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    HORIZONTAL,
    LEFT,
    RIGHT,
    TOP,
    X,
    Y,
    BooleanVar,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
    Text,
)

from .api import list_formats
from .core.detect import detect_format
from .core.engine import Engine, ProgressEvent
from .core.formats import guess_format
from .core.job import Job, Options, Status


class _Row:
    __slots__ = ("path", "fmt", "size", "status", "iid")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fmt = ""
        self.size = 0
        self.status = "待转换"
        self.iid = ""


class ConverterApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        root.title("UniConvert 万能格式转换器")
        root.geometry("980x640")

        self.rows: list[_Row] = []
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.engine: Engine | None = None
        self.running = False

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(side=TOP, fill=X)

        ttk.Button(toolbar, text="添加文件", command=self.add_files).pack(side=LEFT)
        ttk.Button(toolbar, text="添加文件夹", command=self.add_folder).pack(side=LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).pack(side=LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="清空", command=self.clear_all).pack(side=LEFT, padx=(6, 0))

        ttk.Label(toolbar, text="目标格式：").pack(side=LEFT, padx=(18, 2))
        self.target_var = StringVar(value="pdf")
        self.target_combo = ttk.Combobox(toolbar, textvariable=self.target_var, width=14)
        self.target_combo.pack(side=LEFT)
        self.target_combo["values"] = self._format_choices()

        ttk.Label(toolbar, text="输出目录：").pack(side=LEFT, padx=(18, 2))
        self.outdir_var = StringVar(value="")
        ttk.Entry(toolbar, textvariable=self.outdir_var, width=26).pack(side=LEFT)
        ttk.Button(toolbar, text="…", width=3, command=self.pick_outdir).pack(side=LEFT, padx=(2, 0))

        # 文件表
        mid = ttk.Frame(self.root, padding=(8, 0))
        mid.pack(side=TOP, fill=BOTH, expand=True)
        cols = ("name", "size", "src", "dst", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        headings = {
            "name": ("文件名", 300), "size": ("大小", 80),
            "src": ("识别格式", 90), "dst": ("目标格式", 90), "status": ("状态", 120),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)

        # 选项
        opt_frame = ttk.Frame(self.root, padding=(8, 6))
        opt_frame.pack(side=TOP, fill=X)
        ttk.Label(opt_frame, text="转换选项（每行 key=value）：").pack(side=LEFT)
        self.options_text = Text(opt_frame, height=3, width=64)
        self.options_text.pack(side=LEFT, fill=X, expand=True)
        self.options_text.insert("1.0", "# 例：\n# quality=95\n# crf=18\n# delimiter=;")
        self.dry_var = BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="仅试算", variable=self.dry_var).pack(side=LEFT, padx=(8, 0))

        # 底部
        bottom = ttk.Frame(self.root, padding=(8, 6))
        bottom.pack(side=TOP, fill=X)
        self.start_btn = ttk.Button(bottom, text="开始转换", command=self.start)
        self.start_btn.pack(side=LEFT)
        self.cancel_btn = ttk.Button(bottom, text="取消", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side=LEFT, padx=(6, 0))
        self.progress = ttk.Progressbar(bottom, orient=HORIZONTAL, mode="determinate")
        self.progress.pack(side=LEFT, fill=X, expand=True, padx=12)

        self.log = Text(self.root, height=7)
        self.log.pack(side=TOP, fill=BOTH, padx=8, pady=(0, 8))
        self.log.configure(state="disabled")

        self._enable_dnd()

    def _enable_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore

            self.tree.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.tree.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            self._log("已启用拖拽（tkinterdnd2）。")
        except Exception:  # noqa: BLE001
            pass

    def _on_drop(self, event) -> None:  # pragma: no cover - 需要 tkinterdnd2
        paths = self.root.tk.splitlist(event.data)  # type: ignore[attr-defined]
        self._add_paths([Path(p) for p in paths])

    def _format_choices(self) -> list[str]:
        choices: list[str] = []
        for f in list_formats():
            choices.append(f["name"])
        return choices

    # ------------------------------------------------------------------ #
    # 文件列表
    # ------------------------------------------------------------------ #
    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择文件")
        self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择文件夹")
        if folder:
            files = [f for f in Path(folder).rglob("*") if f.is_file()]
            self._add_paths(files)

    def _add_paths(self, paths: list[Path]) -> None:
        existing = {r.path.resolve() for r in self.rows}
        for p in paths:
            if p.resolve() in existing or not p.is_file():
                continue
            row = _Row(p)
            row.size = p.stat().st_size
            fmt = detect_format(p)
            row.fmt = fmt.name if fmt else "?"
            row.iid = self.tree.insert("", "end", values=self._row_values(row))
            self.rows.append(row)
        self._log(f"已添加 {len(self.rows)} 个文件。")

    def _row_values(self, row: _Row):
        size = row.size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                size_str = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
                break
            size /= 1024
        return (row.path.name, size_str, row.fmt, self.target_var.get(), row.status)

    def remove_selected(self) -> None:
        """删除选中的文件行。

        注意：必须在 ``tree.delete`` **之前**快照选中 iid——
        删除后 ``selection()`` 里已经不再包含被删项，用过期的选择做过滤
        会把已删行留在 ``self.rows``，随后对失效 iid 调 ``tree.set``
        就会炸出 TclError。
        """
        selected = set(self.tree.selection())
        if not selected:
            return
        for iid in selected:
            self.tree.delete(iid)
        self.rows = [r for r in self.rows if r.iid not in selected]
        self._log(f"已移除 {len(selected)} 个文件，剩余 {len(self.rows)} 个。")

    def clear_all(self) -> None:
        for r in self.rows:
            self.tree.delete(r.iid)
        self.rows.clear()

    def pick_outdir(self) -> None:
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.outdir_var.set(folder)

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _parse_options(self) -> Options:
        opts = Options()
        for line in self.options_text.get("1.0", END).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            from .core.job import parse_option_value

            opts[key.strip()] = parse_option_value(value)
        return opts

    def start(self) -> None:
        if self.running:
            return
        if not self.rows:
            messagebox.showinfo("提示", "请先添加文件。")
            return
        target = self.target_var.get().strip()
        if not target:
            messagebox.showinfo("提示", "请选择目标格式。")
            return
        fmt = guess_format(target)
        if fmt is None:
            messagebox.showerror("错误", f"无法识别的目标格式：{target}")
            return

        outdir = self.outdir_var.get().strip()
        options = self._parse_options()
        jobs: list[Job] = []
        for row in self.rows:
            if outdir:
                dst = Path(outdir) / f"{row.path.stem}.{fmt.primary_ext}"
            else:
                dst = row.path.with_suffix("." + fmt.primary_ext)
            jobs.append(Job(src=row.path, dst=dst, dst_fmt=fmt.name, options=Options(dict(options.values))))
            row.status = "排队中"
            self.tree.set(row.iid, "status", row.status)
            self.tree.set(row.iid, "dst", fmt.name)

        self.running = True
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0.0
        self.progress["maximum"] = max(1, len(jobs))

        def on_progress(event: ProgressEvent) -> None:
            self.events.put(("progress", event))

        self.engine = Engine(workers=min(4, len(jobs)) or 1,
                             on_exists="rename",
                             dry_run=self.dry_var.get(),
                             on_progress=on_progress)

        def worker() -> None:
            try:
                results = self.engine.run(jobs) if self.engine else []
                self.events.put(("done", results))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True, name="uniconvert-gui").start()
        self.root.after(100, self._drain_events)

    def cancel(self) -> None:
        if self.engine is not None:
            self.engine.request_cancel()
            self._log("已请求取消（当前任务完成后停止）。")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._on_event(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._log(f"内部错误：{payload}")
                    self._finish()
        except queue.Empty:
            pass
        if self.running:
            self.root.after(100, self._drain_events)

    def _on_event(self, event: ProgressEvent) -> None:
        index = event.index
        if event.stage == "step" and event.message:
            self._log(f"[{index}] {event.job.label}: {event.message}")
        if event.result is not None:
            result = event.result
            row = next((r for r in self.rows if r.path == result.src), None)
            if row is not None:
                row.status = result.status.label
                self.tree.set(row.iid, "status", row.status)
        self.progress["value"] = max(self.progress["value"], index - 1 + event.fraction)

    def _on_done(self, results) -> None:
        ok = sum(1 for r in results if r.status is Status.DONE)
        failed = [r for r in results if r.status is Status.FAILED]
        for r in results:
            row = next((x for x in self.rows if x.path == r.src), None)
            if row is not None:
                row.status = r.status.label
                self.tree.set(row.iid, "status", row.status)
        self.progress["value"] = self.progress["maximum"]
        self._log(f"完成：成功 {ok} / {len(results)}" +
                  (f"，失败 {len(failed)}" if failed else ""))
        for r in failed[:10]:
            self._log(f"  ✗ {r.job.label}: {r.error}")
        self._finish()

    def _finish(self) -> None:
        self.running = False
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, message + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")


def launch() -> None:  # pragma: no cover - GUI 需要显示器
    root = Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:  # noqa: BLE001
        pass
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    launch()
