# issue-pr-agent

GitHub Issue → PR 自主编码 Agent:给一个 issue,Agent 自己理解需求、探索代码库、改代码、跑测试验证,最后开 PR。

「AI Engineering from Scratch」课程的项目串课 Capstone(Capstone 16 号),从切片 0 逐层构建,每个切片配套一节学习支撑课程。

## 30 秒看懂

| 问题 | 这个项目怎么答 |
|---|---|
| 是不是只会调 API? | Agent 内核自研:工具注册表 + Schema 校验 + ReAct / ReWOO 双范式(Planner–Worker–Solver + 参数物化)+ 上下文记账与压缩,不依赖任何 Agent 框架 |
| 工具面有多大? | 10 个工具:`read_file` / `list_files` / `grep` / `search_code_semantic` / `edit_file` / `run_tests` / `create_branch` / `commit_changes` / `push_branch` / `request_pr`;整套经官方 MCP SDK 暴露为 MCP Server |
| 模型乱来怎么办? | 四层确定性防御(**零模型调用**):权限策略熔断 → 工具红线与命令白名单 → Canary 凭据诱饵 → Exit Code=0 验证门禁 |
| 效果怎么量化? | 4 用例 × 2 次基准 + 失败模式标签器;系统卡如实披露「评测口径 ≠ 验证门口径」——见 [MODEL_CARD.md](MODEL_CARD.md) |
| 真的开出过 PR 吗? | 自建靶场仓库端到端验收,产出 [demo-repo PR #2](https://github.com/linshaona/demo-repo/pull/2)(mergeable_state=clean,1 文件 +1/−1) |

## 工作流

```text
GitHub issue(API 拉取)
  → Agent 循环:理解 → 规划 → 探索仓库 → 编辑代码 → 跑测试
  → 建分支提交 → 过验证门 → 开 PR
```

硬约束:最大迭代轮数、成本预算、权限模式(`plan` / `default` / `unattended` / `bypass`)。
不做:多 Agent 编排——单 Agent + 计划-执行解耦已覆盖当前场景。

> ⚠️ **Agent 直接在你启动它的工作目录里读写文件**(搜索会跳过 `workspace/`),不存在"克隆到沙箱再改"这一步。请在**临时克隆或专用靶场仓库**里运行它,并依赖权限模式与验证门控制爆炸半径;真正的隔离需要容器(见 [MODEL_CARD.md](MODEL_CARD.md) 的已知限制)。

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
| 8 | 安全:注入防御 + 权限模式 + Canary | 完成✓ |
| 9 | 可观测 + 成本 + 发布(Web 战术遥测控制台) | 完成✓ |

## 快速开始

依赖用 [uv](https://docs.astral.sh/uv/) 管理(`fastapi` / `uvicorn` / `mcp` 等已锁在 `uv.lock`)。

```bash
uv sync
cp .env.example .env          # Windows: copy .env.example .env
```

`.env` 里填 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`(任意 OpenAI 兼容端点,含本地 Ollama)与可选的 `GITHUB_TOKEN`。不带 token 也能用,只是 GitHub API 限流 60 次/小时(带 token 5000 次/小时)。

### 1. Web 战术遥测控制台(推荐)

```bash
uv run uvicorn src.webapp:app --host 127.0.0.1 --port 8000 --reload
```

Windows 可直接双击 [`start_web.bat`](start_web.bat)(默认 8000,`set PORT=8020` 可覆盖),打开 http://127.0.0.1:8000:

- **任务指挥台**:一键装填基准评测题或四层防御红队探针、预览权限风险分级与预算配额、在线查看 `edit_file` 代码 Diff、工具调用轨迹与独立评审员五维量规打分(内置「演示轨迹」按钮可秒级预览);
- **可观测账本**:聚合 OpenTelemetry GenAI 语义属性的运行轨迹与测试取证记录;
- **四层防御与系统卡**:运行时 Kill Switch 开关、Canary 诱饵哨兵状态、10 个工具的 `TOOL_RISK` 风险矩阵;
- **双主题**:暗色战术指挥台 / 浅色工程蓝图一键切换。

> 控制台的数据来自运行时落盘的 `data/runs.jsonl` 等文件;这些属于本地取证数据,**未随仓库发布**(仅 `data/eval_dataset.json` 基准题入库)。

### 2. CLI

```bash
uv run python main.py python/cpython#130000            # ReAct 循环
uv run python main.py python/cpython#130000 --rewoo    # ReWOO 计划-执行
uv run python main.py https://github.com/owner/repo/issues/123
```

### 3. 当成 MCP Server 挂到别的客户端

```bash
uv run python src/mcp_server.py   # stdio:把 10 个工具暴露给任意 MCP 客户端
uv run python src/mcp_client.py   # 自检:连自己、列出工具并试调 grep / read_file
```

## 目录结构

```text
issue-pr-agent/
  main.py                 # CLI 入口(ReAct / --rewoo)
  eval_runner.py          # 基准评测入口(数据集 → 记分卡 → 稳定性/失败报告)
  src/agent.py            # ReAct 循环 + 执行前权限裁决
  src/rewoo.py            # Planner / Worker / Solver + 参数物化
  src/tools.py            # 工具注册表与 10 个工具
  src/permissions.py      # 权限模式 × 风险类别矩阵、kill switch、预算熔断
  src/tripwire.py         # Canary 凭据诱饵
  src/injection_guard.py  # 提示注入签名扫描
  src/verify_gate.py      # Exit Code=0 验证门(request_pr 唯一交付口)
  src/context_manager.py  # 上下文记账与压缩
  src/rag.py              # 代码库语义检索
  src/reviewer.py         # 独立评审员(LLM judge)
  src/failure_tags.py     # 失败模式标签器
  src/observability.py    # OTel GenAI 语义属性落盘
  src/eval.py             # 判分器(Scorer / EvalResult)
  src/mcp_server.py       # MCP 协议适配层(复用同一注册表)
  src/mcp_client.py       # MCP 自检客户端
  src/webapp.py           # FastAPI 控制台后端
  src/static/             # 控制台前端(原生 JS)
  data/eval_dataset.json  # 基准评测题(其余 data/ 为本地运行产物,不入库)
```

## 效果与限制

真实数字、失败归因与环境事故全部记录在 [MODEL_CARD.md](MODEL_CARD.md)(系统卡),包括:真实靶场 PR 一次通过、4 用例 × 2 次基准的通过率与稳定性、四类失败模式分布,以及「服务器迁移丢了 embedding 模型后同一份代码 0/8」这件事。

核心结论:**评测口径 ≠ 可交付口径**——自评通过但测试证据 `exit≠0` 的样本会被验证门拒绝。

## 安全约定

- 所有密钥走 `.env`(已 gitignore),**绝不**把 key 写进源码或 config.json;
- `commit_changes` 拒绝 main/master 直推;`run_tests` 走命令白名单(禁管道与链式命令);
- 交付口唯一:`request_pr` 必须带 `exit=0` 的测试取证才放行,块级发现不可被 Agent 覆盖;
- 提示注入按「指令性文本 = 数据」处理:Issue 正文与工具输出一律不可信,工具参数过签名扫描,`cwd` 外布 Canary 诱饵文件。
