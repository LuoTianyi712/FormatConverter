# 格式转换工具市场调研（饱和式）与差距对照

> 方法：多轮持续搜索市面格式转换工具，记录**新工具**与**新功能点**；
> 同时**对每个准备实施的技术方案做独立验证**（规范/算法/官方文档），
> 验证不通过的不实施。连续 3 轮（R6-R8）未再出现新工具类别与
> 新工程能力（仅长尾格式清单），判定**饱和**。

## 一、轮次记录（工具与功能发现）

### R1 · 综合盘点（中英）
- **新工具**：Filee（244 格式/17 类，光标拖拽圆环、右键菜单、按需下载引擎）、
  飞鼠格式 FlyingMouse（内置 FFmpeg/LibreOffice/Poppler/Tesseract/Pandoc，
  PDF→Excel 表格提取、PDF→Word 版式引擎、RAW/dcraw、TTS、中英 UI）、
  XMedia Recode、Epubor、VidCoder、LosslessTunes、鲲穹 vcf 转换器、
  How to Convert（6098 种转换/邮件 113 种子类）、Xonvert（浏览器本地，
  字体子集化）、Cove Universal Converter（pypdf+pandoc 链路）、Pandoc、
  HandBrake、XnConvert、Aconvert、Doxillion、Freemake、Shutter Encoder、
  Converseen、MConverter、API2Convert、ConvertAPI、AnyToISO、Spin 3D、
  Express Rip/Zip、CoolUtils（1400+ 格式）、Ghostscript、ImageMagick、fre:ac…
- **新功能点**：位图**真矢量化**（trace→SVG）、**邮件格式**（EML/MSG/PST）、
  **漫画**（CBZ/CBR）、**字体子集化**、3D→GLB/glTF、CD/DVD 抓取、
  HWP 韩文格式、KFX 电子书。

### R2 · 工具横切（123apps / Shutter Encoder / XnConvert / potrace）
- **新功能点**：**视频→图片序列**（按帧率抽帧）、**目标文件大小**（2-pass 压到指定 MB）、
  **响度归一化**（LUFS）、replace audio、**字幕封装(embed)与压制(burn)**、
  视频裁剪/镜像/2-pass、**音频转文字 Whisper**（AI）、音频分离 Demucs（已支持）、
  图片：**无损 JPEG 旋转**、80+ 链式动作（亮度/对比度/伽马/锐化/模糊/浮雕）、
  元数据编辑/清除（EXIF/IPTC/XMP/GPS）、监folder 自动转换、
  potrace（BMP/PNM→SVG/PDF/EPS/DXF 等，GPL 工具，算法=像素→平滑曲线）。

### R3 · 细分品类（PDF 大全 / 邮件 / 漫画 / TTS）
- **新功能点**：**PDF→长图**（多页竖向拼成一张，DPI 可调）、
  **删页/页面重排/倒序/奇偶页分离/空白页清理/插空白页/加封面**、
  **元数据查看/清理**、n-up 拼版（2合1/4合1）、可搜索 PDF（OCR 字层）、
  PDF 签名、**EML/MSG→PDF/HTML（内嵌图片 CID→data URI、附件清单、取证头）**、
  **cbz/cbr/cb7/cbt→PDF（按阅读顺序，一图一页，JPEG/PNG 原样嵌入）**、
  **PDF→CBZ（逐页渲染 JPEG + ComicInfo.xml）**、**TTS 文字→WAV（系统 SAPI 引擎）**。

### R4 · 字幕 / 电子书 / 图标 / 音频（Subtitle Edit / calibre / favicon / 音频工具集）
- **新功能点**：字幕 300+ 格式（MicroDVD/TTML/iTT/SCC/SAMI/EBU-STL…）、
  **双语字幕合并**、帧率转换、强制对齐、HI 听力障碍标注去除、连行合并、
  DVD/蓝光字幕 OCR、导出 PNG/bdn；
  电子书输入矩阵（AZW/AZW3/AZW4/CB7/CHM/DJVU/LIT/PDB/PML/RB/SNB/TCR…）、
  **MOBI/PalmDOC（压缩 1/2/17480）**、KEPUB；
  **favicon 全家桶**（多尺寸 ICO 16-256 + 独立 PNG + webmanifest + HTML 片段）；
  音频 42 件套：**铃声制作（M4R/MP3，30s+淡入淡出）**、**倒放**、
  **静音删除**、**降噪(频谱门限)/电流声陷波(50/60Hz)**、响度标准化(BS.1770)、
  **变调（保速 WSOLA / 变速保调）**、3 段 EQ/低音增强/混响/8D、
  **中置消音去人声（相位抵消，非 AI）**、单双声道处理。

