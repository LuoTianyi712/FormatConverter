# UniConvert 设计文档

> 版本 1.0 · 2026-10 · 对应代码 `uniconvert/`

## 1. 背景与目标

### 1.1 市场调研摘要

对主流格式转换产品的能力拆解（2026）：

| 产品 | 定位 | 强项 | 短板 |
|---|---|---|---|
| 格式工厂 / 迅捷 | 桌面全能 | 音视频、图片、PDF、批量 | 只支持 Windows；数据/字幕/电子书弱；无自动寻路 |
| HandBrake | 视频专精 | 转码质量与预设 | 只做视频 |
| Calibre | 电子书专精 | 书库管理与格式链 | 只做电子书 |
| Smallpdf / iLovePDF | PDF 在线工具集 | PDF 合并/压缩/签署体验极好 | 只做 PDF；上传隐私顾虑 |
| CloudConvert / Convertio | 云端全能 | 格式覆盖最广（API 化） | 依赖网络上传；免费额度限制 |
| 7-Zip / WinRAR | 压缩 | 压缩率/加密 | 只做压缩 |

**共性缺口**（UniConvert 的机会点）：

1. 跨族"短路"能力弱：docx→epub、csv→xlsx、srt→ass 这类"顺手转换"要么没有、要么藏在各工具里；
2. 无**自动寻路**：不支持 A→B 时几乎都直接报错，而不是尝试 A→X→B；
3. 依赖缺失时体验差：跑到一半才报错，而不是规划期阻止并提示安装命令；
4. 隐私：在线工具必须上传；桌面工具则普遍仅 Windows。

### 1.2 设计目标

- **万能**：八大族 110+ 格式；族内全连通（数据族 9×9、字幕族 5×5 两两直达）；
- **零依赖内核**：核心 + 文档/数据/字幕/编码转换只用标准库，`pip install` 后即可工作；
- **自动路由**：无直连时 Dijkstra 找最优中转链路，路径可解释（`route`/`dry-run`）；
- **优雅降级**：重能力（FFmpeg/Pillow/LibreOffice）在规划期过滤，缺失时给出安装命令；
- **三个前端**：CLI（自动化）、GUI（桌面用户）、Web（跨设备/演示）；
- **可扩展**：插件机制 + 自环转换（同格式重处理）+ 依赖感知的边。

### 1.3 非目标（v1）

- 不做云服务/账号体系；不追求 PDF 版式保真（那是 LibreOffice/Adobe 的工作）；
- 不做 OCR、PDF 编辑（列入路线图）；不做流式转码（大视频内存占用更高，但胜在简单可靠）。

## 2. 总体架构

```text
┌─────────────────────── 前端层 ────────────────────────┐
│  cli.py（argparse）  gui.py（tkinter）  web.py（Flask）│
└───────────────┬──────────────────────────────────────┘
                │ Job / Options / JobResult（core/job.py）
┌───────────────▼──────────────────────────────────────┐
│                    api.py（公共 API）                  │
│  convert / convert_batch / route / list_formats / ... │
└───────────────┬──────────────────────────────────────┘
                │
┌───────────────▼───────────────┐   ┌──────────────────┐
│        engine.py 执行引擎      │──▶│  plugins.py 插件  │
│  规划│并发│重试│覆盖策略│进度   │   │  目录/entry-point │
└───────┬───────────────┬───────┘   └──────────────────┘
        │               │
┌───────▼──────┐  ┌─────▼──────────────┐
│ graph.py     │  │  registry.py       │
│ Dijkstra 寻路 │◀─│  转换器=有向边      │
│ 依赖诊断      │  │  (src→dst, deps…)  │
└───────┬──────┘  └─────▲──────────────┘
        │               │ 注册
┌───────▼──────┐  ┌─────┴────────────────────────────────┐
│ formats.py   │  │ converters/  内置转换器族              │
│ 格式世界地图  │  │ data│text│subtitle│archive│document  │
│ + detect.py  │  │ pdf│image│media│font（可选依赖按需）    │
└──────────────┘  └──────────────────────────────────────┘
```

数据流（一次转换）：

