# AMLL Lyrics Skill

面向 AI 编程助手的 AMLL 歌词制作流程，配套可单独运行的 Python 工具。覆盖查歌、版本核对、数据库查重、素材准备、模型强制对齐、演唱音节拆分、背景人声与对唱、TTML 校验及投稿。

**这个 skill 用于辅助用户快速制作逐字／逐音节、背景人声、对唱等高级歌词，降低制作门槛，推动社区贡献。它提供制作辅助，不对最终歌词质量作保证，也不能代替用户核对或社区审核。**

> **提交前请仔细核对。** 请使用对应版本的原音频，从头到尾试听并逐句检查歌词原文、译文、起止时间、音节拆分、伴唱／对唱及歌曲元数据。模型结果可能错位、漏词或误判声部；“格式校验通过”不等于内容正确、听感对齐或符合全部投稿要求。确认成品准确、完整且播放正常后再提交，最终稿由提交者负责。

**模型生成候选时间，人根据真实唱法复核。** 未完成用户最终复核的文件应标为“待复核稿”，不能直接推送。本项目不会仅凭模型分数声称逐字对齐，也不会在用户只要求预览时自动投稿。

这是社区项目，与 AMLL 官方没有隶属关系。MIT 许可证适用于这里的代码和原创文档；音乐、歌词、模型权重及第三方依赖仍遵循各自的权利和许可证。

## 安装 skill

