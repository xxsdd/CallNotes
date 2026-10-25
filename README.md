# 通话笺 · CallNotes

Windows 上的录音、实时字幕与通话文字稿分析工具。把电脑里聊过的事情，变成可以查找、回听和追问的本地记录。

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

这是 **0.1.2 开发版源码快照**，当前运行环境为 Windows、Python 3.11、NVIDIA CUDA 与本地 faster-whisper large-v3。仓库提供源码和功能演示视频，尚未提供通用安装包或自动配置模型的安装向导。

实时字幕的延迟与识别质量取决于硬件和录音条件。多人重叠、背景人声、游戏术语与人名仍可能识别错误；声音分组不等于真实身份，也不保证自动对应微信或 Discord 昵称。

## 数据与分析

录音、文字稿和问答保存在本机。用户点击分析或提问时，相关文字资料通过本机已登录的 Codex 联网处理，使用该账户额度，原始录音不会作为分析附件上传。分析使用临时会话，本地保存同一文字稿版本的追问上下文。

本仓库不包含个人录音、真实文字稿、账号配置、模型权重或 Python 运行环境。视频中的会议内容与分析结果为虚构演示，等待过程有所省略。

## 开发环境启动

当前代码仍保留原开发环境的 D 盘约定。首次在其他电脑运行前，需要准备以下路径或调整对应源码常量：

| 内容 | 默认位置／入口 |
| --- | --- |
| 记录数据 | `D:\Apps\CallNotes-Data`，可用 `CALLNOTES_DATA` 环境变量覆盖 |
| 缓存、锁与临时文件 | `D:\Apps\CallNotes-Cache` |
| ASR Python | 在应用设置中指定；默认指向 `D:\Apps\VoiceStudio-Environment\project\.venv\Scripts\python.exe` |
| ASR 模型 | 在应用设置中指定包含 `model.bin` 的 faster-whisper large-v3 本地模型目录 |
| 可选说话人模型 | `D:\Apps\CallNotes\models\diarization` |
| 可选 CUDA DLL | `D:\Apps\CallNotes\runtime\cudnn8`；实际版本需匹配 ASR 环境 |

创建应用环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

应用界面的依赖与转写工作进程的依赖分开。ASR Python 环境需自行安装 `faster-whisper`、`av`、`numpy`、`opencc-python-reimplemented` 及与硬件匹配的 CUDA 运行库，并提前下载模型。可选说话人功能还需要 `sherpa-onnx`、`scipy`，以及 `segmentation.onnx` 和 `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`。模型按各自上游的许可获取，程序不会自动联网下载。

指定应用录音使用 Windows 进程音频捕获。源码在 `callnotes/native/process_loopback.cpp`；使用 Visual Studio C++ 与 Windows SDK 编译 `process_loopback.dll`，放在同一目录。`build.ps1` 中的编译器和 SDK 路径需按本机安装调整。该能力需要支持进程音频捕获的 Windows 版本（Windows 10 build 20348 或更新版本），建议 Windows 11。

Codex 分析需要本机安装并登录 Codex，且其 CLI/App Server 支持临时会话。未配置时仍可使用本地录音和文字稿功能。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

部分测试依赖 Qt、Windows 音频能力或可选聚类依赖；源码测试不等于完成所有设备上的真实长时间通话验证。

## 宣传素材署名

音乐为 Kevin MacLeod 的 **Inspired**，按 CC BY 4.0 使用。完整署名与剪辑说明见 [素材说明](docs/MEDIA-CREDITS.md)。图标采用用户提供角色参考制作的同人图案，第三方角色与商标权利归各自权利人。

