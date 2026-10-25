# 通话笺 · CallNotes

Windows 上的录音、实时字幕与通话文字稿分析工具。把电脑里聊过的事情，变成可以查找、回听和追问的本地记录。

[**下载 Windows 完整安装包 · v0.1.3**](https://github.com/xxsdd/CallNotes/releases/download/v0.1.3/CallNotes-Setup-0.1.3-Windows-x64.exe)

安装包约 **1.9 GB**，自带运行环境、字幕模型、说话人模型和 Codex CLI。无需安装 Python、Voice Studio 或手动配置路径。下载后双击安装，打开即可本地录音与转写；需要 AI 分析时，在 **设置 → AI 分析 → 登录 Codex** 中登录自己的账号。

适用于 Windows 64 位，推荐 Windows 11。安装目录约需 2.6 GB，录音另占空间。没有 NVIDIA/CUDA 环境也可使用 CPU；字幕速度取决于电脑性能，低性能电脑可能有明显延迟。当前安装包未作商业代码签名，校验值见 [SHA256SUMS.txt](https://github.com/xxsdd/CallNotes/releases/download/v0.1.3/SHA256SUMS.txt)。

[下载宣传视频](docs/callnotes-promo.mp4) · [更新日志](CHANGELOG.md)

https://github.com/user-attachments/assets/f13b27e5-c227-45a1-877c-51961f1a0849


## 已有功能

- 录制麦克风与电脑声音，也可选择指定应用的声音；暂停、继续和停止录音，最小化后继续录制。
- 本地增量字幕，支持中文、英文与中英混用，中文结果显示为简体。
- 导入已有音频或带音轨的视频，生成文字稿；默认先转写，点击“区分说话人”并指定人数后才运行说话人模型。
- 按连续发言合段，查找、编辑、删除片段、局部重新识别，以及查看和管理历史文字稿版本。
- 选择分析方向、Codex 模型与思考档位，整理话题或直接提问；点击按钮即提交文字资料。
- 分析来源可以切换、定位高亮并回听；纯阅读全屏、全局日间与夜间模式。
- 空格播放／暂停，左右方向键跳转五秒，0.5×–2× 播放速度。

## 当前状态

当前版本为 **0.1.3 Windows 安装版**。安装程序包含 Python、界面和音频依赖、large-v3-turbo 多语言字幕模型及本地说话人模型，普通使用者不需要运行下面的开发命令。首次加载模型需要准备时间。

实时字幕的延迟与识别质量取决于硬件和录音条件。多人重叠、背景人声、游戏术语与人名仍可能识别错误；声音分组不等于真实身份，也不保证自动对应微信或 Discord 昵称。

## 数据与分析

录音、文字稿和问答保存在本机。用户点击分析或提问时，相关文字资料通过本机已登录的 Codex 联网处理，使用该账户额度，原始录音不会作为分析附件上传。分析使用临时会话，本地保存同一文字稿版本的追问上下文。

源码仓库不包含个人录音、真实文字稿或账号配置。模型与运行环境放在 Releases 安装包中。视频中的会议内容与分析结果为虚构演示，等待过程有所省略。

## 开发环境启动

以下只供开发者使用。安装版会自动找到内置环境与模型；源代码运行也兼容原来的 D 盘开发环境：

| 内容 | 默认位置／入口 |
| --- | --- |
| 记录数据 | 新电脑：`%LOCALAPPDATA%\CallNotes\Data`；已存在的 `D:\Apps\CallNotes-Data` 继续使用。可用 `CALLNOTES_DATA` 覆盖 |
| 缓存、锁与临时文件 | `%LOCALAPPDATA%\CallNotes\Cache`，可用 `CALLNOTES_CACHE` 覆盖 |
| ASR 工作进程 | 安装目录下的 `worker\CallNotesWorker.exe`；源代码模式可在设置中指定 Python |
| 内置字幕模型 | 安装目录下的 `models\faster-whisper-large-v3-turbo`；可在设置中指定自己的模型 |
| 说话人模型 | 安装目录下的 `models\diarization` |

创建应用环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packaging/requirements-ui.txt -r packaging/requirements-worker.txt
.\.venv\Scripts\python.exe main.py
```

开发模式需要提前准备本地 faster-whisper 模型；说话人功能使用 `segmentation.onnx` 与 `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`。实际识别不联网下载模型。CPU 使用 int8 推理；GPU 运行需要与 CTranslate2 匹配的 CUDA/cuDNN，环境不可用时自动使用 CPU。完整安装包已包含普通使用所需的 CPU 环境与模型。

指定应用录音使用 Windows 进程音频捕获。源码在 `callnotes/native/process_loopback.cpp`；使用 Visual Studio C++ 与 Windows SDK 编译 `process_loopback.dll`，放在同一目录。`build.ps1` 中的编译器和 SDK 路径需按本机安装调整。该能力需要支持进程音频捕获的 Windows 版本（Windows 10 build 20348 或更新版本），建议 Windows 11。

完整安装包已附带 Codex CLI，但不会附带作者的账号或登录凭据。分析需要使用者自己的 Codex 登录；本地录音与字幕不需要登录。

打包脚本位于 [packaging](packaging)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

部分测试依赖 Qt、Windows 音频能力或可选聚类依赖；源码测试不等于完成所有设备上的真实长时间通话验证。

## 宣传素材署名

音乐为 Kevin MacLeod 的 **Inspired**，按 CC BY 4.0 使用。完整署名与剪辑说明见 [素材说明](docs/MEDIA-CREDITS.md)。图标采用用户提供角色参考制作的同人图案，第三方角色与商标权利归各自权利人。