从 [MelioraPlayer/make-amll-lyrics](https://github.com/MelioraPlayer/make-amll-lyrics) 获取源码，或使用已打包的 ZIP。将包含 `SKILL.md` 的目录命名为 `amll-lyrics`，放到助手支持的 skill 目录中，确保 `SKILL.md` 位于该目录第一层；GitHub 的源码 ZIP 解压目录需相应重命名。

Codex 当前支持用户级 `~/.agents/skills/amll-lyrics/` 和项目级 `.agents/skills/amll-lyrics/`。已有同名 skill 时先备份，避免同时安装多份；已有自定义 skill 路径的环境沿用该环境配置。安装位置与调用方式见 [OpenAI 官方文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。

典型请求：

```text
使用 $amll-lyrics，检查这份 TTML，保留我改好的时间和译文，只修正结构问题。
```

```text
使用 $amll-lyrics，根据我提供的音频和歌词制作逐音节 TTML，先给我预览。
```

使用该 skill 的助手需要能读取本地文件和运行命令；投稿还需要已登录的 GitHub CLI 与可操作的 GitHub 网页。脚本本身不调用 LLM API，不需要 OpenAI API Key。其他助手可加载 `SKILL.md`，但具体工具和安装路径由宿主决定。

## 先跑一个离线示例

以下命令在解压后的仓库根目录运行，需要 Python 3.11+，推荐 3.12；可用 [uv](https://docs.astral.sh/uv/) 管理环境。

```shell
uv run --python 3.12 python scripts/ttml_tools.py export examples/reviewed.json work/example.ttml
uv run --python 3.12 python scripts/ttml_tools.py check work/example.ttml --duration 12
uv run --python 3.12 python -B -m unittest discover -s tests -v
```

示例是合成文本与虚构元数据，展示词内无空格的 `a` / `wake`、背景先于主句、背景译文及两个 agent。**不要把示例提交到 AMLL。** 再次导出到同一路径需要 `--force`。

## 依赖按需安装

| 任务 | 依赖 |
| --- | --- |
| LRC 解析、TTML 导出／检查、离线测试 | Python 标准库 |
| 音频下载验证、重采样 | PATH 中的 `ffprobe`、`ffmpeg` |
| 英文 CTC／MMS 对齐 | `requirements/alignment.txt` |
| 中文时间戳候选 | `requirements/chinese.txt` |
| 人声分离 | `requirements/separation.txt` |
| 数据库查询、投稿后核验 | 已登录的 `gh`；新建 Issue 默认走网页模板 |
| 官方解析器复核 | Rust/Cargo，按当前 AMLL 仓库版本配置 |

模型环境推荐 Python 3.12，首次运行会下载较大的权重。使用独立环境，别直接升级已有环境中的 TorchAudio。此实现依赖 TorchAudio 2.8 的 `forced_align`，该 API 从 2.9 起移除。[官方说明](https://docs.pytorch.org/audio/2.8/tutorials/forced_alignment_tutorial.html)

Windows PowerShell，创建英文 CPU 环境：

```powershell
uv venv --python 3.12 .venv-amll
uv pip install --python .venv-amll/Scripts/python.exe torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv-amll/Scripts/python.exe -r requirements/alignment.txt
$amllPy = (Resolve-Path '.venv-amll/Scripts/python.exe').Path
& $amllPy scripts/align_lyrics.py --help
```

macOS／Linux：

```sh
uv venv --python 3.12 .venv-amll
uv pip install --python .venv-amll/bin/python -r requirements/alignment.txt
.venv-amll/bin/python scripts/align_lyrics.py --help
```

Linux 若只需 CPU，可先按上面的官方 CPU 索引安装相同版本的 torch 和 torchaudio，再安装依赖文件。CUDA 环境按 [PyTorch 官方版本表](https://pytorch.org/get-started/previous-versions/) 安装匹配的一对 wheel。模型脚本支持 `cpu`、`cuda`、`auto`；没有实现 MPS 后端。可选依赖没有承诺覆盖所有系统／设备，CI 主要验证不依赖模型的工具行为。

中文或分离功能分别安装相应 requirements 文件；不要用后续 `uv sync` 清理这个手动安装了模型依赖的独立环境。

## 音乐来源

默认使用用户已有的音频与歌词。本项目没有内置的 Meting 服务，也不附带歌曲下载权限。服务可以通过 `--base` 参数或 `AMLL_METING_BASE` 环境变量提供；后者由进程环境读取，不自动加载 `.env`。

PowerShell 交互示例：

```powershell
$env:AMLL_METING_BASE = Read-Host '输入你有权使用的 Meting API 完整地址'
$amllQuery = Read-Host '输入曲名与歌手'
uv run --python 3.12 python scripts/fetch_meting.py search --query $amllQuery --out work/song/search.json
$amllTrackId = Read-Host '核对版本后，输入歌曲 ID'
uv run --python 3.12 python scripts/fetch_meting.py fetch --id $amllTrackId --out work/song/source
```

不同 Meting 部署支持的参数和响应结构有差异；搜索功能需服务支持。`--lyrics-only` 只取歌词。音频必须通过 ffprobe 验证，失败下载不会替换原文件。服务返回的签名地址、响应正文、音频与歌词均保存在本地任务目录，不应加入源码仓库。

## 制作流程

1. 查询歌手、专辑、版本、ID 和时长；搜索数据库、全部状态 PR／Issue 与制作占位。
2. 保存原始素材和音频 hash。运行 `prepare_audio.py`，人工区分信用信息、译文与实际唱词，并复核行窗口。
3. 对核对后的 `units` 运行 `align_lyrics.py`。英文可用 CTC／MMS 交叉比较，中文用 FunASR；必要时分离人声。
4. 根据实际唱法拆清晰的多音节词，复核首辅音、长音与换气。模型字符投影只是切点候选。
5. 设置 `x-bg` 与稳定的 agent，准确翻译，保留用户修订。导出后做结构检查、官方解析器复核和实际播放检查。
6. 用户完成最终试听与内容复核，并明确授权投稿后，通过仓库网页模板提交；等待机器人 PR，再下载精确 head 文件核对。缺标签不能成为重复创建的理由。

详细步骤由 [SKILL.md](SKILL.md) 路由到 `references/`。不支持的格式扩展应保留原 XML，不应强行通过简单 JSON 导出器重写。

## 脚本约定

- `--help` 不加载模型，也不下载权重。
- 输出默认保护已有文件；需要覆盖时明确使用 `--force`。
- `prepare_audio.py` 记录 LRC 声明的 offset，不自动应用；`--offset-ms` 为正表示延后歌词。
- `align_lyrics.py --ids` 只生成所选行的报告。使用新输出目录保存局部复核；`--force` 替换整个报告而非合并。
- `ttml_tools.py compare` 比较受支持的歌词语义；未知 TTML 扩展仍需 XML 差异和官方解析器核对。
- 本地 checker 不是官方验收，也不是音频对齐证明。未实际试听的内容必须明确标注。

## 开发与打包

```shell
uv run --python 3.12 python -B -m unittest discover -s tests -v
uv run --python 3.12 python scripts/build_release.py --out dist
```

打包器按固定允许列表收集代码、文档、示例和测试，生成 ZIP、文件 hash 清单及 ZIP 的 SHA-256；不会递归打包任务目录、模型、环境或缓存。发布包是可解压安装的 skill 源码包，不是插件市场安装包。

提交贡献时附带复现步骤与合成样例；修复应有针对性的回归测试。不要在测试夹具中提交真实完整歌曲、私人 API、令牌、个人文件路径或用户投稿数据。不要为了测试修改真实 AMLL Issue 或 PR。

0.1.0 的本地验证环境为 Windows、Python 3.12：21 项测试通过，包含 FFmpeg 合成音频重采样；合成示例通过 `ttml_processor 0.3.3` 与当时仓库 validator；英文 CTC 用已有 emission 缓存完成一行真实强制对齐复核。macOS／Linux CI 已配置但尚未远程执行，本次未重新实测 CUDA、FunASR 和 Demucs 的完整流程。

## 许可证与来源

代码与原创文档采用 [MIT](LICENSE)。发布包不包含第三方模型权重、AMLL 官方校验器源码或音乐素材；运行时使用的第三方组件须遵循各自许可。规则依据链接集中在 skill 和参考文档中，投稿前应重新核对 AMLL 当前要求。
