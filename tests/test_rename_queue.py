"""批量重命名 + 任务队列（SQLite 持久化/断点续跑）测试。"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from uniconvert.cli import main
from uniconvert.core.engine import Engine
from uniconvert.core.job import Job, Status
from uniconvert.queue import JobQueue
from uniconvert.rename import rename_batch


def run_cli(*argv: str):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = main(list(argv))
    return code, buf.getvalue()


class TestRename(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-ren-"))
        for name in ("IMG_001.jpg", "IMG_002.jpg", "IMG_010.jpg", "other.txt"):
            (self.tmp / name).write_text("x", encoding="utf-8")

    def test_preview_does_not_touch_disk(self):
        plans = rename_batch([str(self.tmp / "IMG_*.jpg")] and
                             sorted(self.tmp.glob("IMG_*.jpg")),
                             r"IMG_(\d+)", "照片-{1}")
        changed = [p for p in plans if p.changed]
        self.assertEqual(len(changed), 3)
        self.assertFalse((self.tmp / "照片-001.jpg").exists())  # 没动盘
        self.assertTrue((self.tmp / "IMG_001.jpg").exists())

    def test_apply_regex_groups(self):
        files = sorted(self.tmp.glob("IMG_*.jpg"))
        plans = rename_batch(files, r"IMG_(\d+)", "照片-{1}", apply=True)
        self.assertTrue(all(not p.error for p in plans))
        self.assertTrue((self.tmp / "照片-001.jpg").exists())
        self.assertTrue((self.tmp / "照片-010.jpg").exists())
        self.assertFalse((self.tmp / "IMG_001.jpg").exists())

    def test_sequence_placeholder(self):
        files = sorted(self.tmp.glob("IMG_*.jpg"))
        rename_batch(files, "", "wall-{n}", start=5, pad=2, apply=True)
        self.assertTrue((self.tmp / "wall-05.jpg").exists())
        self.assertTrue((self.tmp / "wall-07.jpg").exists())

    def test_no_match_kept(self):
        files = sorted(self.tmp.glob("*.txt"))
        plans = rename_batch(files, r"IMG_(\d+)", "x-{1}", apply=True)
        self.assertFalse(plans[0].matched)
        self.assertTrue((self.tmp / "other.txt").exists())

    def test_chain_conflict_safe(self):
        """a→b、b→c 这种链式改名不能互相覆盖。"""
        d = self.tmp
        (d / "a.txt").write_text("A", encoding="utf-8")
        (d / "b.txt").write_text("B", encoding="utf-8")
        # 用 python 脚本做链式：a→b 会撞已存在的 b → 计划里应报错跳过
        plans = rename_batch([d / "a.txt"], "", "b", apply=False)
        self.assertTrue(plans[0].error)  # b.txt 已存在

    def test_duplicate_in_plan_detected(self):
        d = self.tmp
        files = [d / "IMG_001.jpg", d / "IMG_002.jpg"]
        plans = rename_batch(files, r"IMG_\d+", "same", apply=False)
        errors = [p.error for p in plans]
        self.assertTrue(all(errors))  # 同计划重名被挡下

    def test_cli_preview_and_apply(self):
        code, out = run_cli("rename", str(self.tmp / "IMG_*.jpg"),
                            "--find", r"IMG_(\d+)", "--replace", "pic-{1}")
        self.assertEqual(code, 0, out)
        self.assertIn("预览", out)
        self.assertTrue((self.tmp / "IMG_001.jpg").exists())

        code, out = run_cli("rename", str(self.tmp / "IMG_*.jpg"),
                            "--find", r"IMG_(\d+)", "--replace", "pic-{1}",
                            "--apply")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tmp / "pic-001.jpg").exists())


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uc-q-"))
        self.db = self.tmp / "queue.db"
        self.src = self.tmp / "src"
        self.out = self.tmp / "out"
        self.src.mkdir()
        self.out.mkdir()

    def _jobs(self, n: int = 3):
        jobs = []
        for i in range(n):
            p = self.src / f"f{i}.json"
            p.write_text('{"i": %d}' % i, encoding="utf-8")
            jobs.append(Job.make(p, self.out, to="yaml"))
        return jobs

    def test_add_and_run(self):
        with JobQueue(self.db) as q:
            ids = q.add(self._jobs(3))
            self.assertEqual(len(ids), 3)
            self.assertEqual(len(q.pending()), 3)
            pairs = q.run(engine_kwargs={"workers": 1})
            self.assertEqual(len(pairs), 3)
            self.assertTrue(all(r.status is Status.DONE for _, r in pairs))
            self.assertEqual(len(list(self.out.glob("*.yaml"))), 3)
            self.assertEqual(q.stats().get("done"), 3)
            # 再 run 一次：没有 pending，什么都不做
            self.assertEqual(q.run(engine_kwargs={"workers": 1}), [])

    def test_resume_after_interruption(self):
        """模拟中断：running 状态在下一次打开数据库时复位为 pending。"""
        with JobQueue(self.db) as q:
            q.add(self._jobs(2))
            # 手动把一条标记成 running（模拟进程被杀）
            q._conn.execute("UPDATE jobs SET status='running' WHERE id=1")
            q._conn.commit()
        with JobQueue(self.db) as q2:
            items = {i.id: i.status for i in q2.list()}
            self.assertEqual(items.get(1), "pending")  # 已复位
            pairs = q2.run(engine_kwargs={"workers": 1})
            self.assertEqual(len(pairs), 2)

    def test_failed_job_recorded(self):
        bad = self.src / "bad.json"
        bad.write_text("{非法", encoding="utf-8")
        with JobQueue(self.db) as q:
            q.add([Job.make(bad, self.out, to="yaml")])
            q.run(engine_kwargs={"workers": 1})
            item = q.list(status="failed")[0]
            self.assertIn("解析失败", item.error)
            self.assertEqual(item.attempts, 1)

    def test_clear(self):
        with JobQueue(self.db) as q:
            q.add(self._jobs(2))
            q.run(engine_kwargs={"workers": 1})
            self.assertEqual(q.clear(done_only=True), 2)
            self.assertEqual(q.stats(), {})

    def test_cli_queue_flow(self):
        src = self.src / "cli.json"
        src.write_text('{"a": 1}', encoding="utf-8")
        code, out = run_cli("queue", "add", str(src), "-t", "yaml",
                            "--out", str(self.out), "--db", str(self.db))
        self.assertEqual(code, 0, out)
        self.assertIn("已入队 1", out)

        code, out = run_cli("queue", "run", "--db", str(self.db))
        self.assertEqual(code, 0, out)
        self.assertTrue((self.out / "cli.yaml").exists())

        code, out = run_cli("queue", "list", "--db", str(self.db))
        self.assertEqual(code, 0, out)
        self.assertIn("完成", out)


if __name__ == "__main__":
    unittest.main()
