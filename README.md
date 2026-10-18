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
| **PDF 工具箱**（合并/拆分/旋转/加密/解密/抽图/压缩/页码/水印/**删页重排倒序/清空白页/封面/元数据/长图/目标压缩/裁剪**） | ✅ | ✅ | ✅ | ✅ | pypdf 后端，中文水印为矢量文本，`uniconvert pdf -h` |
| **OCR**（图片/扫描 PDF → 文本，中英混排） | ✅ | ✅ | ✅ | ✅ | 需 Tesseract，`uniconvert ocr 扫描件.pdf` |
| **漫画**（CBZ/CBR/CBT/CB7 → PDF，PDF → CBZ） | ✅ | 部分 | ✗ | 部分 | 自然阅读序/rtl/ComicInfo.xml |
| **邮件**（EML → HTML/TXT/PDF，附件导出） | ✅ | ✗ | ✗ | 部分 | cid 图片内联；`eml-att` 导附件 |
| **DOCX 写出**（md/txt/html → docx，零依赖） | ✅ | ✅ | ✅ | ✅ | 顺带开通 srt→docx、epub→docx、pdf→docx 链路 |
| **MOBI/AZW3 → 文本** | ✅ 实验性 | ✗ | ✗ | ✅ | PalmDOC 解压（Huff 变体明确报错） |
| **图片工具**（水印/拼接/压到指定大小/**翻转/DPI/亮度对比度饱和度锐化伽马/矢量化/favicon 全家桶**） | ✅ | ✅ | ✗ | 部分 | potrace 真矢量化优先，内嵌兜底 |
| Office 文档提取（docx/odt/pptx/xlsx/ods→txt/md/html/csv…） | ✅ 内置解析 | ✅ | ✅ | ✅ | **零依赖**（OOXML/ODF 即 ZIP+XML） |
| **CAJ → PDF**（知网文献） | ✅ 提取内嵌 PDF | ✅ | ✗ | ✗ | 复杂变体提示用 caj2pdf |
| 电子书（epub/fb2→txt/html/md，html→epub） | ✅ | ✗ | ✗ | 部分 | **零依赖**，Calibre 风格 |
| 压缩包重打包（zip/tar/7z/cbz）+ gz/bz2/xz/zst | ✅ 流式大文件 | ✅ | ✗ | ✅ | 7z 需 py7zr 或 7z.exe |
| 图片转换（jpg/png/webp/bmp/gif/tiff/ico…） | ✅ | ✅ | 部分 | ✅ | 需 Pillow；SVG 栅格化另有多后端 |
| 图片 ⇄ SVG | ✅ 内嵌 SVG **零依赖** | ✗ | ✗ | 部分 | SVG→位图需 cairosvg/inkscape 等 |
| 音视频转码 / GIF / 抽帧 / 抽字幕轨 | ✅ + **平台预设** | ✅ | ✗ | ✅ | 需 FFmpeg；`profile=wechat/douyin` |
| **视频/音频拼接、封装字幕、替换音轨、响度归一、消人声（相位）、变调、降噪、倒放、声道、目标大小二压、抽帧序列、裁剪镜像、铃声制作** | ✅ | ✅ | ✗ | 部分 | `uniconvert concat / ringtone / frames`；`mux_sub/replace_audio/loudnorm/karaoke/target_size` |
| **TTS 文字转语音**（系统引擎 → WAV/MP3） | ✅ | ✅ | ✗ | ✗ | `uniconvert tts "你好" -o hi.wav` |
| **URL 直接转换**（Aconvert 式） | ✅ | 部分 | ✗ | ✅ | `uniconvert convert https://…/a.png a.webp` |
| **字体子集化**（网页字体缩 90%+） | ✅ | ✗ | ✗ | 部分 | `uniconvert font-subset font.ttf out.woff2 --text-file 常用字.txt` |
| **WPS 金山系**（.wps/.et/.dps 经 LibreOffice） | ✅ | ✅ | ✗ | ✗ | 格式表 + 转换边自动寻路 |
| **视频水印**（文字/图片、九宫格/平铺透明度） | ✅ | ✅ | ✗ | 部分 | `wm_text=样张 wm_pos=br` |
| 视频变速/音量/淡入淡出/HLS(m3u8) | ✅ | 部分 | ✗ | 部分 | `speed=1.5 volume=-6`，`-t m3u8` |
| **人声/伴奏分离** | ✅ 需 Demucs | ✅ | ✗ | ✗ | `uniconvert separate 歌曲.mp3` |
| PDF：→ txt / → 图片 / 图片→PDF | ✅ | ✅ | ✅ | ✅ | 多后端降级（pypdf/PyMuPDF/poppler） |
| 3D 模型互转（stl/obj/ply） | ✅ **零依赖** | ✗ | ✗ | 部分 | 三角面几何转换 |
| 通讯录（vcf ⇄ csv/json） | ✅ **零依赖** | ✗ | ✗ | 部分 | 手机通讯录导出转换 |
| 批量 + 并发 + 重试 + 覆盖策略 | ✅ | ✅ | 部分 | ✅ | `-j 8 --retry 1 --on-exists skip` |
| **批量重命名**（正则 + 模板 + 序号） | ✅ 两阶段防链式覆盖 | 部分 | ✗ | ✗ | `uniconvert rename IMG_* --find ... --replace ...` |
| **任务队列持久化 + 断点续跑** | ✅ SQLite | ✗ | ✗ | API | `queue add → queue run`（可反复跑） |
| **目录监控自动转换** | ✅ 零依赖轮询 + 日志 | 部分 | ✗ | ✗ | `uniconvert watch` |
| **Web 实时进度（SSE）+ 任务历史** | ✅ | ✗ | ✗ | ✅ | `/api/tasks` + `/api/tasks/<id>/events` |
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
uniconvert pdf merge a.pdf b.pdf -o 合集.pdf --compress      # 合并 + 压缩一条龙
uniconvert pdf split 报告.pdf --every 10 --out 拆分/          # 每 10 页一个文件
uniconvert pdf split 论文.pdf --pages 1-3,5 --out 分段/       # 按范围拆
uniconvert pdf rotate 扫描件.pdf -o 转正.pdf --angle 90       # 旋转（可指定页）
uniconvert pdf encrypt 机密.pdf -o 加密.pdf --password 123456 # 加密（AES-256）
uniconvert pdf decrypt 加密.pdf -o 明文.pdf --password 123456 # 解密
uniconvert pdf images 杂志.pdf --out 图片/ --min-size 20480   # 抽出内嵌图片
uniconvert pdf compress 扫描件.pdf -o 小体积.pdf --dpi 100 --quality 60  # 压缩
uniconvert pdf number 报告.pdf -o 带页码.pdf                  # 加页码（1 / 3 样式）
uniconvert pdf watermark 投标.pdf -o 盖章.pdf --text "严禁外传" --opacity 20  # 中文矢量水印
```

**OCR / 人声分离 / CAJ / 重命名 / 队列**：

```bash
uniconvert ocr 截图.png                        # 中英混排识别 → 截图.txt
uniconvert ocr 扫描合同.pdf -o dpi=300          # 扫描版 PDF 逐页识别
uniconvert convert 病历扫描.png 病历.txt        # OCR 也走转换图（装了 Tesseract 后）
uniconvert separate 歌曲.mp3 --out 伴奏/        # 人声/伴奏分离（需 Demucs）
uniconvert convert 论文.caj 论文.pdf            # 知网 CAJ → PDF（提取内嵌）
uniconvert rename IMG_*.jpg --find "IMG_(\d+)" --replace "旅行-{1}" --apply
uniconvert rename 素材/*.png --replace "wall-{n}" --start 1 --pad 3 --apply  # 纯序号
uniconvert queue add 素材/*.png -t webp --out 输出/   # 入队（先在计划里排队）
uniconvert queue run -j 8                      # 执行/续跑（进程被杀也不丢）
uniconvert queue list                          # 看进度
```

**图片工具**：

```bash
uniconvert image watermark photo.jpg out.jpg --text "禁止盗图" --pos br --opacity 40
uniconvert image watermark photo.jpg out.jpg --text "样张" --tile --rotate -30  # 平铺水印
uniconvert image collage 图1.jpg 图2.jpg 图3.jpg -o 拼图.jpg --mode grid --cols 2
uniconvert image collage *.png -o 竖长图.png --mode v      # 竖长图（聊天记录式）
uniconvert image compress 大图.jpg --target 500KB           # 压到微信/上传限制以内
```

**字幕**（9 格式互通 + 双语合并 + 缩放/HI 去除）：

```bash
uniconvert convert subs.srt subs.sbv             # SRT⇄MicroDVD/TTML/SBV/SAMI/ASS/VTT/LRC
uniconvert convert subs.srt subs.srt -o scale=25/23.976   # 帧率转换（等比缩放时间轴）
uniconvert convert subs.srt clean.srt -o remove_hi=true    # 去 [听力障碍] 标注
uniconvert subtitle merge 中文.srt 英文.srt -o 双语.srt --mode bilingual
uniconvert subtitle merge a.srt b.srt -o 合集.srt --mode concat
```

**漫画 / 邮件 / 文档 / MOBI**：

```bash
uniconvert convert 漫画.cbz 漫画.pdf -o rtl=true   # 日漫从右往左
uniconvert convert 杂志.pdf 杂志.cbz               # 逐页 JPEG + ComicInfo.xml
uniconvert convert 发票.eml 发票.pdf               # 邮件转 PDF（含 CID 内联图）
uniconvert eml-att 发票.eml --out 附件/             # 导出邮件附件
uniconvert convert 笔记.md 笔记.docx               # 零依赖 DOCX 写出
uniconvert convert kindle.mobi 书.txt              # MOBI 解出纯文本
```

**PDF 增强**：

```bash
uniconvert pdf pages 报告.pdf -o 整理.pdf --delete 1,5-8 --reverse
uniconvert pdf blank 扫描件.pdf -o 去空白.pdf       # 删除空白页
uniconvert pdf cover 论文.pdf -o 带封面.pdf --cover-text "毕业论文"
uniconvert pdf meta 报告.pdf --set title=年报 --set author=张三 -o 改元数据.pdf
uniconvert pdf meta 报告.pdf --get                   # 查看元数据
uniconvert pdf longpic 合同.pdf -o 合同长图.jpg       # 拼成长图发微信
uniconvert pdf compress 扫描件.pdf -o 小.pdf --target 5MB   # 压到指定大小
uniconvert pdf crop 书页.pdf -o 裁边.pdf --margins 20
```

**媒体增强 / 铃声 / TTS / 其他**：

```bash
uniconvert concat 片段1.mp4 片段2.mp4 -o 合集.mp4      # 拼接（同编码流拷贝）
uniconvert convert 电影.mp4 带字幕.mkv -o mux_sub=中字.srt   # 字幕封装
uniconvert convert 视频.mp4 新版.mp4 -o replace_audio=新配音.mp3
uniconvert convert 视频.mp4 微信版.mp4 -o target_size=25MB   # 微信 25MB 二压
uniconvert convert 歌曲.mp3 伴奏.mp3 -o karaoke=true         # 消人声（相位抵消）
uniconvert convert 录播.mp4 响度.mp4 -o loudnorm=true        # 响度归一 -16 LUFS
uniconvert convert 视频.mp4 out.mp4 -o pitch=2 -o denoise=true -o channels=mono
uniconvert ringtone 歌曲.mp3 --out 铃声/ -o start=1:05 duration=30   # M4R+MP3
uniconvert frames 视频.mp4 --out 帧/ -o fps=1         # 按秒抽帧
uniconvert tts "欢迎使用 UniConvert" -o 欢迎.wav       # 系统语音合成
uniconvert font-subset 字体.ttf 网页字体.woff2 --text-file 站内常用字.txt
uniconvert image favicon logo.png --out site/          # favicon 全家桶
uniconvert convert https://example.com/a.png b.webp    # URL 直转
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
| FFmpeg | 音视频转码/GIF/抽帧/抽字幕轨/视频水印 | `winget install Gyan.FFmpeg` |
| Tesseract | OCR 图片/扫描 PDF → 文本 | `winget install UB-Mannheim.TesseractOCR` |
| Demucs | 人声/伴奏分离 | `pip install demucs` |
| Pillow | 图片互转、图片→PDF、水印/拼接/压缩 | `pip install Pillow` |
| PyYAML | YAML 读写 | `pip install PyYAML` |
| pypdf + pypdfium2 | PDF 工具箱（合并/拆分/页码/水印…） | `pip install pypdf pypdfium2` |
| openpyxl | 写出 .xlsx | `pip install openpyxl` |
| LibreOffice | doc/ppt/xls ↔ pdf 等高保真转换 | 官网安装 |
| py7zr 或 7z.exe | 7z 归档读写 | `pip install py7zr` |
| fontTools + brotli | 字体互转 | `pip install fonttools brotli` |
| cairosvg/Inkscape | SVG → 位图 | `pip install CairoSVG` |
| Flask | 网页界面（含 SSE 实时进度） | `pip install flask` |

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
python -m unittest discover -s tests -v     # 303 个用例，零依赖
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

- [x] PDF 合并/拆分/旋转/加密/解密/抽图/压缩/页码/水印（v1.1~1.2）
- [x] 图片水印/拼接长图/压到指定大小（v1.1）
- [x] 音视频平台预设 + 变速/音量/淡入淡出 + HLS(m3u8)（v1.1）
- [x] 3D stl/obj/ply 互转、通讯录 vcf、目录监控、右键菜单（v1.1）
- [x] OCR（Tesseract）、视频水印、人声分离（Demucs）、CAJ→PDF（v1.2）
- [x] 批量命名模板（正则+序号）、任务队列持久化/断点续跑（v1.2）
- [x] Web 实时进度（SSE）+ 任务历史（v1.2）
- [ ] PDF 转 Word/PPT/Excel（版式重建，pdf2docx 集成）
- [ ] 视频去水印（区域擦除）、屏幕录制
- [ ] 扫描 PDF → 可搜索 PDF（OCR 字层回嵌）
- [ ] 分片上传/断点续传（Web 大文件）、多人并发配额

## License

MIT
