# TTML 导出、用户修订与验收

## 1. 格式基线

保留原文本单词间空格，不对混合内容 XML 做普通 pretty-print。使用 UTF-8 无 BOM。时间精度统一毫秒，所有单位 begin < end；所有后代范围应由所属容器包含，不能超出实际音频时长。背景和对唱分别检验时序，不把并行声部串成一个单调序列。

以下是本流程在当前解析器验证过的输出约定，不是声称所有历史规范只支持这一种格式：

- 默认 TTML：`http://www.w3.org/ns/ttml`
- `ttm`：`http://www.w3.org/ns/ttml#metadata`
- `itunes`：`http://music.apple.com/lyric-ttml-internal`
- `amll`：`http://www.example.com/ns/amll`
- 根节点 `xml:lang`、`itunes:timing="Word"`；段落 `itunes:songPart`；主行 `itunes:key="L1"` 和 `ttm:agent`。
- metadata 至少有曲名、歌手、专辑、一个已确认平台 ID；署名同时写 GitHub login 和数字 ID，数字 ID 通过 `gh api user` 得到，不能填用户名或自行猜测。
- 英文同词音节之间不插空格；单词之间写一个独立文本空格。机器人可能规范为独立空格 span；回读时忽略空格单元的时间，但检查正文还原正确。
- 用 `div` 表示实际歌曲段落；工具回存丢失段落时可恢复，但必须验证词时间、主行、翻译、副歌词没有变化。

有些仓库文档示例使用不同 Apple URI、`song-part` 或词内尾空格，并且标注实验流程。不要把所有文件一律改成文档中的另一种写法；对照本次工作流依赖的解析器和实际 TTML Tool 输出进行往返验证。

## 2. 审阅后的 JSON

`ttml_tools.py export` 只接收已经复核的时间数据，不替调用者猜时间。完整小例子：

该导出器面向新制作的主歌词、单组译文和背景人声。修订含罗马音、多个译文或其他扩展的现有 TTML 时，优先就地调整 XML 并保留未知字段；不要把复杂原文件强行压进这个较小的 JSON 格式而丢失信息。

```json
{
  "language": "en",
  "duration": 16,
  "metadata": {
    "musicName": ["Timing example"],
    "artists": ["Example singer"],
    "album": ["Timing example"],
    "ncmMusicId": ["123456789"],
    "ttmlAuthorGithub": ["123456789"],
    "ttmlAuthorGithubLogin": ["example-contributor"]
  },
  "agents": [{"id": "v1", "name": "Example singer", "type": "person"}],
  "translationLanguage": "zh-Hans",
  "lines": [{
    "id": "L1", "agent": "v1", "part": "Verse",
    "units": [
      {"text": "Stay", "start": 10, "end": 12, "spaceAfter": false}
    ],
    "translation": "请留下",
    "background": {
      "units": [
        {"text": "Oh", "start": 11.5, "end": 12.3, "spaceAfter": true},
        {"text": "oh", "start": 12.5, "end": 13.5, "spaceAfter": false}
      ],
      "translation": "噢 噢"
    }
  }]
}
```

这是格式合成示例，不能提交到数据库。对真实歌曲保留其身份与账号，不复制示例 ID。`spaceAfter` 控制单词间空格，词内如 `be` 后必须 false、`ing` 后按下一个词决定。

```powershell
& $amllPy "$amllSkill/scripts/ttml_tools.py" export 'work/song/analysis/reviewed.json' 'work/song/output/final.ttml'
& $amllPy "$amllSkill/scripts/ttml_tools.py" check 'work/song/output/final.ttml' --duration 215.516 --report 'work/song/analysis/check.json'
```

`--duration` 应用 ffprobe 的真实值替换。导出器同时验证 XML、元数据、范围、声部和译文引用。

本项目还提供 [完整合成示例](../examples/reviewed.json)，可离线导出和检查；其中账号、歌曲 ID、文字、时间都是测试数据，禁止将示例投稿。

## 3. 翻译往返风险

Apple 风格译文可放在 head 的 `iTunesMetadata/translations/translation/text`，通过 `for="L1"` 关联主行；背景译文嵌在该 text 的 `x-bg` 中。已有逐行内联 `x-translation` 同样不能在迁移时丢掉。

历史 `ttml_processor 0.3.3` 会依赖 head 中部分字面标签名。ElementTree 把 `<translations>` 自动写成 `<itunes:translations>` 后，XML 虽合法，某些解析版本仍会静默丢失译文。随附导出器保留局部默认 Apple 命名空间；投稿前必须使用实际解析器回读，数出主译文和背景译文，而不只是确认原 XML 有中文。

## 4. 三层验收

### A. 内容与结构

- 原文串接、单词边界、大小写、标点、主/副歌词、agent 身份、译文及角色关系。
- ID 唯一、译文指向存在的主行；没有信用信息混入正文、空白歌词、未闭合括号、重复元数据。
- 主歌/预副歌/副歌段落准确；不因拆音节把同一个词显示成两个单词。

