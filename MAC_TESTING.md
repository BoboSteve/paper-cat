# Mac 平台测试记录

## 测试目标
在 Mac 平台上成功运行 PaperCat 的速览流程，验证跨平台兼容性。

## 测试环境
- 操作系统: macOS
- Python 版本: 3.9+
- 硬件架构: Apple Silicon (M系列芯片) 或 Intel

## 测试步骤

### 1. 环境准备
```bash
# 克隆仓库（如果还没有）
git clone https://github.com/Kristy-Nora/paper-cat
cd paper-cat

# 安装依赖
python scripts/install_skill.py --target claude
```

### 2. 环境自检
```bash
python scripts/doctor.py
```

### 3. 运行速览流程
```bash
# 创建测试任务目录
mkdir test_task

# 运行速览流程
python scripts/quick.py search --task test_task --topic "LLM Agent 长期记忆" --concepts '[["long-term memory", "agent memory"], ["LLM", "large language model"]]'
```

## 预期结果
- [ ] 成功运行环境自检
- [ ] 成功执行速览搜索
- [ ] 生成有效的报告文件

## 实际测试记录

### 测试日期: 2026-11-24
### 测试人员: BoboSteve

### 步骤 1: 环境准备
#### 执行命令:
```bash
python scripts/install_skill.py --target claude
```

#### 执行结果:
```
[No output - installation successful]
```

### 步骤 2: 环境自检
#### 执行命令:
```bash
python scripts/doctor.py
```

#### 执行结果:
```
环境检查
✓ 两分钟速览：可直接使用（无需任何账号或配置）
✓ 深度检索（全文核实）：可用
  等级名录（CCF / 中科院分区）：首次深度检索时自动下载（需访问 GitHub）；下载失败也能继续，只是不标等级
可选增强（不配也能用）：
  · OpenAlex：免 key 也能用；配了 key 有自己的每日额度，检索、补摘要、正式发表载体与 ISSN（等级/分区靠它）更稳定，不易被限流 → python scripts/configure_env.py openalex
  · Semantic Scholar：多一个高质量检索源，摘要覆盖更全 → python scripts/configure_env.py semantic
  · OpenReview：按会议找全（如 ICLR）时枚举全部主会录用论文，并区分 Oral/Spotlight/Poster → python scripts/configure_env.py openreview
  · Unpaywall：多一个合法开放获取 PDF 下载渠道，全文获取率更高 → python scripts/configure_env.py unpaywall
  · 小红书（xiaohongshu-mcp）：从帖子和评论发现大家在讨论、推荐的论文（只作线索，必须再核实） → python scripts/xhs_setup.py --setup
  · 本地排序模型（可选）：候选很多（数百篇以上）时用本地小模型快速初排，深度模式更快 → 按 references/runtime-setup.md 配置 runtime.local.json
```

### 步骤 3: 速览流程测试
#### 执行命令:
```bash
mkdir -p test_mac
python scripts/quick.py search --task test_mac --topic "LLM Agent 长期记忆" --concepts '[["long-term memory", "agent memory"], ["LLM", "large language model"]]'
```

#### 执行结果:
```
QUICK_SEARCH raw=300 unique_in_window=283 full_coverage=202 shortlist=40
  openalex：150 篇
  arxiv：150 篇
NEXT 读 /Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/quick/shortlist.md，写 /Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/quick/picks.json，再运行 render
```

### 步骤 4: 生成推荐报告
#### 执行命令:
```bash
python scripts/quick.py render --task test_mac
```

#### 执行结果:
```
QUICK_REPORT picks=3 also=3 -> /Users/bobo/05_Project/06_curiosity/paper-cat/test_mac/quick/report.html
```

## 遇到的问题及解决方案

### 问题 1:
#### 描述: 无
#### 解决方案: 无

### 问题 2:
#### 描述: 无
#### 解决方案: 无

## 结论
- [x] 测试通过
- [ ] 测试失败，需要修复

## 测试总结
Mac 平台上的 PaperCat 速览流程已成功运行。整个流程包括：
1. 环境自检 - 成功通过所有检查项
2. 搜索 - 成功检索到 300 篇相关论文
3. 生成推荐 - 成功创建了 3 篇推荐论文和 3 篇也值得一看的论文
4. 报告生成 - 成功生成 HTML 格式的报告文件

测试表明 PaperCat 在 Mac 平台上完全兼容，可以正常运行速览流程。