### R5 · 压缩 / 3D-CAD / 目标大小 / WPS
- **新功能点**：AES-256 加密压缩（ZIP/7z）、**分卷压缩**、自解压 EXF/WIM/ISO 等；
  3D 转换矩阵（STL/OBJ/PLY/GLB/glTF/3MF/DAE/FBX/STEP/IGES/USDZ…，
  「tessellated/lossless/approximated」分级）；
  **微信 25MB 压制公式**：总码率 = 目标字节×8÷时长，视频码率 = 总码率 − 音频码率，
  2-pass，目标留 2~4% 余量；
  **WPS 金山系格式**（.wps/.et/.dps/.ett/.wpt/.dpt）与 OFD。

### R6 · 技术方案验证轮（无新工具）
逐项验证见下表（二、技术验证记录），本轮机内全部通过，无新工具。

### R7 · UI / 自动化深挖（File Juggler / PixieSwitch / Duckling / TinyWow）
- **UI 功能点**：设置持久化、内置偏好预设（Web 优化/归档/社交）、
  键盘快捷键（Ctrl+O/Del/Ctrl+Enter）、图片缩略图预览、转换统计
  （成功/失败/耗时/字节）、深色主题、历史面板、规则化自动化
  （条件：文件名/内容/大小/日期 → 动作：转换/移动/重命名/执行命令）。
  TinyWow：200+ 工具集中搜索、一键即用。

### R8 · 穷尽复核（ToolPure / ConvertX / Filee 格式目录 / ezyzip / Keynou）
- 新发现仅为**长尾格式**：视频 VOB/MOD/DV/RM(RMVB)/SWF/WTV/CAVS、
  音频 AU/CAF/WavPack/TTA/W64/DSD/Musepack/TAK/Speex、
  字幕 SAMI(.smi)/SCC；多卷归档合并、CRX→ZIP 等长尾工具。
- **连续第 3 轮（R6/R7/R8）无新工具类别与工程级新能力 → 饱和。**

## 二、技术方案验证记录（实施前先验，全部通过）

| 方案 | 结论 | 依据 |
|---|---|---|
| MicroDVD `.sub` | `{start}{end}text` 帧格式；首行 `{1}{1}fps` 声明帧率（默认 25）；`\|` 换行；`{y:i}`/`{c:$BBGGRR}`（BGR）标签 | MicroDVD 规范/Wikipedia/subtitld |
| PalmDOC 解压（MOBI） | 0x01-08=复制 N 字面量；09-7F/00=字面量；80-BF=双字节 LZ77（11bit 距离+3bit 长度，长度+3）；C0-FF=空格+字节^0x80；块 4096；压缩标记：1=无/2=PalmDOC/0x4448=Huff(放弃) | calibre format_docs + KindleUnpack |
| ffmpeg 消人声 | `pan=stereo\|c0=c0-c1\|c1=c1-c0`（相位抵消，仅立体声，会连带削弱中置鼓/贝斯——需在文档注明局限） | StackOverflow 采纳答案 + karaoke-toolkit |
| ffmpeg 响度归一 | `loudnorm=I=-16:TP=-1.5:LRA=11` | ffmpeg 官方滤镜文档（BS.1770） |
| 目标大小 2-pass | 总码率=目标字节×8÷时长；视频码率=总−音频；2% 安全余量；`-pass 1/2` | ffmpeg-video-filesize 项目 + 小丸工具箱教程 |
| potrace 矢量化 | CLI：`potrace -b svg -o out.svg in.bmp`（输入 BMP/PNM，需灰度/二值预处理） | potrace 官方手册 |
| PDF 叠加（页码/水印） | pypdf merge_page + 独立 writer；pypdf6 `NumberObject` 浮点截断→`FloatObject`；中文用 STSong-Light+UniGB-UCS2-H | 本项目实践 + pypdf 文档 |
| cbz→pdf | 一图一页按文件名序；JPEG/PNG 原样嵌入、其他重编码；跳过 `__MACOSX`/`.DS_Store` | SumatraPDF / cb2pdf / useotools |
| DOCX 最小写入 | ZIP：`[Content_Types].xml`+`_rels/.rels`+`word/document.xml`(+styles) | OOXML 规范（ECMA-376 最小集） |
| EML→HTML | Python `email` 标准库；`cid:` 图片转 base64 data URI 内联；头信息表 | iTextMaster/PDFengine 描述的做法 |
| EML 附件/取证头 | 附件清单+逐个导出；可选完整 Received 链 | MailToPDF（确认需求真实存在） |

