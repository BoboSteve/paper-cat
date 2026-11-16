# 运行环境（Windows / macOS / Linux）

skill 的脚本只依赖 Python 3.10+ 标准库，外加三样可选组件。所有命令写成 `python scripts/...`：Windows 上 `python` 指工具环境里的解释器（如 `<tool_root>/.venv/Scripts/python.exe`），macOS/Linux 上是 `<tool_root>/.venv/bin/python`。`runtime.local.json` 的 `python` 字段记录实际路径，Agent 应优先使用它。

## 组件

| 组件 | 用途 | Windows | macOS | Linux |
|---|---|---|---|---|
| Python 3.10+（独立 venv） | 运行全部脚本 | ✓ | ✓ | ✓ |
| `uv` / `uvx` | 运行固定版本的 paper-search-mcp（多源检索） | ✓ | ✓ | ✓ |
| PyMuPDF（`pip install "pymupdf>=1.24,<2"`） | 按页、按阅读顺序抽取全文（推荐） | ✓ | ✓ | ✓ |
| poppler `pdftotext`（备选） | 无 PyMuPDF 时抽取全文 | TeX Live / poppler | `brew install poppler` | 发行版包 |
| Jev 排序模型 | 候选排序 | llama.cpp + GGUF（NVIDIA） | MLX 8-bit（Apple Silicon） | llama.cpp + GGUF |
| `openreview-py`（`requirements-openreview.txt`） | 路径 A 的 OpenReview 枚举 | ✓ | ✓ | ✓ |

## 首次配置

1. 建立独立工具目录与 venv，安装 `uv`、PyMuPDF，以及路径 A 需要时的 `requirements-openreview.txt`。
2. 复制模板为 `runtime.local.json`：Windows/Linux 用 `runtime.example.windows.json`，Apple Silicon 用 `runtime.example.macos.json`，把 `<...>` 换成本机路径。模型文件放在工具目录，不放进 skill。
3. 凭据只放在 `~/.config/paper-search-mcp/.env`（或 `PAPER_SEARCH_MCP_ENV_FILE`）。用交互脚本录入并当场测试，输入不回显：
   ```bash
   python scripts/configure_env.py openreview
   python scripts/configure_env.py semantic
   ```
   OpenAlex key 写为 `OPENALEX_API_KEY=...`；可选 `UNPAYWALL_EMAIL=...` 打开 Unpaywall 下载渠道。不要把密钥贴进对话。
4. 运行 `python scripts/preflight.py --require tools --allow-degraded --out preflight.json` 查看各来源状态。缺账号的来源记为 degraded，流程继续使用可用来源，并在报告中写明缺口。
5. 等级注册表：`python scripts/build_registry.py --cache-dir <cache_dir>`。它来自第三方 CCF/分区数据的固定快照，正式使用前按需要的年度核对。

## 模型服务

```bash
python scripts/jev_service.py start    # 启动并等待 /health
python scripts/jev_service.py status
python scripts/jev_service.py stop     # 只停止本 skill 启动的进程
```

`run.py rank` 会在需要时自动启动、用完自动关闭以释放显存。`service.mode` 可以是 `managed`（脚本自己启动 `processes` 中的命令）或 `command`（委托已有脚本，例如本机的 PowerShell 启动器）。只监听 127.0.0.1，端口被占用时不会覆盖其他程序。

上下文上限（如 4096 tokens）包含完整请求；超长摘要会失败并保留失败记录，不会静默截断。更换模型、量化或上下文后，旧评分按指纹自动失效。

## 编码与路径（Windows 常见坑）

- 所有脚本显式按 UTF-8 读写；仍建议用 `python -X utf8` 运行。
- **不要用 Git Bash 的 heredoc（`<<EOF`）或 `echo` 写含中文的 JSON/CSV**：Windows 下会写成 GBK，导致读取失败。用 Agent 的文件写入工具，或 Python 以 `encoding="utf-8"` 写文件。
- 任务目录、PDF 和报告路径用绝对路径；路径中有空格时加引号。
- 后台模型进程会继承调用方的输出句柄；`jev_service.py` 已把输出重定向到日志文件，避免 Agent 的 shell 一直等待。

## 安装到各个 Agent harness

```bash
python scripts/install_skill.py --target claude            # ~/.claude/skills/paper-cat（Claude Code）
python scripts/install_skill.py --target codex             # ~/.codex/skills/paper-cat（Codex）
python scripts/install_skill.py --target claude --project <repo>   # 只在某个项目内启用
python scripts/install_skill.py --target <其他 harness 的 skills 目录>
```

默认不复制本机的 `runtime.local.json`；在同一台机器上安装时加 `--with-runtime`。SKILL.md 不依赖某个 harness 专有的工具名：需要浏览器的步骤（如 ACM DL 下载）写成"用 Agent 的浏览器工具，没有则交给用户"。修改工作副本后重新运行安装命令同步，避免多个副本规则分叉。
