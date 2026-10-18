# UniConvert · 万能格式转换器

零依赖内核 + 自动路由 + 批量并发 + 插件系统。
一个转换引擎打通 **文档 / 数据 / 图片 / 音视频 / 压缩包 / 字幕 / 字体 / 编码** 八大格式族，
覆盖市面上格式工厂 / 迅捷 / Smallpdf / CloudConvert / HandBrake / Calibre 的常见能力，
且 **不装任何第三方包就能跑核心功能**。

```text
                    ┌─────────────┐
   CLI (argparse) ──┤             │
   GUI (tkinter) ───┤   转换引擎   │──► 转换图（Dijkstra 寻路）
   Web (Flask)  ────┤   Engine    │      A → X → B 自动中转
   Python API   ────┤             │      JSON→YAML→TOML… 全连通
   插件系统     ────└─────────────┘
```

## 30 秒上手

```bash
# 无需安装依赖，直接用
python -m uniconvert formats                 # 看看支持哪些格式（110+ 种）
python -m uniconvert route docx epub         # 文档→电子书怎么走？docx→html→epub
python -m uniconvert convert 报告.docx -t pdf
python -m uniconvert convert 数据.json 数据.toml
python -m uniconvert batch 图片/*.png -t webp --out 输出目录/ -j 8
python -m uniconvert doctor                  # 体检：哪些可选能力已就绪
```

或安装为命令（可选）：

```bash
pip install -e .          # 零依赖安装
pip install -e .[all]     # 一次装齐常见 Python 后端（Pillow/PyYAML/pypdf…）
```

## 功能矩阵（对标市面产品）

| 能力 | UniConvert | 格式工厂 | Smallpdf | CloudConvert | 说明 |
|---|---|---|---|---|---|
| 数据格式互转（JSON/YAML/TOML/XML/INI/CSV/plist…） | ✅ 全连通 + 类型推断 | 部分 | ✗ | 部分 | **零依赖**（YAML 需 PyYAML） |
| 字幕互转（SRT/ASS/SSA/VTT/LRC） | ✅ 全连通 + 平移 | 部分 | ✗ | 部分 | **零依赖**，任意两种互转 |
| **PDF 工具箱**（合并/拆分/旋转/加密/解密/抽图/压缩） | ✅ | ✅ | ✅ | ✅ | pypdf 后端，`uniconvert pdf -h` |
| **图片工具**（水印/拼接长图/压到指定大小） | ✅ | ✅ | ✗ | 部分 | Pillow 后端，`uniconvert image -h` |
| Office 文档提取（docx/odt/pptx/xlsx/ods→txt/md/html/csv…） | ✅ 内置解析 | ✅ | ✅ | ✅ | **零依赖**（OOXML/ODF 即 ZIP+XML） |
| 电子书（epub/fb2→txt/html/md，html→epub） | ✅ | ✗ | ✗ | 部分 | **零依赖**，Calibre 风格 |
| 压缩包重打包（zip/tar/7z/cbz）+ gz/bz2/xz/zst | ✅ 流式大文件 | ✅ | ✗ | ✅ | 7z 需 py7zr 或 7z.exe |
| 图片转换（jpg/png/webp/bmp/gif/tiff/ico…） | ✅ | ✅ | 部分 | ✅ | 需 Pillow；SVG 栅格化另有多后端 |
| 图片 ⇄ SVG | ✅ 内嵌 SVG **零依赖** | ✗ | ✗ | 部分 | SVG→位图需 cairosvg/inkscape 等 |
| 音视频转码 / GIF / 抽帧 / 抽字幕轨 | ✅ + **平台预设** | ✅ | ✗ | ✅ | 需 FFmpeg；`profile=wechat/douyin` |
| 视频变速/音量/淡入淡出/HLS(m3u8) | ✅ | 部分 | ✗ | 部分 | `speed=1.5 volume=-6`，`-t m3u8` |
| PDF：→ txt / → 图片 / 图片→PDF | ✅ | ✅ | ✅ | ✅ | 多后端降级（pypdf/PyMuPDF/poppler） |
| 3D 模型互转（stl/obj/ply） | ✅ **零依赖** | ✗ | ✗ | 部分 | 三角面几何转换 |
| 通讯录（vcf ⇄ csv/json） | ✅ **零依赖** | ✗ | ✗ | 部分 | 手机通讯录导出转换 |
| 批量 + 并发 + 重试 + 覆盖策略 | ✅ | ✅ | 部分 | ✅ | `-j 8 --retry 1 --on-exists skip` |
| **目录监控自动转换** | ✅ 零依赖轮询 | 部分 | ✗ | ✗ | `uniconvert watch` |
| **Windows 右键菜单集成** | ✅ 一键安装脚本 | ✅ | ✗ | ✗ | `packaging/install_context_menu.ps1` |
| JSON 机器可读报告（流水线） | ✅ `--report` | ✗ | ✗ | API | 脚本化转换的关键 |
| 自动寻路（无直连时中转） | ✅ 图寻路 | ✗ | ✗ | ✗ | **核心差异化** |
| 插件系统（热加载自定义转换器） | ✅ 目录/entry-point | 部分 | ✗ | ✗ | 见 `examples/plugin_reweek.py` |
| 三个前端（CLI / GUI / Web） | ✅ | GUI | Web | Web | tkinter 零依赖，Web 需 Flask |
| 干跑预览 / 路径可视化 | ✅ | ✗ | ✗ | ✗ | `--dry-run`、`route`、`reach` |

