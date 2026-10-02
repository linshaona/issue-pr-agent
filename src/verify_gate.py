"""切片6 · P14/L38 验证门:确定性函数,同样输入必得同样结论,无 LLM 参与。

block 级发现直接否决 passed;warn 只注释不拦路。
取证由调用方完成(request_pr 收集工件),门只做纯判定——
同样的工件集永远得出同样的结论。
"""
PROTECTED_BRANCHES = {"main", "master"}


def verify(test_records: list[dict], branch: str,
           diff_stat: str, dup_pr_exists: bool | None) -> dict:
    """验证门:检查测试取证、diff、分支安全、重复 PR。

    :param test_records: run_tests 写入 data/test_runs.jsonl 的执行记录(按时间序)
    :param branch: 当前分支名(取证自 git)
    :param diff_stat: 相对目标分支的 diff --stat 输出(空 = 无改动)
    :param dup_pr_exists: 该分支是否已有开放 PR;None = API 查询失败,状态未知
    :return: {"passed": bool, "findings": [...], "summary": str}
    """
    findings: list[dict] = []

    def add(check: str, severity: str, detail: str):
        findings.append({"check": check, "severity": severity, "detail": detail})

    # block 1: 测试取证 —— "从未运行"与"运行失败"同样不可放行:
    # 没有证据 = agent 可能根本没验,门替它记忆这件事,不靠它自觉
    if not test_records:
        add("test_evidence", "block",
            "没有任何测试执行记录:必须先真实运行 run_tests 再请求开 PR。")
    else:
        last = test_records[-1]
        if last.get("exit_code") != 0:
            add("test_evidence", "block",
                f"最近一次测试未通过(exit_code={last.get('exit_code')},"
                f"命令: {last.get('command')})。修复后重跑测试。")

    # block 2: diff 非空 —— 空手开 PR
    if not (diff_stat or "").strip():
        add("empty_diff", "block", "分支相对目标分支没有任何改动,不能开 PR。")

    # block 3: 分支安全 —— 红线
    if branch in PROTECTED_BRANCHES:
        add("protected_branch", "block",
            f"当前分支 '{branch}' 受保护,改动不允许走 PR 流程。")

    # block 4 / warn: 重复 PR —— API 状态未知时降级为 warn,不因网络故障阻断
    if dup_pr_exists is True:
        add("duplicate_pr", "block", "该分支已存在开放 PR,不要重复创建。")
    elif dup_pr_exists is None:
        add("duplicate_pr", "warn",
            "无法确认是否已有开放 PR(API 查询失败),请人工确认。")

    n_block = sum(1 for f in findings if f["severity"] == "block")
    n_warn = sum(1 for f in findings if f["severity"] == "warn")
    passed = n_block == 0
    return {"passed": passed, "findings": findings,
            "summary": f"{n_block} block / {n_warn} warn"}
