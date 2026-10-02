"""L26 失败模式标签器:对 Trajectory 做签名式检测(P14/L26 Build)。

五种业界高频失败模式中,四个可直接从轨迹签名检出;
scope_creep 需要 git diff 文件白名单数据,留给权限分层(P15/L10)接线。
检测必须确定性(纯 stdlib、零模型调用)——这一点与验证门同源:
标签是监控信号,不是模型的自评。
签名式检测的已知代价:可能误报(如报告引用"已修复但测试未通过"会被
success_hallucination 命中)。廉价信号先上线,语义判断交给人工复核。
"""
from src.context_manager import Trajectory

# 工具层错误约定(tools.py):自身报错用"错误:"前缀
ERROR_PREFIX = "错误:"
# 测试失败标记(run_tests):合法的测试不过,归入级联,不算工具误用
TEST_FAIL_MARK = "❌"
# 报告里的自称成功标记
SUCCESS_MARKS = ("✅", "成功", "已修复")


def hallucinated_action(traj: Trajectory, registry) -> list[int]:
    """幻觉动作:调用了不存在的工具,或参数键 ⊄ schema properties(幻觉键)。
    返回命中的步序号。注册表执行层的键过滤发生在 record 之后,
    所以轨迹里记录的是模型原始产出的 args——幻觉键在这里可见。"""
    schemas = {s["function"]["name"]: set(s["function"]["parameters"].get("properties", {}))
               for s in registry.get_schemas()}
    hits = []
    for i, call in enumerate(traj.tool_calls):
        allowed = schemas.get(call.name)
        if allowed is None:
            hits.append(i)                       # 工具不存在
        elif not set(call.args).issubset(allowed):
            hits.append(i)                       # 参数键幻觉
    return hits


def tool_misuse(traj: Trajectory) -> list[int]:
    """工具误用:工具存在、键也对,但运行时报错(错误: 前缀)。返回步序号。"""
    return [i for i, c in enumerate(traj.tool_calls)
            if c.output_snippet.startswith(ERROR_PREFIX)]


def cascade_radius(traj: Trajectory) -> int:
    """级联半径(L26 Exercise 3):首个失败步之后仍继续执行的步数。
    失败 = 工具报错(错误:)或测试不过(❌);0 = 无失败或首败即终。
    注意:半径度量的是暴露窗口(在坏地基上还跑了多少步);
    验证门在 request_pr 处的拦截是止损,不会反映在这个数字里。"""
    for i, c in enumerate(traj.tool_calls):
        if c.output_snippet.startswith(ERROR_PREFIX) or TEST_FAIL_MARK in c.output_snippet:
            return len(traj.tool_calls) - 1 - i
    return 0


def success_hallucination(traj: Trajectory, test_records: list[dict]) -> bool:
    """成功幻觉:报告自称成功,但测试证据缺失或末次 exit≠0。
    L26 核心命题的反面取证——"完成"必须由环境状态背书,而非模型自述。"""
    report = traj.final_report or ""
    if not any(mark in report for mark in SUCCESS_MARKS):
        return False
    if not test_records:
        return True                              # 自称成功却没有任何测试取证
    return test_records[-1].get("exit_code") != 0


def tag(traj: Trajectory, registry, test_records: list[dict]) -> dict:
    """汇总一次运行的全部标签。返回 {"modes": [...], "cascade_radius": int, "detail": {...}}"""
    halluc = hallucinated_action(traj, registry)
    misuse = tool_misuse(traj)
    radius = cascade_radius(traj)
    fake_success = success_hallucination(traj, test_records)

    modes = []
    if halluc:
        modes.append("hallucinated_action")
    if misuse:
        modes.append("tool_misuse")
    if radius:
        modes.append("cascade")
    if fake_success:
        modes.append("success_hallucination")
    return {"modes": modes, "cascade_radius": radius,
            "detail": {"hallucinated_steps": halluc, "misuse_steps": misuse}}
