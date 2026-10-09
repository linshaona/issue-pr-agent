# issue-pr-agent

GitHub Issue → PR 自主编码 Agent:输入一个 issue,Agent 自己理解需求、探索代码库、修改代码、跑测试验证,最后开 PR。

这是「AI Engineering from Scratch」课程的项目串课 Capstone(Capstone 16 号),从切片 0 开始逐层构建,每个切片配套学习支撑课程(见 LEARNING.md 的 Project spine)。

## MVP 范围(v0.1)

```text
GitHub issue(API 拉取)
  → Agent 循环:理解 → 规划 → 探索本地克隆仓库 → 编辑代码 → 跑测试
  → 建分支提交 → 开 PR
```

工具面 v0.1:`read_file` / `list_files` / `search_code` / `edit_file` / `run_tests` / `create_pr`
硬约束:本地沙箱克隆、最大迭代数、成本预算。
v0.1 不做:多 Agent、MCP 对外暴露、Web UI。

## 切片路线图

| 切片 | 交付 | 状态 |
|------|------|------|
| 0 | 项目骨架 + GitHub API 拉 issue + CLI | 完成✓ |
| 1 | Agent 循环跑通(读 issue → 决定动作 → 2 个工具) | 完成✓ |
| 2 | 工具注册表 + Schema 校验 + 结构化输出 | 完成✓ |
| 3 | 代码库 RAG(embedding 索引仓库) | 完成✓ |
| 4 | 评测基座(固定 issue 测试集 + 判分) | 完成✓ |
| 5 | 上下文工程 + 计划-执行(ReWOO) | 完成✓ |
| 6 | 验证门 + git 工作流工具 | 完成✓ |
| 7 | MCP 服务器化 | 完成✓ |
| 8 | 安全:沙箱 + 注入防御 + 权限模式 | 完成✓ |
| 9 | 可观测 + 成本 + 发布(Web 战术遥测控制台) | 完成✓ |

## 快速开始

### 1. Web 战术遥测控制台 (推荐)

首次运行先同步依赖(Web 控制台所需的 fastapi/uvicorn 已声明在 `pyproject.toml` 并由 `uv.lock` 锁定)：

```bash
uv sync
```

然后直接双击运行 [`start_web.bat`](start_web.bat) 或在终端启动 FastAPI 服务：

```bash
uv run uvicorn src.webapp:app --host 127.0.0.1 --port 8000 --reload
```

打开浏览器访问 `http://127.0.0.1:8000`，支持：
- **🎯 任务指挥台**: 一键装填基准评测题 (`data/eval_dataset.json`) 或四层安全防御红队探针、实时预览 L10 权限风险分级与预算配额、在线查看 `edit_file` 代码 Diff、工具调用轨迹与 L39 独立评审员五维量规打分（内置「✨ 演示轨迹」按钮可秒级预览全部可视化组件）。
- **📈 可观测与历史账本**: 实时聚合 `data/runs.jsonl` (OpenTelemetry GenAI 语义属性) 与 `data/test_runs.jsonl` 测试取证记录。
- **🛡️ 四层防御与系统卡**: 实时控制 L14 Kill Switch、监控 Canary 诱饵哨兵状态、检视 10 个注册工具的 `TOOL_RISK` 风险矩阵。
- **🌗 双主题切换**: 支持「暗色战术指挥台 (Tactical Dark Deck)」与「浅色工程蓝图 (Blueprint Light)」一键切换。

### 2. CLI 命令行模式

```bash
pip install -r requirements.txt
copy .env.example .env        # 填入 GITHUB_TOKEN(可选,推荐)
python main.py python/cpython#130000
# 也支持完整 URL:
python main.py https://github.com/owner/repo/issues/123
```

不带 `GITHUB_TOKEN` 可用,但限流 60 次/小时;带 token 5000 次/小时。

## 安全约定

- 所有密钥走 `.env`(已 gitignore),**绝不**把 key 写进源码或 config.json;
- Agent 的一切仓库操作只在 `src/` 下的沙箱克隆里进行(切片 1 起)。
