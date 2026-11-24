# 检查点 1：检索计划（请确认）

**主题**：面向数据可视化的多场景协同、多智能体与人机协同分析

**主题描述**：Data visualization for multi-scenario collaboration, multi-agent systems, and human-machine collaborative analysis

**目标论文画像**：Focus on recent top conferences and journals in computer science, particularly in visualization, human-computer interaction, and multi-agent systems.

**时间窗**：2023-01-01 to 2026-11-24  → 解析为 {'start': '2023-01-01', 'end': '2026-11-24'}

**纳入标准**：

**排除标准**：

**路径**：B　**检索规模**：小（默认读 20 篇）　**全文阅读量 K**：5　**会议轨道**：main_only（main_only 只收主会；include_findings 同时收 Findings）

**检索式**（由概念组自动生成：组内同义词 OR、组间 AND）：
- `concepts` data visualization multi-scenario collaboration multi-agent systems human-machine collaboration collaborative analysis（arxiv,crossref；arXiv 最多 300 条，OpenAlex 最多 200 条，其余来源各最多 25 条）

**数据源**：arXiv、OpenAlex（免 key）、Crossref 可用；未配置本地排序模型：rank 阶段按关键词覆盖度排序，再由 Agent 读摘要二次排序。


**可选：再配置以下来源效果更好（不配置也能完整运行，不影响本次确认）**

| 来源 | 配置后的好处 | 怎么配置 |
|---|---|---|
| OpenAlex | 免 key 也能用；配了 key 有自己的每日额度，检索、补摘要、正式发表载体与 ISSN（等级/分区靠它）更稳定，不易被限流 | `python scripts/configure_env.py openalex`（在 openalex.org 注册，账户设置里复制 API key（免费额度）） |
| Semantic Scholar | 多一个高质量检索源，摘要覆盖更全 | `python scripts/configure_env.py semantic`（semanticscholar.org/product/api 点 Request an API Key 填表，key 通过邮件发放（可能要等几天）） |
| Unpaywall | 多一个合法开放获取 PDF 下载渠道，全文获取率更高 | `python scripts/configure_env.py unpaywall`（无需注册，只需填写一个联系邮箱） |
| 小红书（xiaohongshu-mcp） | 从帖子和评论发现大家在讨论、推荐的论文（只作线索，必须再核实） | `python scripts/xhs_setup.py --setup`（一条命令：下载并校验官方程序、启动本机服务、弹出浏览器由你扫码（Windows 若被 Defender 误拦，会提示你自行决定是否排除）） |

密钥和密码请在你自己的终端里运行上面的命令输入（不回显），不要发到对话里。

**要不要顺便看看小红书？** 发帖的人往往是对论文比较有把握才分享，评论里也常有复现反馈。可以用来发现论文，并在最后给推荐论文附上“社区口碑”（只作参考，不作证据）。
- 选项：A 看（需要你先运行 `python scripts/xhs_setup.py --setup` 扫码登录，约 3 分钟；或者直接把帖子文字贴给 Agent）　B 这次不看　C 以后都不要再问（`python scripts/doctor.py --dismiss xiaohongshu`）

种子文献：0 个文件；小红书线索：0 个文件

---
需要用户确认：主题与画像是否准确、检索式是否要增删、K 与轨道策略是否合适。
确认后运行：`python scripts/run.py search --task "/Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/deep/final_actual_test"`