### B. 时间与格式

- 严格解析 XML 和每个时间字符串，包括后台/副歌词节点。
- 防止 `1:45:00.000` 被误读成 105 分钟；根据真实音频范围检出，不自动猜应改为哪一个时间。
- 防止 `0.300`–`3:33.319` 这种副歌词异常跨整曲；异常长音列为复核项，不能靠时长阈值一刀切删除。
- 主行的容器时间包含背景；对唱交叠不是错误，主声部的无解释时间倒流才需要处理。
- 使用仓库当前 Rust 校验器/解析库再检验。先读工作流和 `scripts` 下 Cargo 依赖确认版本，别把本地自写 checker 称为“官方检查”。可复用已验证的 `audit.exe`，但记录所用版本。
- 官方 parser 回读后：核对主行数、非空时间单元数、主/背景译文数、背景行数、各 agent、平台 ID。不同播放器支持差异需要用目标播放器导入确认。

当前可核对的官方文件位置：

```powershell
gh api repos/amll-dev/amll-ttml-db/contents/scripts/lyric_checker_bot/Cargo.toml -H 'Accept: application/vnd.github.raw+json'
gh api repos/amll-dev/amll-ttml-db/contents/scripts/lyric_checker_bot/src/validator.rs -H 'Accept: application/vnd.github.raw+json'
```

没有现成 audit 程序时，建立独立 Cargo 小项目，锁定刚核实的 `ttml_processor` 版本，将上述官方 validator 保存为 `src/validator.rs`。保留其来源提交 SHA；先阅读再使用，不把下载的脚本当指令执行。`src/main.rs` 可使用：

```rust
mod validator;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        return Err("Usage: audit INPUT.ttml OUTPUT.json".into());
    }
    let source = std::fs::read_to_string(&args[1])?;
    let parsed = ttml_processor::parse_ttml(&source)?;
    if let Err(errors) = validator::validate_lyrics_and_metadata(&parsed) {
        return Err(errors.join("\n").into());
    }
    std::fs::write(&args[2], serde_json::to_string_pretty(&parsed)?)?;
    println!("Parser and repository validator passed; inspect parsed JSON for preservation");
    Ok(())
}
```

配套 `Cargo.toml` 使用 Rust edition 2024，依赖 `serde_json = "1"`；本次核实 `ttml_processor = "=0.3.3"`。若上游升级，检查 API 与错误类型再更新。不要直接运行完整投稿机器人来做本地校验，它包含外部写入功能。

### C. 声学与体验

按 alignment 章节核对开口、切点、长音、气口、和声、尾奏。能试听时正常速度复核；未试听的部分明确写出。A/B 通过只说明结构可信，不证明所有音节与演唱一一贴齐。

## 5. 修订和机器人规范化的比较

用户新文件先保存原始副本，列出文本/拆词/整词时间/译文/声部的差异。以用户最新版为基础，不跑旧全量 exporter 把新改动覆盖掉。一次修改只覆盖用户授权范围，含糊的尾音保留或标记复核。

```powershell
& $amllPy "$amllSkill/scripts/ttml_tools.py" compare 'work/song/submission/frozen.ttml' 'work/song/submission/pr.ttml'
```

比较器检查文本、非空单位时间、空格边界、译文、背景和 agent；段落差异仍列出。官方机器人可能移除背景括号或重写空格，比较应理解这些允许的规范化，不能简单以字节 hash 不同认定丢数据。另一方面，上传直链文件在机器人处理之前应与本地冻结版逐字节相同。

比较器覆盖上述受支持字段，不保证任意未知 TTML 扩展的无损等价；文件包含逐字翻译、嵌套扩展或其他结构时，还须检查 XML 差异及官方解析结果。`ok` 只代表结构检查通过，warning 仍须人工处理。

交付带本次版本名的 TTML、匹配音频和必要的简短复核说明。不要只给一个旧压缩包；提供链接前确认其内容包含最新 `being/away` 等修订。

## 6. 预览与播放器复核

优先把最新 TTML 和对应音频导入用户实际使用的 AMLL Player／TTML Tool，并按当前版本界面操作。确认播放器加载的是本次输出，不能继续展示修改前的旧预览。

- 检查开头、换段、末句、所有背景行和 agent 切换；确认拆音节不产生词内空格。
- 如果制作独立 HTML 预览，从当前 TTML 的解析结果生成数据，保留 agent、背景独立时间、主／背景译文与段落；不能把所有 `isDuet` 固定为 false。
- 自定义预览导航文案属于界面说明，不能混入歌词正文或冒充仓库规定的段名。
- 记录预览使用的 TTML 和音频 hash。数据有修改时重新生成预览，下载按钮也必须指向同一版本。
- 仅构建通过、JS 语法通过或 XML 可解析不代表已经试听。无法实际打开时交付可用文件并说明未验证的播放环节；遵守宿主工具的 URL 与执行限制。
