# 小红书线索渠道

小红书适合发现"最近大家在讨论哪些论文、为什么值得看"，但帖子和评论不是论文证据。本渠道的原则：

1. **线索 ≠ 论文**：帖子里提到的论文必须在 arXiv / OpenAlex 找到真实记录才成为种子（`clue_import.py --resolve`）。找不到的标题只留在 `xhs_clues.jsonl` 的 `unresolved_titles`，不进入候选池。
2. **种子 ≠ 纳入**：小红书种子与其他候选一样经过 Jev 排序和全文通读，不因热度直接进核心清单。
3. **热度 ≠ 质量**：点赞/收藏/评论数记在 `community_signal`，报告中单列为"社区反馈"，不参与等级、相关性或纳入判断。
4. **只读、低频、用户本人登录**：只用搜索和读取详情的工具；不点赞、不评论、不发帖。登录由用户本人扫码完成，Agent 不处理账号密码。

## 获取帖子（三种方式，任选）

### A. xiaohongshu-mcp（推荐，Windows / macOS / Linux 都有）

开源项目 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)：提供 Windows x64、macOS Apple Silicon、Linux x64 预编译程序和 Docker 镜像，以 HTTP MCP 方式运行在 `http://localhost:18060/mcp`。

**本 skill 封装的命令（推荐，不需要在 harness 里注册 MCP）**：

```bash
python scripts/xhs_setup.py --setup    # 下载并校验官方程序 → 检查 Defender → 启动服务（仅 127.0.0.1）→ 弹出浏览器扫码
python scripts/xhs_setup.py --check    # 安装 / 校验 / 服务 / 登录状态 / 排除项
python scripts/xhs_setup.py --start    # 用已保存的登录启动服务
python scripts/xhs_setup.py --login    # 登录过期时重新扫码（失败会自动恢复旧登录）
python scripts/xhs_setup.py --search "Agent 技能库 论文" --out clues/xhs_skill.json   # 只读搜索 + 前 3 篇正文与评论
python scripts/xhs_setup.py --lookup <TASK>   # 对已读论文逐篇反查口碑（默认 10 篇）
python scripts/xhs_setup.py --stop | --logout  # 停止服务 / 删除本机登录信息
```

- **登录信息只保存在本机**：`<工具目录>/cookies.json`。工具目录默认是每用户应用数据目录（Windows `%LOCALAPPDATA%\paper-cat\xiaohongshu-mcp`，macOS `~/Library/Application Support/paper-cat/xiaohongshu-mcp`，Linux `~/.local/share/paper-cat/xiaohongshu-mcp`），也可在 `runtime.local.json` 用 `xiaohongshu_tool_dir` 指定。它不在 skill 目录、任务目录或打包文件里；`--logout` 删除。
- **Windows Defender**：rod 解压的 `leakless.exe` 常被误报。`--setup` 只检测并提示；是否加入排除由用户本人决定并运行 `--setup --allow-defender-exclusion`（弹一次 UAC，只排除那一个文件，`--remove-exclusions` 撤销）。Agent 不代为修改系统安全设置。也可以改用 Docker 版或直接贴帖子文字。
- **速度**：每次调用会启动一次无界面浏览器（约 20–30 秒），所以搜索与反查都放后台运行；只用读取类工具，调用之间有间隔。
- 已在 harness 注册 MCP 时（Claude Code：`claude mcp add --transport http xiaohongshu http://127.0.0.1:18060/mcp`），也可以直接用工具 `search_feeds` / `get_feed_detail`，把返回 JSON 原样存到 `clues/`。同一账号不要同时在其他网页端登录，否则会被挤下线。

### B. MediaCrawler 导出

[NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（Playwright，扫码登录，`ENABLE_GET_COMMENTS=True`）导出的 JSON/JSONL 可直接作为输入。仅用于个人研究，遵守其许可与平台规则。

### C. 用户手动提供

用户把帖子文字、链接或截图内容贴给 Agent；Agent 存为 `clues/*.md` 或 `.txt`（截图先转写文字，标注"转写"）。

## 导入与核实

```bash
python scripts/run.py init --task <TASK> --brief brief.json --clues clues/*.json
python scripts/run.py search --task <TASK>      # 自动调用 clue_import.py --resolve 并以 xhs_clue 标签合并
```

或单独运行：

```bash
python scripts/clue_import.py --input clues/note1.json --input clues/post.md \
  --out-clues xhs_clues.jsonl --out-seeds xhs_seeds.jsonl --resolve
```

抽取内容：arXiv 编号与链接、DOI、OpenReview/ACL/ACM/GitHub 链接、书名号或引号中的英文题名、"论文：/Paper:" 后的题名，以及帖子里常见的“🤖 论文简称”“简称：一句点评”式列表（简称按 arXiv 标题前缀精确核实）。同一论文被多篇帖子提到会合并，记录 `mentioned_in_notes` 与 `mention_count`（讨论度，不是质量）。每条种子记录 `matched_by`（arxiv:/doi:/title:/name:）和 `clue_in_comments_only`（只在评论里出现）。

## 口碑反查（给推荐论文附上社区反馈）

读完论文后，可以反过来查"小红书上怎么评价这些论文"：

```bash
python scripts/xhs_setup.py --lookup <TASK>       # 一条命令：生成关键词 → 逐篇只读搜索 → 存 clues/lookup_<位置>.json → 汇总
# 已在 harness 注册 MCP 时也可分步：clue_import.py --suggest <TASK> → 用 search_feeds 搜索并原样保存 → 下一行
python scripts/clue_import.py --attach <TASK>      # 只保留确实在讨论这篇论文的帖子，汇总到报告卡片
```

报告里每篇论文显示一行"💬 小红书 N 篇讨论 · 点赞最多的一条评论（社区反馈，仅供参考）"。搜索结果里与论文无关的帖子会被过滤。注意：发帖者可能是作者本人在推广，评论比帖子更独立；热度不代表质量。

## Agent 的分析方式

在检查点 2 的汇报中，Agent 单独总结小红书线索：

- 出现了哪些论文、哪些已核实、哪些找不到（可能是题名写错或尚未公开）；
- 帖子给出的推荐理由与评论中的补充/质疑（如"代码没开源""结果复现不了"），作为**待全文验证的假设**；
- 哪些论文同时被多篇帖子提到（讨论度），但明确这不是质量结论。

最终判断仍以全文原句为准；如果全文与帖子说法冲突，以全文为准并在 `reading_notes` 中写明。
