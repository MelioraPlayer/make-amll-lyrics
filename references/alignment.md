# 自动模型流程与声学复核

## 环境与模型

先检查已有环境和缓存。一个可复现的历史组合是 Python 3.12、torch/torchaudio 2.8.0、Demucs 4.0.1、stable-ts 2.19.1；中文实测 FunASR 1.4.16。这是兼容性基线，不表示最新版。TorchAudio 2.8 的 forced_align 已弃用、2.9 移除，不能直接升级再假定 API 存在。[官方说明](https://docs.pytorch.org/audio/2.8/tutorials/ctc_forced_alignment_api_tutorial.html)

没有可用环境时，最小英文 CPU 环境：

```powershell
uv venv --python 3.12 .venv-amll
uv pip install --python '.venv-amll/Scripts/python.exe' torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python '.venv-amll/Scripts/python.exe' numpy soundfile
$amllPy = (Resolve-Path '.venv-amll/Scripts/python.exe').Path
$amllSkill = Join-Path $env:USERPROFILE '.agents/skills/amll-lyrics'
```

先看 `torch.cuda.is_available()`，GPU 不可用不等于电脑没有显卡；可能安装的是 CPU wheel。大任务可按 PyTorch 官方配套版本建立 CUDA 环境，不能只改 torch 造成 torchaudio DLL 不匹配。小范围复核优先复用现有 CPU 环境。

上述为 Windows PowerShell。macOS／Linux 的解释器位置为 `.venv-amll/bin/python`；也可使用 [README](../README.md) 中的依赖文件安装方式。基础校验无需安装模型依赖。

英文：Wav2Vec2 English CTC 用于原拼写对齐，MMS_FA 作为另一种声学定位；Whisper/stable-ts 用于检查段落遗漏、识别冲突及粗分行。中文：FunASR 时间戳模型配合已核对汉字；MMS 的罗马化需逐字映射并处理多音字。其他语言须选合适模型/转写，英文归一化器遇到非英语字符应报错，不能默默丢字。

## 1. 准备音频和窗口

```powershell
& $amllPy "$amllSkill/scripts/prepare_audio.py" --audio 'work/song/source/source.audio' --lrc 'work/song/source/original.lrc' --out 'work/song/analysis'
```

输出 16 kHz mono PCM 和待核对行窗口。原音频原样保留。ffmpeg 重采样不剪掉头部静音，不做变速，不重设时间零点。

检查 `rows.json`，移除确认不唱的信用行，并填写 `units`。英文默认按空格成词；中文先按字，保留标点与空格映射，不把字按模型输出字数硬凑。每行提供 `cropStart/cropEnd`，覆盖完整唱词和合理上下文；默认 LRC 行窗只是候选。长间奏应缩窗，下一行若有提前进入应留重叠余量。

没有 LRC 时，先用 ASR/Whisper 找大致区域，再把用户确认的歌词映射到这些区域。不能直接均匀分配整首歌时长。

## 2. 按需分离人声

伴奏强、和声混叠或时间模型明显跑偏时用 Demucs。

```powershell
uv pip install --python $amllPy demucs==4.0.1
& $amllPy -m demucs --two-stems vocals -n htdemucs --shifts 0 -o 'work/song/analysis/separated' 'work/song/source/source.audio'
```

实际输出目录以命令结果为准。保留完整长度和起点；把 vocals 再转成 16 kHz 单声道。TorchAudio 新旧 I/O 有兼容问题时，优先 ffmpeg/soundfile；可使用 Demucs 官方 Python API + soundfile 导出，不能假装失败命令已经产生可用文件。

分离后的 vocals 仍可能残留合成器、吉他、混响；同样也可能损伤和声。所有关键判断交叉对照原混音。高度平直的音高不是“必然是合成器”的证据。

## 3. 运行自动对齐

```powershell
$env:TORCH_HOME = (Join-Path (Get-Location) 'work/models')
& $amllPy "$amllSkill/scripts/align_lyrics.py" --audio 'work/song/analysis/mix-16k.wav' --rows 'work/song/analysis/rows.json' --model ctc --out 'work/song/analysis/ctc'
& $amllPy "$amllSkill/scripts/align_lyrics.py" --audio 'work/song/analysis/mix-16k.wav' --rows 'work/song/analysis/rows.json' --model mms --out 'work/song/analysis/mms'
```

有 vocals 时也跑关键片段，或以 vocals 为主、原混音为复核。中文使用 `--model funasr`，安装对应依赖；中文 rows 必须提供逐字 `units`，必要时提供等长 `modelUnits`（供模型的发音文本，输出仍保留原字）。不要把所有外语语气词无条件改为“哦”。

脚本输出每字母/字的时间、模型分数和缓存来源，缓存键含音频 hash、裁剪窗、模型与版本；字幕文本改变可重用同音频 emissions，但不能重用旧 token 对齐结果。

默认拒绝覆盖 `alignment.json`；修订后需明确添加 `--force` 或输出到新目录。`--force` 保留 emission 缓存，但成功后会替换整个结果 JSON。用 `--ids` 只跑局部时应输出到单独目录，不能把局部结果当作全曲结果；对齐失败不覆盖旧报告。

强制对齐不是识别正确性的证明：给错词也能产出时间。对低分、文本与 greedy decode 冲突、落在窗口边缘、极短/极长单位、跨行重叠输出 review 标记。分数是模型内部指标，不是“准确率 90%”。

英文拆分时，先为全曲整理多音节候选清单，不只处理用户点名的词。基于词典读音与唱法选定字符分组后，可把分组传给脚本自动投影到模型字符时间：

```json
{"L10":{"5":["a","way"]},"L31":{"4":["be","ing"]}}
```

最外层是 rows 中的真实行 ID，内层是该行从 0 开始的 unit 索引；这个例子对应两行已核对的演唱片段，不能直接套到另一首歌。将内容保存为 `split-plan.json`，在原对齐命令追加 `--split-plan 'work/song/analysis/split-plan.json'`。脚本保持每个词的原拼写，输出 `syllableCandidates`。变更分组可重用 emissions；候选边界仍须按下面步骤复核。

## 4. 从字母定位到演唱音节

按 `vocals-and-syllables.md` 由实际发音确定拆法，再利用字母证据定位音节首辅音和元音转折。CTC 的字母峰值不等于真实发声起点，不能机械将所有字母时间向前移固定 25 ms。

组合多模型时检查它们是否指向同一声学事件，而非简单平均所有时间。差异超过约 80–120 ms 是复核提示，不是普遍的合格/不合格阈值。唱得快时 60 ms 已很明显，缓慢滑音则边界本来就宽。

`MMS_FA.get_model()` 已产生 log probabilities 并附 wildcard 通道；不要再套一次 softmax 后拿分数和英文 CTC 横向比较。CTC 普通声学模型则需要 log_softmax。缓存应包含这种后处理版本，避免修正代码后继续使用旧 emissions。

没有可信边界：保留整词或少拆，并记录理由。要求“尽量拆多音节”不意味着必须把每个词拆到字母。

## 5. 尾音、气口、重复段

- 模型往往只抓到声母/元音核心，长音会截短；`you/all/prey/away` 等尾音需要单独检查释放位置。
- 不把尾词填满到下一行来遮盖间奏；不把所有混响当仍在唱词。以原混音、人声波形、频谱和可用试听综合判断。
- 一个元音上升降多个音高通常仍是同一音节；重新起声/明确重唱才增加新的单位，不能凭每个波峰编写 Ohh。
- 重复副歌可用互相关、频谱匹配确定偏移，但要检查是否真为同一录音片段。相同歌词、相同节拍都不能证明尾音和和声也相同。
- 局部修订只移动有证据的切点。保留整词起止时要明确这轮没有重新验证整词范围，不把“保留”写成“已重新校准”。

## 6. 复核记录

生成 `review-notes.json`：行 ID、原切点、新切点、证据来源、是否重算、是否试听、仍不确定之处。正常速度检查语义和节拍；慢速辅助定位，最后回到原速。只能看波形/模型时如实写“模型与波形复核”，不能说“逐句听过”。

覆盖全曲的明显错位检查，并重点抽查：首句、所有新增拆分、跨段过门、重复副歌、两声部重叠、最后十几秒。输出时说明哪些是精修、哪些仍待听感确认。