```text
Input(路径/字符串)
  → detect.sniff（文件头第一优先）
  → Job(源格式, 目标格式, Options)
  → graph.find_route（可用边过滤 → Dijkstra）
  → engine 逐步执行（多步经临时目录中转）
  → JobResult（状态/输出/耗时/警告/路径）
```

## 3. 核心模型

### 3.1 格式（节点）

`core/formats.py` 的 `Format`：规范名、大类、扩展名、MIME、**magic 特征**、
是否无损、是否文本、选项清单、别名。110+ 格式一次声明，全局共用，
供给嗅探（detect）、寻路（graph）、界面（CLI/GUI 过滤器）三处消费。

**扩展新格式 = 加一条记录**，其余零改动。

### 3.2 转换器（边）

```python
def func(src: Path, dst: Path, opts: Options) -> Path | None: ...
```

注册时携带元数据：

| 字段 | 含义 | 引擎如何使用 |
|---|---|---|
| `deps` | 外部依赖（`ffmpeg` 或 `py:PIL`） | 规划期过滤；缺失则出安装提示 |
| `weight` / `quality` | 边代价/质量评分 | 寻路代价 = weight / quality |
| `group` | 族名（ffmpeg/data/…） | 统计、路由展示、按组启停 |
| `allow_self` | 允许 src==dst 自环 | 同格式重处理（美化 JSON、平移字幕） |
| `note` | 中文备注 | `route` 输出里直接可读 |

### 3.3 寻路

- 代价函数 `cost(e) = e.weight / max(e.quality, ε)`：权重小、质量高的边优先；
- Dijkstra 保证首次到达即最优；中间链路剪掉自环；同代价用插入序稳定；
- `available_only=True` 先过滤依赖缺失边；找不到路时做"忽略依赖"二次寻路，
  若存在则把缺的依赖名提取出来抛 `DependencyMissingError`（含安装命令）。
- `src == dst`：优先走自环（重处理），无自环则 0 步直通（引擎执行复制）。

### 3.4 任务与结果

`Job(src, dst, src_fmt, dst_fmt, options)` —— `dst` 可为文件、目录或省略（配合 `--to`）；
`JobResult(status, route, output, elapsed, src_size/dst_size, warnings, error)` ——
**引擎永不抛异常**，错误全部进 `JobResult`，批量执行天然隔离单个失败。

### 3.5 执行策略

- 覆盖：`rename / skip / overwrite / error` 四策略；
- 多步中转：`tempfile.mkdtemp` 隔离，finally 清理（`keep_temp` 可留档）；
- 并发：ThreadPoolExecutor（I/O 与外部进程型任务为主）；
- 重试：仅对 `ConversionError` 重试（依赖缺失/参数错误不浪费重试）；
- 进度：`ProgressEvent(job, index, total, stage, step/step_total, fraction)`，
  回调异常被引擎吞掉（前端故障不影响转换）；
- 取消：`request_cancel()` 事件，未开始任务标记取消，在跑任务做完当前步。

### 3.6 错误体系

```text
UniconvertError
├── UnsupportedFormatError   格式无法识别（带"是不是想用 xx"提示）
├── NoRouteError             图上不可达
├── DependencyMissingError   缺依赖（带一行安装命令）
├── ConversionError          转换失败（包装底层异常 + 边信息）
├── ValidationError          输入/参数不合法
└── CancelledError           用户取消
```

原则：**错误信息必须是"下一步怎么做"**。缺依赖给安装命令；解码失败提示换编码；
7z 后端缺失列出两种后端任选其一。

## 4. 各族实现要点（渐进增强）

### 4.1 零依赖族（核心卖点）

