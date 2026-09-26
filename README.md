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
| 5 | 上下文工程 + 计划-执行(ReWOO) | 🔨 进行中 |
| 6 | 验证门 + git 工作流工具 | ⬜ |
| 7 | MCP 服务器化 | ⬜ |
| 8 | 安全:沙箱 + 注入防御 + 权限模式 | ⬜ |
| 9 | 可观测 + 成本 + 发布 | ⬜ |

## 快速开始(切片 0)

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
