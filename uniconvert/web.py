"""网页界面（Flask 可选依赖）。

```bash
uniconvert web --port 8000
```

单页应用：拖文件 → 选目标格式 → **实时进度**（SSE）→ 下载结果。
接口保持简洁的 JSON，便于脚本直接调用：

* ``GET  /api/formats``      格式清单
* ``GET  /api/route?src=json&dst=yaml``  路径查询
* ``POST /api/convert``      同步转换（旧接口，简单场景用）
* ``POST /api/tasks``        异步提交（返回 tid，适合大文件/批量）
* ``GET  /api/tasks/<tid>/events``  SSE 进度流
* ``GET  /api/tasks/<tid>``  任务结果
* ``GET  /api/history``      最近任务历史
* ``GET  /api/download/<sid>/<name>``     下载单个结果
* ``GET  /api/download/<sid>/_all.zip``   打包下载全部
"""

from __future__ import annotations

import io
import json
import queue as _queue
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path

from .api import list_formats
from .core.engine import Engine
from .core.formats import guess_format
from .core.job import Job, Options, Status, parse_option_value
from .errors import UniconvertError

_SESSIONS: dict[str, dict] = {}
_TASKS: dict[str, dict] = {}
_HISTORY: "deque[dict]" = deque(maxlen=50)
_SESSION_TTL = 3600  # 1 小时后过期

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UniConvert 万能格式转换器</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; max-width: 860px;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
  h1 { font-size: 1.5rem; }
  .card { border: 1px solid #8884; border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0; }
  select, textarea, button, input[type=text] { font: inherit; padding: .35rem .6rem;
         border-radius: 8px; border: 1px solid #8886; background: transparent; color: inherit; }
  textarea { width: 100%; box-sizing: border-box; min-height: 4.5em; }
  button { cursor: pointer; }
  button.primary { background: #2563eb; border-color: #2563eb; color: #fff; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: .95rem; }
  td, th { text-align: left; padding: .45rem .5rem; border-bottom: 1px solid #8883; }
  .muted { opacity: .65; font-size: .9rem; }
  .ok { color: #16a34a; } .bad { color: #dc2626; }
  #drop { border: 2px dashed #8886; border-radius: 12px; padding: 1.4rem; text-align: center; }
  #drop.over { border-color: #2563eb; }
</style>
</head>
<body>
<h1>UniConvert · 万能格式转换器</h1>
<p class="muted">零依赖内核，支持文档 / 数据 / 图片 / 音视频 / 压缩包 / 字幕 / 编码等格式族。</p>

<div class="card">
  <div id="drop">把文件拖到这里，或 <button type="button" id="pick">选择文件</button></div>
  <input type="file" id="files" multiple hidden>
  <p id="picked" class="muted">未选择文件</p>
</div>

<div class="card">
  <label>目标格式
    <select id="target"></select>
  </label>
  <label style="margin-left:1rem">同名策略
    <select id="onexists">
      <option value="rename">自动改名</option>
      <option value="overwrite">覆盖</option>
      <option value="skip">跳过</option>
    </select>
  </label>
  <p class="muted">转换选项（每行 key=value，可留空）：</p>
  <textarea id="options" placeholder="quality=95&#10;crf=18&#10;pages=1-3"></textarea>
  <p><button class="primary" id="go">开始转换</button>
     <span id="progress" class="muted"></span></p>
</div>

<div class="card" id="result-card" hidden>
  <h2 style="font-size:1.1rem">结果 <a id="zip-all" hidden>↓ 打包下载全部</a></h2>
  <table id="result"><thead><tr><th>文件</th><th>状态</th><th>输出</th></tr></thead><tbody></tbody></table>
</div>

<div class="card">
  <h2 style="font-size:1.1rem">最近任务</h2>
  <div id="history" class="muted">（暂无）</div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let chosen = [];

async function loadFormats() {
  const res = await fetch('/api/formats');
  const data = await res.json();
  const byCat = {};
  for (const f of data) (byCat[f.category_label] ||= []).push(f);
  const sel = $('target');
  for (const [cat, items] of Object.entries(byCat)) {
    const group = document.createElement('optgroup');
    group.label = cat;
    for (const f of items) {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name + (f.desc ? ' — ' + f.desc : '');
      group.appendChild(opt);
    }
    sel.appendChild(group);
  }
  sel.value = 'pdf';
}

function setFiles(list) {
  chosen = list;
  $('picked').textContent = chosen.length
    ? chosen.map(f => f.name).join('、') : '未选择文件';
}

$('pick').onclick = () => $('files').click();
$('files').onchange = () => setFiles([...$('files').files]);
const drop = $('drop');
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over');
  setFiles([...e.dataTransfer.files]); };

$('go').onclick = async () => {
  if (!chosen.length) { alert('请先选择文件'); return; }
  const fd = new FormData();
  for (const f of chosen) fd.append('files', f, f.name);
  fd.append('target', $('target').value);
  fd.append('on_exists', $('onexists').value);
  fd.append('options', $('options').value);
  $('go').disabled = true;
  $('progress').textContent = '提交中…';
  try {
    const res = await fetch('/api/tasks', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) { alert(data.error); $('go').disabled = false; return; }
    const tid = data.tid;
    const es = new EventSource(`/api/tasks/${tid}/events`);
    es.addEventListener('progress', (e) => {
      const d = JSON.parse(e.data);
      const pct = Math.round((d.fraction || 0) * 100);
      $('progress').textContent = `[${d.index}/${d.total}] 步骤 ${d.step}/${d.step_total} ${d.message}（${pct}%）`;
    });
    es.addEventListener('done', async () => {
      es.close();
      const r = await fetch(`/api/tasks/${tid}`);
      const task = await r.json();
      renderResults(task.results, tid, task.summary);
      $('go').disabled = false;
    });
    es.onerror = () => { es.close(); $('go').disabled = false; $('progress').textContent = '连接中断，可在“历史”里查看结果'; };
  } catch (err) {
    alert('请求失败：' + err);
    $('go').disabled = false;
  }
};

function renderResults(results, sid, summary) {
  const tbody = $('result').querySelector('tbody');
  tbody.innerHTML = '';
  for (const r of results) {
    const tr = document.createElement('tr');
    const ok = r.status === 'done';
    tr.innerHTML = `<td>${r.src}</td><td class="${ok ? 'ok' : 'bad'}">${r.status_label}${r.error ? '：' + r.error : ''}</td>`
      + `<td>${r.download ? `<a href="${r.download}">${r.output}</a>` : '—'}</td>`;
    tbody.appendChild(tr);
  }
  $('result-card').hidden = false;
  const zipAll = $('zip-all');
  zipAll.hidden = !(results.filter(r => r.download).length > 1);
  zipAll.href = `/api/download/${sid}/_all.zip`;
  $('progress').textContent = summary || '';
}

async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const items = await res.json();
    const box = $('history');
    if (!items.length) { box.textContent = '（暂无）'; return; }
    box.innerHTML = items.map(h =>
      `<div>${new Date(h.created * 1000).toLocaleTimeString()} — ${h.summary}`
      + `（${h.files} 个文件，目标 ${h.target}）</div>`).join('');
  } catch (e) {}
}

loadFormats();
loadHistory();
setInterval(loadHistory, 15000);
</script>
</body>
</html>
"""


def _prune_sessions() -> None:
    now = time.time()
    stale = [sid for sid, s in _SESSIONS.items() if now - s["created"] > _SESSION_TTL]
    for sid in stale:
        _SESSIONS.pop(sid, None)
    stale_tasks = [t for t, task in _TASKS.items()
                   if task["status"] == "done" and now - task["created"] > _SESSION_TTL]
    for t in stale_tasks:
        _TASKS.pop(t, None)


def _run_task(task: dict, jobs: list[Job], on_exists: str) -> None:
    """后台线程：执行转换并把进度推进 SSE 队列。"""
    q: _queue.Queue = task["events"]
    tid = task["tid"]

    def hook(event) -> None:
        try:
            q.put({
                "stage": event.stage,
                "index": event.index,
                "total": event.total,
                "step": event.step,
                "step_total": event.step_total,
                "fraction": round(event.fraction, 4),
                "message": event.message or "",
                "label": event.job.label,
            })
        except Exception:  # noqa: BLE001
            pass

    try:
        engine = Engine(workers=min(4, len(jobs)) or 1,
                        on_exists=on_exists, on_progress=hook)
        results = engine.run(jobs)
    except Exception as exc:  # noqa: BLE001
        q.put({"stage": "error", "message": f"{type(exc).__name__}: {exc}",
               "index": 0, "total": 0, "step": 0, "step_total": 0, "fraction": 0})
        results = []

    out: list[dict] = []
    ok_count = 0
    for r in results:
        item = {
            "src": r.job.label,
            "status": r.status.value,
            "status_label": r.status.label,
            "error": r.error,
            "output": r.output.name if r.output else "",
            "download": None,
        }
        if r.status is Status.DONE and r.output:
            ok_count += 1
            item["download"] = f"/api/download/{tid}/{r.output.name}"
        out.append(item)

    task["results"] = out
    task["summary"] = f"完成 {ok_count}/{len(results)}"
    task["status"] = "done"
    _HISTORY.appendleft({
        "tid": tid, "created": task["created"], "summary": task["summary"],
        "files": task["files"], "target": task["target"],
    })
    q.put({"stage": "finished", "summary": task["summary"],
           "index": len(results), "total": len(results),
           "step": 0, "step_total": 0, "fraction": 1.0,
           "message": task["summary"], "label": ""})
    q.put(None)


def create_app():
    """构造 Flask 应用（测试里也可以直接调用）。"""
    try:
        from flask import Flask, jsonify, request, send_file
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Web 界面需要 Flask：pip install flask") from exc

    app = Flask("uniconvert")
    app.config["MAX_CONTENT_LENGTH"] = 2048 * 1024 * 1024  # 单请求 2GB

    @app.get("/")
    def index():
        return PAGE

    @app.get("/api/formats")
    def api_formats():
        return jsonify(list_formats())

    @app.get("/api/route")
    def api_route():
        from .api import route

        src = request.args.get("src", "")
        dst = request.args.get("dst", "")
        try:
            r = route(src, dst)
        except UniconvertError as exc:
            return jsonify({"error": str(exc)}), 400
        if r is None:
            return jsonify({"ok": False, "path": None, "steps": []})
        return jsonify({
            "ok": True,
            "path": str(r),
            "steps": [f"{s.src}->{s.dst} ({s.group})" for s in r.steps],
        })

    def _parse_form_options() -> Options:
        options = Options()
        for line in (request.form.get("options") or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            options[key.strip()] = parse_option_value(value)
        return options

    def _save_uploads(uploads, workdir: Path, fmt) -> list[Job]:
        jobs: list[Job] = []
        for up in uploads:
            safe_name = Path(up.filename or "upload").name
            src = workdir / safe_name
            src.write_bytes(up.read())
            dst = workdir / "out" / f"{src.stem}.{fmt.primary_ext}"
            jobs.append(Job(src=src, dst=dst, dst_fmt=fmt.name,
                            options=Options(_parse_form_options().values)))
        return jobs

    @app.post("/api/convert")
    def api_convert():
        uploads = request.files.getlist("files")
        if not uploads:
            return jsonify({"error": "没有收到文件"}), 400
        target = (request.form.get("target") or "").strip()
        fmt = guess_format(target)
        if fmt is None:
            return jsonify({"error": f"无法识别的目标格式：{target}"}), 400
        on_exists = request.form.get("on_exists", "rename")

        _prune_sessions()
        sid = uuid.uuid4().hex[:12]
        workdir = Path(tempfile.mkdtemp(prefix=f"uniconvert-web-{sid}-"))
        _SESSIONS[sid] = {"created": time.time(), "dir": workdir}

        jobs = _save_uploads(uploads, workdir, fmt)
        engine = Engine(workers=min(4, len(jobs)) or 1, on_exists=on_exists)
        results = engine.run(jobs)

        out: list[dict] = []
        ok_count = 0
        for r in results:
            item = {
                "src": r.job.label,
                "status": r.status.value,
                "status_label": r.status.label,
                "error": r.error,
                "route": str(r.route) if r.route else "",
                "output": r.output.name if r.output else "",
                "download": None,
            }
            if r.status is Status.DONE and r.output:
                ok_count += 1
                item["download"] = f"/api/download/{sid}/{r.output.name}"
            out.append(item)

        return jsonify({
            "sid": sid,
            "results": out,
            "summary": f"完成 {ok_count}/{len(results)}",
        })

    @app.post("/api/tasks")
    def api_tasks_create():
        """异步提交转换任务：立即返回 tid，用 SSE 拿进度。"""
        uploads = request.files.getlist("files")
        if not uploads:
            return jsonify({"error": "没有收到文件"}), 400
        target = (request.form.get("target") or "").strip()
        fmt = guess_format(target)
        if fmt is None:
            return jsonify({"error": f"无法识别的目标格式：{target}"}), 400
        on_exists = request.form.get("on_exists", "rename")

        _prune_sessions()
        tid = uuid.uuid4().hex[:12]
        workdir = Path(tempfile.mkdtemp(prefix=f"uniconvert-web-{tid}-"))
        _SESSIONS[tid] = {"created": time.time(), "dir": workdir}

        jobs = _save_uploads(uploads, workdir, fmt)
        task = {
            "tid": tid,
            "status": "running",
            "created": time.time(),
            "events": _queue.Queue(),
            "results": [],
            "summary": "",
            "target": fmt.name,
            "files": len(jobs),
        }
        _TASKS[tid] = task
        threading.Thread(
            target=_run_task, args=(task, jobs, on_exists),
            daemon=True, name=f"uc-web-{tid}",
        ).start()
        return jsonify({"tid": tid})

    @app.get("/api/tasks/<tid>")
    def api_task_get(tid: str):
        task = _TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify({
            "tid": tid,
            "status": task["status"],
            "summary": task["summary"],
            "results": task["results"],
        })

    @app.get("/api/tasks/<tid>/events")
    def api_task_events(tid: str):
        from flask import Response

        task = _TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        q: _queue.Queue = task["events"]

        def gen():
            while True:
                item = q.get()
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield ("event: progress\ndata: "
                       + json.dumps(item, ensure_ascii=False) + "\n\n")

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/history")
    def api_history():
        return jsonify(list(_HISTORY))

    @app.get("/api/download/<sid>/<name>")
    def api_download(sid: str, name: str):
        session = _SESSIONS.get(sid)
        if not session:
            return jsonify({"error": "会话不存在或已过期"}), 404
        workdir: Path = session["dir"]
        if name == "_all.zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in sorted((workdir / "out").glob("*")):
                    if f.is_file():
                        zf.write(f, f.name)
            buf.seek(0)
            return send_file(buf, mimetype="application/zip",
                             as_attachment=True, download_name="uniconvert-results.zip")
        target = workdir / "out" / Path(name).name
        if not target.is_file():
            return jsonify({"error": "文件不存在"}), 404
        return send_file(target, as_attachment=True, download_name=target.name)

    return app


def launch(host: str = "127.0.0.1", port: int = 8000, *, debug: bool = False) -> None:  # pragma: no cover
    app = create_app()
    print(f"UniConvert Web 已启动：http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":  # pragma: no cover
    launch()