## 核心设计：转换图

每个转换器是图里的一条**有向边**（源格式 → 目标格式），引擎把所有边拼成图后，
用 Dijkstra 找**代价最小**的链路：

```bash
$ uniconvert route docx epub
路径：docx → html → epub
  1. docx → html  [document]  weight=1  quality=1
  2. html → epub  [document]  打包为 EPUB3  weight=1  quality=1
总代价=2  最低质量=1

$ uniconvert route json toml
路径：json → toml          # 数据族全连通，永远直连

$ uniconvert convert 视频.mp4 动图.gif --dry-run
[dry-run] 视频.mp4 (mp4 → gif) -> 动图.gif    # 先规划后执行，不产物
```

好处：

- **无直连也有路**：`docx → epub` 没有直连转换器，走 `docx→html→epub` 自动两步；
- **依赖感知**：缺 FFmpeg 时 MP4→GIF 规划期就被拦住，并给出安装命令，而不是运行到一半炸掉；
- **可解释**：`route` / `--dry-run` 把「为什么这样转、经过哪几步」摊开给你看；
- **防伪路径**：压缩流（gz 等）解压时会校验文件头与目标格式是否相符，
  `video.mp4.gz → x.gif` 这类错误立即报错，不会产出损坏文件。

## 支持格式一览（110+ 种）

| 大类 | 格式 |
|---|---|
| 数据 | json, yaml, toml, xml, ini, plist, jsonl, csv, tsv |
| 字幕 | srt, ass, ssa, vtt, lrc |
| 文档 | docx, doc, odt, pptx, ppt, odp, rtf, txt, md, html, tex |
| 电子书 | epub, mobi, azw3, fb2, cbz, cbr |
| 表格 | xlsx, xls, ods, csv, tsv |
| 压缩 | zip, 7z, tar, gz, bz2, xz, zst, rar |
| 图片 | jpg, png, webp, avif, heic, bmp, gif, tiff, ico, tga, psd, jp2, qoi, dds, svg |
| 音视频 | mp4, mkv, avi, mov, webm, flv, wmv, mpg, ts, 3gp, ogv, m3u8 / mp3, aac, flac, wav, ogg, opus, wma, aiff, alac, ape, m4b, amr, ac3, dts, mid |
| 3D | stl, obj, ply, gltf, glb, dae, 3mf, dxf |
| 通讯录 | vcf |
| 字体 | ttf, otf, woff, woff2 |
| 编码 | base64, hex, urlencode, quoted-printable, rot13 |
| 实用 | hash（md5/sha1/sha256/sha512/blake2b/crc32 + HMAC）、bin |

> 完整清单与每种的选项：`uniconvert formats` / `uniconvert options mp4`

## CLI 速查

```bash
uniconvert convert a.json a.yaml             # 文件 → 文件
uniconvert convert a.docx -t pdf             # 原地换格式
uniconvert convert a.jpg b.webp -o quality=92
uniconvert convert a.mkv out.mp4 -o profile=wechat        # 微信发送预设（720p 省流量）
uniconvert convert a.mkv out.mp4 -o profile=douyin        # 抖音竖屏预设（1080x1920 填充）
uniconvert convert a.mp4 b.mp4 -o speed=1.5 -o volume=-3  # 1.5 倍速 + 音量 -3dB
uniconvert convert a.mp4 stream.m3u8 -o hls_time=6        # 转 HLS 流（含分片）
uniconvert convert a.pdf txt -o pages=1-3 -o dpi=200
uniconvert convert subs.srt subs.ass -o shift=1.5         # 整体延后 1.5 秒
uniconvert convert a.json a.json -o sort_keys=true        # 同格式重排/美化
uniconvert convert data.csv data.json -o types=true       # 数字/布尔自动还原类型
uniconvert convert 旧编码.txt 新编码.txt -o from_encoding=gb18030
uniconvert convert a.json.gz a.json                       # 解压即转换
uniconvert convert model.stl model.obj                    # 3D 模型互转（零依赖）
uniconvert convert 通讯录.vcf 通讯录.csv                    # 手机通讯录转表格
uniconvert batch ./截图 -t webp --out ./压好/ -j 8 --on-exists rename --report report.json
uniconvert extract 漫画.cbz -d 输出目录/                    # 解压（防路径穿越）
uniconvert pack 文档/*.md -o 合集.zip --base 文档/          # 打包
uniconvert info 神秘文件.dat                                # 文件头体检（识别真身）
uniconvert reach srt                                        # 从 srt 出发能到哪些格式
uniconvert can mp4 gif && echo 可以转
uniconvert gui                                              # 图形界面
uniconvert web --port 8000                                  # 网页界面
```

