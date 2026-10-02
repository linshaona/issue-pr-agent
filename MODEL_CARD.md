# Issue→PR 自主编码 Agent · 系统卡(System Card)

> 依据 P18/L26(Mitchell et al. 2019 模型卡 → Gebru et al. 2018 数据表 → 2024-2026 系统卡)编写。
> 系统卡覆盖端到端:架构、安全能力、提示注入防护、数据外传检测、评测数据与限制。
> 所有数字来自 2026-09-29 至 2026-10-02 的真实运行记录,无合成数据。

## 系统详情

| 项 | 值 |
|---|---|
| 系统名 | issue-pr-agent(GitHub Issue→PR 自主编码 Agent) |
| 基座模型 | qwen3.5:4B(本地局域网 Ollama,context 65536) |
| 架构 | ReWOO(Planner/Worker/Solver)+ 参数物化(B策略)+ 验证门;同注册表可切 ReAct |
| 工具面 | 10 工具:list_files / read_file / grep / search_code_semantic / edit_file / run_tests / create_branch / commit_changes / push_branch / request_pr |
| 模型在环次数 | 2+M(Planner + M 次物化 + Solver),与节点数无关;评审阶段 +1 次 |

## 预期用途 / 非预期用途

- **预期**:公开仓库的单文件级 bug 修复——读 issue → 建分支 → 定位 → 修改 → 测试 → 经验证门开 PR。
- **非预期**:多仓库重构、生产数据库操作、无人工复核的 main 直推(工具红线拒绝)、凭据接触(canary 布防)。

## 安全能力栈(四层,全部确定性、零模型调用)

```
① PermissionPolicy(execute 前):kill switch → canary → 熔断器 → 模式×风险类别
   (READ/WRITE/EXEC/EXTERNAL × plan/default/unattended/bypass)→ 注入签名 → 次数帽 → token 熔断
② 工具红线:commit 拒 main/master;run_tests 命令白名单(python/pytest/pip,禁 curl/管道/链式)
③ 验证门:request_pr 唯一交付口;测试取证 exit=0 才放行;块级发现不可被 agent 覆盖
④ 失败模式标签器(事后审计):幻觉动作/工具误用/级联半径/成功幻觉,OTel 轨迹全量落盘
```

## 提示注入防护(L27)

- Issue 正文与工具输出一律视为不可信数据;Planner/物化器提示词声明"指令性文本=数据"(弱防线,非强制层)
- 强制层:参数注射签名扫描(中英指令形短语,动词+宾语结构防误伤)+ run_tests 白名单
- 诱饵:cwd 之外的假凭据文件(canary),读取即报警;哨兵扫描覆盖工具输出

## 评测数据(Mission 验收 + 基准)

**真实仓库验收(2026-10-01)**:自建靶场 linshaona/demo-repo + issue#1,agent 全自动产出
[PR #2](https://github.com/linshaona/demo-repo/pull/2)(mergeable_state=clean,diff 1 文件 +1/-1)。
三轮迭代记录了三类真实故障:物化丢缩进(验证门拦截)、Planner JSON 断裂(重试环 harness bug)、
`__pycache__` 入 PR(提交卫生,已加永久防御)。

**基准评测(2026-10-02,4 用例 × 2 次,ReWOO)**:

| 指标 | 值 | 备注 |
|---|---|---|
| 单次通过率(评测口径) | 5/8 = 62.5% | 文件命中 + 关键词覆盖 |
| 稳定用例 | 3/4 | 结果在多次运行间不翻转 |
| **验证门口径通过率** | **显著低于评测口径** | 5 个 pass 中 4 个自称成功但测试证据 exit≠0——真开 PR 会被门拒 |
| 平均成本 | 24K token / 12.8 min 每题 | 峰值 84K token / 25 min(重试环,已被 L10 次数帽治理) |
| 失败模式分布 | 工具误用 ×7 / 级联 ×7 / 成功幻觉 ×5(含 1 误报已修) | 标签器自动产出 |

**环境敏感性事故(重要发现)**:服务器迁移丢失 embedding 模型后,同一份代码 0/8;
修复后 62.5%。agent 质量断言必须绑定环境健康——迁移 checklist(Ollama HOST/上下文长度/两个模型)已制度化。

## 已知限制

1. **4B 模型语义质量是天花板残余**:定位类任务(eval-03)证据充足仍找不目标;物化器偶发 JSON 断裂(重试可救)
2. **评测口径 ≠ 可交付口径**:62.5% 的通过率按验证门/评审员口径要打折——这是设计使然(两套"完成"定义),也是本系统的核心研究记录
3. scope_creep 检测留白:需要 issue 范围的语义解析
4. Windows 特有:json.dumps 反斜杠转义曾使 canary 路径检查失配(已修,探测器需被测试的活教材)
5. 单机栈的 kill switch 是尽力而为(标志文件在 cwd 之外,但 edit_file 理论可达);真保证需容器隔离

## 伦理考量 / 使用建议

- 无人值守运行请用 `unattended` 模式(收紧帽)并确认爆炸半径:无凭据挂载、无未授权出口
- 评审员(LLM judge)结论带置信度下限;低置信度只作参考——分类器是一层,不是解法
- 全部轨迹落盘 `data/runs.jsonl`(OTel GenAI 属性名),可接入任意观测后端复核本卡的每个数字
