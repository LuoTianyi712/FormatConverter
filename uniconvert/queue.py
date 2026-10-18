"""任务队列：SQLite 持久化 + 断点续跑。

```bash
uniconvert queue add 素材/*.png -t webp --out 输出/   # 入队（不执行）
uniconvert queue run -j 8                             # 执行（可反复跑，续完为止）
uniconvert queue list                                 # 查看进度
uniconvert queue clear --done                         # 清理已完成
```

设计要点：

* 任务存 ``~/.uniconvert/queue.db``（可用 ``--db`` 指定）；
* ``queue run`` 只领 ``pending`` 和上次中断残留的 ``running`` 任务
  （打开数据库时会把 ``running`` 复位成 ``pending``，即断点续跑）；
* 每次执行结果实时写回状态（done/failed + 错误信息 + 尝试次数），
  进程被杀也不会丢进度。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .core.engine import Engine
from .core.job import Job, JobResult, Options, Status
from .errors import ValidationError

__all__ = ["JobQueue", "default_db_path"]

#: 默认数据库位置
def default_db_path() -> Path:
    override = os.environ.get("UNICONVERT_QUEUE_DB", "")
    if override:
        return Path(override)
    return Path.home() / ".uniconvert" / "queue.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    to_fmt TEXT DEFAULT '',
    options TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


@dataclass
class QueueItem:
    id: int
    src: str
    dst: str
    to_fmt: str
    options: dict
    status: str
    error: str
    attempts: int
    created: float
    updated: float

    def to_job(self) -> Job:
        return Job(
            src=Path(self.src),
            dst=Path(self.dst),
            dst_fmt=self.to_fmt,
            options=Options(dict(self.options)),
            label=Path(self.src).name,
        )

    @property
    def status_label(self) -> str:
        return {
            "pending": "等待中", "running": "转换中", "done": "完成",
            "failed": "失败", "skipped": "跳过", "cancelled": "已取消",
        }.get(self.status, self.status)


class JobQueue:
    """SQLite 任务队列。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # 断点续跑：上次运行中被中断的任务复位为 pending
        self._conn.execute(
            "UPDATE jobs SET status='pending', updated=? WHERE status='running'",
            (time.time(),),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def add(self, jobs: Iterable[Job]) -> list[int]:
        """入队一组任务，返回任务 id 列表。"""
        ids: list[int] = []
        now = time.time()
        for job in jobs:
            cur = self._conn.execute(
                "INSERT INTO jobs (src, dst, to_fmt, options, status, created, updated)"
                " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (str(job.src), str(job.dst), job.dst_fmt,
                 json.dumps(job.options.to_dict(), ensure_ascii=False, default=str),
                 now, now),
            )
            ids.append(int(cur.lastrowid))
        self._conn.commit()
        return ids

    def _update(self, job_id: int, status: str, *, error: str = "", bump: bool = False) -> None:
        if bump:
            self._conn.execute(
                "UPDATE jobs SET status=?, error=?, attempts=attempts+1, updated=?"
                " WHERE id=?",
                (status, error, time.time(), job_id),
            )
        else:
            self._conn.execute(
                "UPDATE jobs SET status=?, error=?, updated=? WHERE id=?",
                (status, error, time.time(), job_id),
            )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get(self, job_id: int) -> QueueItem | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, *, status: str = "", limit: int = 500) -> list[QueueItem]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY id LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def pending(self) -> list[QueueItem]:
        return self.list(status="pending")

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _row(row: sqlite3.Row) -> QueueItem:
        try:
            options = json.loads(row["options"] or "{}")
        except json.JSONDecodeError:
            options = {}
        return QueueItem(
            id=row["id"], src=row["src"], dst=row["dst"], to_fmt=row["to_fmt"],
            options=options, status=row["status"], error=row["error"] or "",
            attempts=row["attempts"], created=row["created"], updated=row["updated"],
        )

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        engine_kwargs: dict | None = None,
        ids: Sequence[int] | None = None,
        on_progress: Callable | None = None,
    ) -> list[tuple[int, JobResult]]:
        """执行全部 pending 任务（可反复调用续跑），返回 ``(id, 结果)`` 列表。"""
        items = [self.get(i) for i in ids] if ids else self.pending()
        items = [i for i in items if i is not None and i.status == "pending"]
        if not items:
            return []

        for item in items:
            self._update(item.id, "running", bump=True)

        jobs = [item.to_job() for item in items]
        engine = Engine(**(engine_kwargs or {}))
        results = engine.run(jobs, on_progress=on_progress)

        out: list[tuple[int, JobResult]] = []
        for item, result in zip(items, results):
            status = result.status.value
            self._update(item.id, status, error=result.error)
            out.append((item.id, result))
        return out

    def clear(self, *, done_only: bool = False) -> int:
        if done_only:
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE status IN ('done','skipped','cancelled')"
            )
        else:
            cur = self._conn.execute("DELETE FROM jobs")
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobQueue":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def jobs_from_paths(
    files: Iterable[str | Path],
    *,
    to: str,
    outdir: str | Path | None = None,
    options: Options | dict | None = None,
    on_exists_suffix: bool = False,
) -> list[Job]:
    """把文件列表装成全速转换任务（queue add 用）。"""
    if not to:
        raise ValidationError("queue add 需要用 --to 指定目标格式")
    jobs: list[Job] = []
    outdir_p = Path(outdir) if outdir else None
    for f in files:
        p = Path(f)
        dst = outdir_p if outdir_p else None
        jobs.append(Job.make(p, dst, to=to, options=options))
    return jobs