**PDF 工具箱**（对标 Smallpdf / iLovePDF）：

```bash
uniconvert pdf merge a.pdf b.pdf -o 合集.pdf                 # 合并
uniconvert pdf split 报告.pdf --every 10 --out 拆分/          # 每 10 页一个文件
uniconvert pdf split 论文.pdf --pages 1-3,5 --out 分段/       # 按范围拆
uniconvert pdf rotate 扫描件.pdf -o 转正.pdf --angle 90       # 旋转（可指定页）
uniconvert pdf encrypt 机密.pdf -o 加密.pdf --password 123456 # 加密（AES-256）
uniconvert pdf decrypt 加密.pdf -o 明文.pdf --password 123456 # 解密
uniconvert pdf images 杂志.pdf --out 图片/ --min-size 20480   # 抽出内嵌图片
uniconvert pdf compress 扫描件.pdf -o 小体积.pdf --dpi 100 --quality 60  # 压缩
```

**图片工具**：

```bash
uniconvert image watermark photo.jpg out.jpg --text "禁止盗图" --pos br --opacity 40
uniconvert image watermark photo.jpg out.jpg --text "样张" --tile --rotate -30  # 平铺水印
uniconvert image collage 图1.jpg 图2.jpg 图3.jpg -o 拼图.jpg --mode grid --cols 2
uniconvert image collage *.png -o 竖长图.png --mode v      # 竖长图（聊天记录式）
uniconvert image compress 大图.jpg --target 500KB           # 压到微信/上传限制以内
```

**目录监控**（手机相册自动转 WebP 等）：

```bash
uniconvert watch ./手机备份相册 -t webp --out ./压缩相册 --interval 2
uniconvert watch ./收件箱 -t pdf --once        # 脚本里只扫一遍
```

**Windows 右键菜单**（对标格式工厂）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\install_context_menu.ps1
# 右键任意文件 → 用 UniConvert 转换 → 转为 PDF / PNG / WebP / MP3…
```

通用选项：`-o encoding=gb18030`（读写编码）、`-o newline=crlf`、`--on-exists rename|skip|overwrite|error`、`--dry-run`、`--retry N`。

## Python API

```python
from uniconvert import convert, convert_batch, route, list_formats, can_convert

r = convert("报告.docx", "报告.epub")     # 自动 docx→html→epub 两步走
print(r.status, r.route, r.output)

# 批量 + 并发 + 进度回调
results = convert_batch(
    [("a.json", "a.yaml"), ("b.csv", "b.json")],
    workers=4,
    on_progress=lambda e: print(f"{e.index}/{e.total} step {e.step}: {e.message}"),
)

# 先问再转
if can_convert("抽帧.png", "webp"):
    print(route("png", "webp"))
