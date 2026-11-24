# 检查点 3：前 5 篇全文阅读名单（请确认）

排序方式：按关键词覆盖度排序（未使用本地模型）；建议让 Agent 读摘要做一次二次排序。共 11 篇；≥0.9 0，0.5–0.9 0，<0.5 11。

| 位置 | 分数 | 标题 | 年份 | 发表载体 | 等级/分区 | 提示 |
|---|---|---|---|---|---|---|
| 0 | 0.26 | Human-AI collaboration in Hybrid Multi-Agent Systems | 2025 | International Journal of Electronics and | — |  |
| 1 | 0.24 | MASK: Multi-Agent Collaboration for Optimizing Adversarial Attacks on Multi-Agen | 2026 | IEEE Transactions on Knowledge and Data  | 中科院 1 区 |  |
| 2 | 0.22 | Beyond Optimization: A Multi-Agent Systems and Human–Agent Collaboration Perspec | 2026 | Drones | 中科院 3 区 |  |
| 3 | 0.20 | Generative and Cognitive Artificial Intelligence for Adaptive Human-Machine Coll | 2026 | International Conference on Multi-Agent  | — |  |
| 4 | 0.20 | Ai-Centric Design 2.0: Orchestrating Human-Ai Collaboration Through Multi-Agent  | 2025 | — | — |  |

## 紧随其后的 6 篇（可补回）

| 位置 | 分数 | 标题 | 年份 | 发表载体 | 等级/分区 | 提示 |
|---|---|---|---|---|---|---|
| 5 | 0.20 | Collaboration and Coordination in Multi-Agent Systems | 2024 | — | — |  |
| 6 | 0.00 | Bias Correction and Data Synthesis through Reflective LLM Collaboration | 2025 | — | — |  |
| 7 | 0.00 | Multi-scenario digital twin-driven human-robot collaboration multi-task disassem | 2025 | Journal of Manufacturing Systems | 中科院 1 区 |  |
| 8 | 0.00 | Multi-Agent LLM Systems: From Emergent Collaboration to Structured Collective In | 2025 | — | — |  |
| 9 | 0.00 | Multi-Modal Analysis of Presentation Media Data | 2025 | International Conference on Multi-Agent  | — |  |
| 10 | 0.00 | Modeling AI-Human Collaboration as a Multi-Agent Adaptation | 2025 | — | — |  |

## 你可以这样调整

- 删掉不想读的、补回想读的、改 K：直接告诉 Agent（会写入 `topk_overrides.json`）；
- 让 Agent 读摘要做二次排序并按方法类别分散（`rerank_sheet.md` → `rerank.json` → `run.py rerank`）；
- “提示”一栏是摘要里出现了你的排除词，只提醒读全文时确认，不会自动排除。

确认后运行：`python scripts/run.py fetch --task "/Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/deep/final_actual_test"`
