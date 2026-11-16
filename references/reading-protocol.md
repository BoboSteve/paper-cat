# 全文通读协议

Top K 中每篇论文都要读全文后再判断。本协议让阅读可核查、可分批、可交给子 Agent。

## 每篇读什么

`fulltext_path` 指向按页切分的文本（`===== PAGE n =====`）。按以下顺序阅读，并在 `reading_notes` 里用短句记录：

1. **问题与设定**：解决什么问题，与目标主题是同一问题、相邻问题还是可迁移的方法？
2. **方法**：核心机制是什么（1–2 句）；可迁移到目标课题的部件是什么？
3. **证据**：数据集/基准、主要结果、消融；结果是否支撑其主张？
4. **局限与失败**：作者承认的局限、失败案例、负面结果。
5. **最近邻工作**：它比较的基线，是否有我们候选池里没有的重要论文（可作为补充检索词）？
6. **代价**：算力、数据、人工标注需求。
7. **与课题的关系**：最小验证实验应该怎么设计？

## 填写判断表

| 字段 | 要求 |
|---|---|
| `read_scope` | 读完后才填 `fulltext`；没读或读不了保持空或 `pending_fulltext` |
| `decision` | core / periphery / exclude / pending / merged |
| `paper_role` | direct_competitor / method_donor / failure_warning / data_eval_support（可多选，用 `;` 分隔） |
| `relevance` | high / medium / low；与等级分开，低相关不因 CCF A 进核心 |
| `reason` | 一两句，说明为什么是这个决定 |
| `evidence_quote` | **从全文原样复制** 1–2 句最能支撑决定的原句；不要改写、不要拼接不同位置的句子 |
| `evidence_page` | 引句所在页码（PAGE 标记里的数字） |
| `reading_notes` | 上面 7 点的简短记录 |

导入时 `judgment_import.py` 会在全文中逐字定位引句（容忍换行、连字符断行和连字），找不到即拒收，并给出最接近位置的页码供修正。

## 分批与子 Agent

- 先读 5–10 篇，向用户汇报方法聚类、意外发现、是否需要调整标准，再继续。
- K > 10 时可按 5 篇一批交给子 Agent（harness 支持时）。给子 Agent 的任务只包含：目标主题与纳入/排除标准、这一批的 `fulltext_path`、本协议的字段表；要求返回与判断表同列的行。主 Agent 合并结果、抽查引句，再导入。
- 子 Agent 与主 Agent 的判断冲突时，主 Agent 重读冲突段落并在 `reason` 中说明取舍。

## 不能做的事

- 只看摘要就给 core/periphery/exclude。
- 把 Jev 分数、会议等级、小红书热度当作相关性理由。
- 用全文以外的记忆补充论文内容。