## 三、与本工程差距对照（调研后 → 实施清单）

✅=原有 ｜ 🆕=本轮新增 ｜ ⏭=明确排除（原因）

| 能力 | 现状 | 本轮 |
|---|---|---|
| PDF 合并/拆分/旋转/加密/解密/抽图/压缩/页码/水印 | ✅ v1.1-1.2 | |
| PDF 删页/重排/倒序/奇偶分离/清空白页/加封面/元数据/长图/目标压缩/裁剪 | ✖ | 🆕 全做 |
| 图片格式矩阵/水印/拼接/目标大小/GIF | ✅ | |
| 图片 翻转/DPI/亮度对比度饱和度锐化伽马/EXIF清除 | 部分 | 🆕 补 |
| 位图→真 SVG（potrace） | 仅内嵌 | 🆕 potrace 优先，内嵌兜底 |
| favicon 全家桶（ICO多尺寸+PNG套+manifest+HTML） | 仅 ICO | 🆕 做 |
| 字幕 SRT/ASS/SSA/VTT/LRC | ✅ | |
| 字幕 MicroDVD/TTML/SBV/SAMI + 帧率缩放 + HI 去除 + 双语合并 | ✖ | 🆕 做 |
| 漫画 CBZ/CBR→PDF、PDF→CBZ | ✖ | 🆕 做 |
| 邮件 EML→HTML/TXT/PDF（CID 内联） | ✖ | 🆕 做（MSG/PST/OST 仅记录） |
| docx/pptx/xlsx/odt/epub 解析 | ✅ | |
| txt/md/html→DOCX（最小 OOXML 写入） | ✖ | 🆕 做（顺带开通 srt→docx、epub→docx、pdf→docx 链路） |
| MOBI/AZW→txt/html（PalmDOC） | ✖ | 🆕 做（Huff 压缩变体明确报错） |
| WPS 系（wps/et/dps/ett/wpt/dpt）经 LibreOffice | ✖ | 🆕 格式表+边 |
| 3D STL/OBJ/PLY | ✅ | |
| 3D→glTF/GLB 导出 | ✖ | 🆕 做（基础三角面+法线省略） |
| 视频转码/GIF/预设/变速/音量/淡入出/HLS/进度 | ✅ | |
| 视频合并(concat)/调换音轨(replace audio)/封装字幕(mux) | ✖ | 🆕 做 |
| 视频目标大小(2-pass，微信25MB)/抽帧序列/裁剪/镜像 | ✖ | 🆕 做 |
| 音频 倒放/降噪/响度归一/变调/声道/低音/静音删除/消人声(相位) | ✖ | 🆕 做 |
| 铃声制作（M4R/MP3，30s+淡化） | ✖ | 🆕 做 |
| 人声分离 Demucs | ✅ | |
| OCR/PDF 页码水印/视频水印/队列/watch/右键菜单/插件 | ✅ | |
| 字体互转 | ✅ | 🆕 +子集化命令 |
| TTS（系统 SAPI → WAV，可选 ffmpeg 转 MP3） | ✖ | 🆕 做（Windows） |
| URL 直接输入转换 | ✖ | 🆕 做 |
| 加密压缩（ZIP AES/7z 密码）/分卷 | ✖ | ⏭ pyzipper 等可选依赖过多，记录至 v1.4 路线图 |
| DVD/CD 抓取、屏幕录制、视频下载 | ✖ | ⏭ 非格式转换核心（硬件/网络依赖），路线图记录 |
| 可搜索 PDF（OCR 字层）/PDF 签名/PDF n-up | ✖ | ⏭ 复杂度高，路线图 |
| AI 系（白描上色/去背景/超分/翻译/说话人转写 Whisper） | ✖ | ⏭ 模型依赖，路线图（转写可用 faster-whisper 可选集成） |
| RAW 相机文件（dcraw） | ✖ | ⏭ 记录（Pillow 无 RAW；dcraw 外部依赖） |
| CHM/DJVU/ICNS/HWP/KFX 等长尾 | ✖ | ⏭ 记录 |
| UI：每文件耗时/大小列、设置持久化、预设、快捷键、失败重试、打开输出目录、缩略图预览 | 部分 | 🆕 本轮改进 |
| UI：深色主题、历史面板 | ✖ | ⏭ tkinter 成本高，路线图（Web 端已有历史） |