| 族 | 原理 |
|---|---|
| 数据 | 统一"解析→Python 对象→序列化"；YAML 走 PyYAML，TOML 读取用 tomllib、写出用自研最小 dumper（标量/数组/嵌套表/表数组） |
| 字幕 | 统一 `Cue(start_ms, end_ms, text, style)` 中间模型；5 种格式两两互转 + 时间平移 |
| 文档 | **OOXML/ODF/EPUB 都是 ZIP+XML**：docx 段落/表格、odt 标题/列表、xlsx 共享字符串+行列引用、epub spine 顺序、fb2 树遍历；html→epub 手写 EPUB3 打包（mimetype 首条且不压缩） |
| 归档 | zip/tar 标准库流式重打包；gz/bz2/xz 标准库、zst 用 zstandard；**解压校验**：文本查可解码+结构化轻解析，二进制查 magic |
| 编码 | base64/hex/urlencode/quoted-printable/rot13 互转；hash 支持 8 种算法 + HMAC + crc32 |
| 文本 | Markdown 渲染器（块级+行内语法，约 120 行）；HTML→Markdown/文本用 `html.parser`；RTF 分组剥离 + `\uN`/`\'hh` 解码 |

### 4.2 可选依赖族（规划期感知）

- **image**：Pillow 光栅互转（质量/缩放/旋转/动画帧透传）；SVG 栅格化四后端降级
  （cairosvg→inkscape→magick→rsvg）；位图→SVG 内嵌**零依赖**；
- **pdf**：文本抽取与页面渲染各三后端降级（pypdf/PyMuPDF/poppler），多页输出 `name-0001.png`；
- **media**：FFmpeg 参数翻译层（编码器表/CRF/分辨率/裁剪/旋转/字幕压制/GIF 调色板双 pass）；
- **office**：LibreOffice 无头转换兜底 doc/ppt/xls→pdf 等；
- **font**：fontTools 容器互转（ttf/otf ⇄ woff/woff2）。

### 4.3 防伪路径（重点设计）

压缩流的边是"任意→gz / gz→任意"，它让 `data.json.gz → data.json` 这类需求免费获得，
但也可能产出 `mp4 → gz → mkv` 之类的**字节重解释伪路径**。两道闸门：

1. 规划期：解压边只向「内容可验证」的目标注册（文本格式 / 有 magic 的格式 / bin），
   无特征格式（stl、tga…）不开放解压边 → `midi→stl` 直接 NoRoute；
2. 执行期：解压后校验——文本格式验证可解码并对结构化格式做**轻量真解析**
   （json.loads / tomllib / ElementTree / 字幕 parser）；二进制格式验证文件头 magic。
   失败即报错并删除半成品，绝不静默产出损坏文件。

## 5. 前端设计

| 前端 | 技术 | 适用 | 关键交互 |
|---|---|---|---|
| CLI | argparse（零依赖） | 脚本/自动化 | 子命令动词化、`--dry-run`、退出码语义（0/1/2）、中文帮助、UTF-8 控制台适配 |
| GUI | tkinter（零依赖） | 桌面用户 | 文件表（识别格式列）、目标格式下拉、进度条、日志、后台线程 + 队列轮询、可选拖拽（tkinterdnd2） |
| Web | Flask（可选） | 跨设备/演示 | 单页拖拽上传 → 转换 → 逐个下载/打包 zip；JSON API（/api/formats /api/route /api/convert） |

三端共享同一份 `Engine` 与 `Job/Options/JobResult`，前端只做"收集输入、渲染事件"。

## 6. 插件协议

```python
from uniconvert.core import register          # 注册边
from uniconvert.converters._util import need_dep, run_tool, read_text ...

@register("csv", "reweek", group="myplugin", deps=("pandas",))
def conv(src, dst, opts): ...
```

三种加载源：`UNICONVERT_PLUGIN_PATH` → `~/.uniconvert/plugins/*.py` → entry-point
（组名 `uniconvert.converters`）。加载失败收集为 `PluginInfo.error`，不拖垮主程序；
未知格式名（不在 formats.py）也可作为节点参与寻路——`engine` 对不识别的扩展名
退化为"按扩展名建节点"。

## 7. 质量保障

- **136 个 unittest**（零依赖运行）：格式/嗅探/寻路（独立 Registry）/
  数据族往返/字幕往返/编码往返/归档含 Zip-Slip 与伪路径拦截/文档族（内存构造
  docx/xlsx/odt/epub/fb2 夹具）/引擎策略（覆盖/重试/取消/进度/临时清理）/CLI 端到端/插件热加载；
