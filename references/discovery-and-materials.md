# 歌曲、版本、查重与素材

## 1. 建立歌曲身份

按曲名 + 歌手搜索，必要时再加专辑；不要因为第一个结果同名就选它。检查原版、现场、Acoustic、Remix、翻唱、剪辑版、加速版、单曲/专辑重复发行。保留原始大小写的正式歌名和歌手姓名。

记录 `track.json`：曲名、歌手数组、专辑、平台 ID、版本描述、平台时长、查询时间、来源 URL、占位/投稿/PR 链接。下载后补上解码时长、采样率、声道数、SHA-256。平台 ID 和音源必须对应；另一平台的同名歌不能直接共用时间轴，需检验开头静音及整体版本。

Meting 是可选来源，服务由使用者自行提供；本项目不附带公共音乐下载服务。先查看所用服务的文档，确认是否支持以下查询约定（并非所有部署都支持搜索）：

```text
server=netease&type=search&id=关键词&limit=10
server=netease&type=song&id=歌曲ID
server=netease&type=lrc&id=歌曲ID
server=netease&type=url&id=歌曲ID
```

不要字符串拼接未编码关键词。随附脚本使用 URL 编码：

```powershell
$amllSkill = Join-Path $env:USERPROFILE '.agents/skills/amll-lyrics'
$env:AMLL_METING_BASE = Read-Host '输入你有权使用的 Meting API 完整地址'
$amllQuery = Read-Host '输入曲名与歌手'
uv run --python 3.12 --no-project python "$amllSkill/scripts/fetch_meting.py" search --query $amllQuery --out 'work/song/search.json'
```

脚本不自动从搜索结果选 ID；先检查返回的 artist/title/album/id 或嵌套链接。`--base` 可覆盖环境变量。基础 URL 已有的查询参数会保留，同名业务参数由本次请求替换。响应及下载清单可能包含带签名的地址，属于本地任务数据，不能随源码公开。

## 2. 分三层查重

1. **库内**：使用 [歌词站](https://amlldb.bikonoo.com/) 搜索，或 GitHub code search 按歌曲平台 ID、曲名、别名搜索 `raw-lyrics`、`metadata`、对应平台目录。也可对已取得的仓库索引用 `rg`。代码搜索有索引延迟，精确平台路径及元数据再查一次。
2. **投稿 PR/Issue**：搜索全部状态，检查正在审、被拒、已合并、已关闭但转成 PR 的情况。不能只查主分支就说没人做过。
3. **制作占位**：搜索曲名、歌手及 ID，阅读占位正文中涉及的版本。自己的旧占位复用；别人已占位时告知状况，不再默认创建重复占位。

```powershell
$amllRepo = 'amll-dev/amll-ttml-db'
$amllSongQuery = Read-Host '输入已确认的歌名／歌手搜索式'
$amllTrackId = Read-Host '输入已确认的网易云歌曲 ID'
gh issue list --repo $amllRepo --state all --search $amllSongQuery --limit 100 --json number,title,state,author,url
gh pr list --repo $amllRepo --state all --search $amllSongQuery --limit 100 --json number,title,state,url
gh api --method GET search/code -f "q=$amllTrackId repo:$amllRepo" --jq '{total_count,paths:[.items[].path]}'
```

处理分页、限流与搜索错误。403/429/超时/索引缺失不能当成“没有”。如果已存在：先读文件确定是补充、修正还是不同版本，再决定是否继续；保留原作者署名。

## 3. 素材下载

已确认 ID 后，下载到每首歌独立目录。用户已要求使用自己的 API 获取素材时直接执行正常公开接口请求，无需反复询问同一来源。正常失败可短暂重试一次；403/付费限制/无效授权等不尝试绕过，说明具体响应并继续处理已有歌词。

```powershell
uv run --python 3.12 --no-project python "$amllSkill/scripts/fetch_meting.py" fetch --id $amllTrackId --out 'work/song/source'
```

脚本支持直接音频、重定向以及 JSON 中的 URL；音频最终用 ffprobe 验证，不以 HTTP 200、扩展名或文件大小代替音频校验。没有拿到真实音频时不能提供一份伪 MP3。用户要求下载链接时可给 API 或验证过的直链，注明直链可能过期。

只公开投稿 TTML，不附带整首音乐上传到 GitHub；审核确实需要音源时按仓库流程提供用户授权的来源链接。模型下载仅用官方或已知可信缓存，不自动执行陌生 checkpoint 中的代码。

## 4. LRC 清理与翻译

优先级：用户修订稿 > 已确认版本的可靠原文 > 平台原文 > ASR 候选。发生冲突回听/比较证据，不能按优先级悄悄消除用户故意改动。

- 原 LRC、平台译文和清理后的唱词分别存档。
- 识别 `[mm:ss.xxx]` 多时间标签、`[offset:毫秒]`、时间顺序、空行和可能的元数据；一行重复时间标签要展开为多个演唱事件。
- 时间为 `00:00` 可能是真歌词；尾奏区可能还有 ad-lib。作词/作曲/编曲/宣传等信用信息经文本和音频核对后移到元数据，不写进正文。
- 单独保存平台译文，不把中文译文误当英文主歌词送给模型。歌词请求为空时查其他已授权的来源，不能从歌曲标题生成完整歌词。
- 不把所有标点删除；去除不合规的句末逗号/句号时保留缩写中的 `'`、意义必要的问号/感叹号及词内连字符。
- 英文按行首和专名校正大小写；合并行后重新检查原来的行首词，如 `a` + `Wild animal` 应为 `a wild animal`。
- 润色译文不改变主客体、否定、时态和比喻关系。用户要求文艺时可自然表达，不能为了押韵重写剧情。

## 5. 可追溯目录

建议结构：`source/` 原文件与下载记录，`analysis/` 采样音频/模型缓存/对齐 JSON/疑点，`output/` 用户最终文件，`submission/` 冻结的投稿文件与上传记录。模型大缓存可放共享目录。

同一首歌的文件名含版本或状态；最终 TTML 配套明确是哪一个音频 hash。不要把另一版本音频与旧轴打包给用户。修改前备份用户文件，默认输出新文件，只有已知的本次生成文件才可按当前任务更新。
