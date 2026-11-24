# 检查点 4：全文通读（1/5 篇可读，共约 9 页）

已分成 1 批（`reading/assignment_B*.md`），阅读说明在 `reading/INSTRUCTIONS.md`，协议见 `references/reading-protocol.md`。
- 每批读完写 `reading/batch_<批次>.json`；证据句必须从原文逐字复制，会自动核对并定位页码；
- 支持子 Agent 的工具可以每批交给一个子 Agent 并行读；不支持就逐批顺序读；
- 中断（额度用完、断网）不会丢进度：`python scripts/reading.py status --task <TASK>` 列出还没完成的批次，只重读那些；
- 先读完第一批，向用户汇报方法聚类和意外发现，再继续。

## 批次

- B1：1 篇，约 9 页

## 缺全文（保持待核实）

- 位置 1：MASK: Multi-Agent Collaboration for Optimizing Adversarial Attacks on Multi-Agent Systems — 所有开放渠道均失败 → 进入人工取全文清单
- 位置 2：Beyond Optimization: A Multi-Agent Systems and Human–Agent Collaboration Perspective on De — 所有开放渠道均失败 → 进入人工取全文清单
- 位置 3：Generative and Cognitive Artificial Intelligence for Adaptive Human-Machine Collaboration — 所有开放渠道均失败 → 进入人工取全文清单
- 位置 4：Ai-Centric Design 2.0: Orchestrating Human-Ai Collaboration Through Multi-Agent Systems fo — 所有开放渠道均失败 → 进入人工取全文清单

给用户两个选项：
- A 自己下载 PDF，保存为 `/Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/deep/final_actual_test/pdfs/manual/<位置>.pdf`（如 `1.pdf`），再运行一次 fetch；
- B 用名单后面的论文替补：Agent 写 `topk_overrides.json`（如 `{"remove": [1], "add": [<检查点 3 的位置>]}`）再运行一次 fetch；
- 都不选也可以：缺全文的论文保持“待核实”，不会进入核心结论。

---
全部批次完成后运行（会自动合并、核对引句、生成报告）：
`python scripts/run.py finalize --task "/Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/deep/final_actual_test" --judged-by "<harness> (<model>)"`