- 目录安全：解压防路径穿越（Zip Slip）；
- 失败清理：解压校验失败删除半成品；中间文件 finally 清理。

## 8. 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| v1.0 | 内核 + 八大族 + 三前端 + 插件 + 136 测试 | ✅ |
| v1.1 | PDF 工具箱（合并/拆分/旋转/加密/解密/抽图/压缩）；图片工具（水印/拼接/压到指定大小/动图变速）；媒体增强（平台预设、变速、音量、淡入淡出、HLS、真实进度）；3D 互转；通讯录；watch；右键菜单；208 测试 | ✅ |
| v1.2 | **OCR**（Tesseract 命令行后端 + 转换图 ocr 边）；**PDF 页码/中文矢量水印**（Helvetica/STSong-Light CID + ExtGState 透明度）；**视频水印**（drawtext/overlay 复杂滤镜）；**人声分离**（Demucs two-stems）；**CAJ→PDF**（内嵌 PDF 启发式提取）；**批量重命名**（两阶段防链式覆盖）；**任务队列**（SQLite 持久化 + 断点续跑）；**Web 异步任务 + SSE 实时进度 + 历史**；247 测试 | ✅ |
| v1.3 | PDF→Word/PPT（版式重建）；视频去水印；扫描 PDF 可搜索化；Web 分片上传 | 规划 |

### v1.2 关键设计补充

**OCR 走命令行而非 pytesseract**：少一层 Python 依赖、便于用户在
冻结版 exe 之外自查引擎版本；转换图里注册 ``图片→txt`` 的 OCR 边
（``deps=("tesseract",)``），未装引擎时该边在规划期被过滤，
``uniconvert ocr`` 命令则给出一条 ``winget install`` 的安装指引。

**PDF 水印的字体策略**：纯 ASCII 用 Helvetica（兼容性最大）；
含中文用 PDF 标准 CJK 字体 ``STSong-Light + UniGB-UCS2-H``
（零嵌入、Acrobat/Chrome/Edge 均支持），透明度过 ExtGState ``/ca``。
踩坑记录：pypdf 6 的 ``NumberObject`` 会把浮点序列化成 0，
透明度必须用 ``FloatObject``；叠加页必须挂在**独立 writer** 上，
否则 ``add_blank_page`` 会把 overlay 混进输出页序列（页数翻倍）。

**重命名的两阶段提交**：先全部改临时名、再改目标名，
天然免疫 a→b、b→c 相互覆盖；计划内重名会让**双方**都标记错误整组拒绝。

**队列的断点续跑**：打开数据库时把残留 ``running`` 复位为 ``pending``，
每次结果实时写回；``queue run`` 可反复执行直到清空，
适合长时间批量（进程被杀只损失当前一批）。

**Web SSE 进度**：``POST /api/tasks`` 后台线程跑 Engine，
进度事件进内存队列，``GET /api/tasks/<id>/events`` 以
``text/event-stream`` 推送；完成后写入最近任务历史（内存 deque，上限 50）。

### v1.1 关键设计补充

**工具箱与转换器的分工**：`pdf merge/split`、`image watermark/collage` 这类
「一对多输入」或「参数化操作」不适合塞进 `convert` 子命令，
统一做成 `uniconvert pdf <action>` / `uniconvert image <action>` 命令组，
同时导出为 Python API（`pdf_merge()` / `image_watermark()`），
GUI/Web/插件可直接调用。转换图只负责「格式对格式」，
工具箱负责「操作语义」，两者共享同一批后端依赖。

**媒体进度回传**：引擎执行每步前向 `Options` 注入保留键 `__progress__`
回调，ffmpeg 后端解析 `-progress pipe:1` 的 `out_time_us` 并按时长换算比例；
`build_media_args()` 被拆成纯函数（不碰 ffmpeg 可单测参数翻译），
平台预设（wechat/douyin/bilibili/tiny）以「键值包」形式合并进 opts，
不覆盖用户显式给的选项。

**防伪路径的继续强化**：解压校验扩展到 obj/ply/svg（轻量结构检查），
让 `midi → gz → obj → stl` 这类多跳伪链路在运行时干净失败且不留半成品。
