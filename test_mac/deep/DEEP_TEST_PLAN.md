# 深度检索测试计划

## 测试主题
面向数据可视化的多场景协同、多智能体与人机协同分析

## 测试目标
- 验证深度检索在 Mac 环境下的功能
- 探索近三年顶会顶刊论文
- 识别潜在研究创新点
- 控制总耗时在 30 分钟以内

## 测试步骤

### 1. 环境验证
- [x] 确认 `paper-cat` 虚拟环境已激活
- [x] PyMuPDF 已正确安装
- [x] 环境自检通过

### 2. 深度检索配置
使用以下配置参数：
```json
{
  "topic": "面向数据可视化的多场景协同、多智能体与人机协同分析",
  "topic_description": "Data visualization for multi-scenario collaboration, multi-agent systems, and human-machine collaborative analysis",
  "time_window": "2023-01-01 to 2026-11-24",
  "concepts": [
    ["data visualization", "information visualization"],
    ["multi-scenario collaboration", "cross-domain collaboration"],
    ["multi-agent systems", "multi-agent collaboration"],
    ["human-machine collaboration", "human-computer interaction"],
    ["collaborative analysis", "cooperative analysis"]
  ],
  "selected_k": 5,
  "pack": "general",
  "inclusion": [],
  "exclusion": []
}
```

### 3. 测试执行顺序
1. **初始化**：`python scripts/run.py init`
2. **搜索**：`python scripts/run.py search`
3. **排序**：`python scripts/run.py rank`
4. **获取全文**：`python scripts/run.py fetch`
5. **阅读**：`python scripts/reading.py status`
6. **最终处理**：`python scripts/run.py finalize`

### 4. 时间控制策略
- 使用 `selected_k: 5` 减少处理论文数量
- 优先处理高相关度论文
- 避免完整深度模式，使用快速模式

## 预期结果
- [ ] 成功初始化深度检索
- [ ] 检索到相关论文
- [ ] 生成初步分析报告
- [ ] 识别潜在创新点

## 实际测试记录
### 测试日期: 2026-11-24
### 测试环境: paper-cat 虚拟环境

### 步骤 1: 环境验证
#### 执行命令:
```bash
python scripts/doctor.py
```

#### 执行结果:
```
[环境自检通过]
```

### 步骤 2: 深度检索测试
#### 执行命令:
```bash
python scripts/run.py init --task test_deep_mac --brief test_mac/deep/brief.json
```

#### 预期结果:
```
[初始化成功]
```

## 备注
由于深度检索流程较长，建议在空闲时间运行完整测试。当前测试重点验证环境配置和基本功能。