```

## 可选依赖（缺谁就少哪块能力）

| 依赖 | 解锁 | 安装 |
|---|---|---|
| FFmpeg | 音视频转码/GIF/抽帧/抽字幕轨 | `winget install Gyan.FFmpeg` |
| Pillow | 图片互转、图片→PDF | `pip install Pillow` |
| PyYAML | YAML 读写 | `pip install PyYAML` |
| pypdf + pypdfium2 | PDF 文本/页面渲染 | `pip install pypdf pypdfium2` |
| openpyxl | 写出 .xlsx | `pip install openpyxl` |
| LibreOffice | doc/ppt/xls ↔ pdf 等高保真转换 | 官网安装 |
| py7zr 或 7z.exe | 7z 归档读写 | `pip install py7zr` |
| fontTools + brotli | 字体互转 | `pip install fonttools brotli` |
| cairosvg/Inkscape | SVG → 位图 | `pip install CairoSVG` |
| Flask | 网页界面 | `pip install flask` |

一条命令全部体检：`uniconvert doctor`（只影响对应能力，核心永不依赖它们）。

## 打包为独立 exe（免装 Python，Windows）

```bash
pip install pyinstaller                # 一次即可
python packaging/build_exe.py          # 产物在 dist/，约 2~3 分钟
python packaging/build_exe.py --cli-only   # 只打命令行版
python packaging/build_exe.py --onedir     # 目录模式（启动更快）
```

产物：

| 文件 | 用途 | 大小 |
|---|---|---|
| `dist/UniConvert.exe` | 图形界面，双击即用（无控制台窗口） | ~20 MB |
| `dist/uniconvert-cli.exe` | 命令行，用法与 `python -m uniconvert` 完全一致 | ~20 MB |

说明：

- 两个 exe 名字必须区分大小写之外的形式——Windows 文件系统大小写不敏感，
  `UniConvert.exe` 和 `uniconvert.exe` 会互相覆盖，故 CLI 版名为 `uniconvert-cli.exe`；
- PyInstaller 只打包**构建机上已安装**的 Python 后端：构建前
  `pip install -e .[all]` 可让 exe 自带 Pillow/PyYAML/pypdf 等能力；
- **FFmpeg / 7z / LibreOffice 等外部程序不会进 exe**，运行时自动从 PATH 探测——
  目标机器装了就能用，`uniconvert-cli.exe doctor` 可体检。

## 插件：3 行扩展一个转换器

```python
# ~/.uniconvert/plugins/my_formats.py
from uniconvert.core import register

@register("csv", "reweek", group="myplugin", note="CSV→周报")
def csv_to_reweek(src, dst, opts):
    ...
    return dst
```

放好即生效（或设 `UNICONVERT_PLUGIN_PATH` 指向目录），`uniconvert plugins` 查看加载状态。
完整示例见 [`examples/plugin_reweek.py`](examples/plugin_reweek.py)。

## 测试

```bash
python -m unittest discover -s tests -v     # 208 个用例，零依赖
```

## 项目结构

```text
uniconvert/
├── core/             # 零依赖内核
│   ├── formats.py    #   格式世界地图（110+ 格式元数据 + magic）
│   ├── detect.py     #   文件头嗅探 / 文本特征识别
│   ├── registry.py   #   转换器注册表（边）
│   ├── graph.py      #   转换图 + Dijkstra 寻路 + 依赖诊断
│   ├── job.py        #   Job / Options / JobResult
│   └── engine.py     #   执行引擎（并发/重试/覆盖策略/进度）
├── converters/       # 内置转换器族
│   ├── data.py       #   JSON/YAML/TOML/XML/INI/CSV/plist/JSONL
│   ├── text.py       #   编码/Markdown/HTML/RTF/哈希
│   ├── subtitle.py   #   SRT/ASS/SSA/VTT/LRC
│   ├── archive.py    #   zip/tar/7z + gz/bz2/xz/zst + 解压/打包
│   ├── document.py   #   docx/odt/xlsx/ods/epub/fb2 + LibreOffice
│   ├── pdf.py        #   PDF 文本/渲染/生成
│   ├── image.py      #   Pillow + SVG 多后端
│   ├── media.py      #   FFmpeg 音视频
│   └── font.py       #   字体容器互转
├── api.py            # 公共 API
├── cli.py / gui.py / web.py   # 三个前端
└── plugins.py        # 插件加载

packaging/            # exe 打包（PyInstaller）
├── build_exe.py      #   一键构建脚本
├── gui_launcher.py   #   图形版入口
└── cli_launcher.py   #   命令行版入口
```

详细设计见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 路线图

- [x] PDF 合并/拆分/旋转/加密/解密/抽图/压缩（v1.1，Smallpdf 式）
- [x] 图片水印/拼接长图/压到指定大小（v1.1）
- [x] 音视频平台预设 + 变速/音量/淡入淡出 + HLS(m3u8)（v1.1）
- [x] 3D stl/obj/ply 互转、通讯录 vcf、目录监控、右键菜单（v1.1）
- [ ] OCR（Tesseract）：图片/扫描 PDF → 文本
- [ ] PDF 页码/水印/合并压缩包一键合并（PDF 工具箱增强）
- [ ] 视频水印（drawtext/overlay）、人声伴奏分离（Demucs）
- [ ] 批量命名规则模板（正则重命名 + 序号）
- [ ] CAJ → PDF（知网学术格式，对标格式工厂）
- [ ] 任务队列持久化与断点续跑；Web 端任务历史

## License

MIT
