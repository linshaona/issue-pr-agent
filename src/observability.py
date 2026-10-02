"""P17/L13 可观测:运行轨迹导出 + 聚合摘要。

层次选择(L13 "陷阱"节):在 SDK/账本层观测——数据全部来自
Trajectory/ContextAccountant/PermissionPolicy/标签器,不耦合任何
agent 框架;属性名对齐 OpenTelemetry GenAI 语义约定(2025 发布),
换观测后端(Langfuse/Phoenix/Helicone)不丢遥测。

采样(L13 教条:100% 错误、100% 高成本、5% 成功):当前规模
全量保留;超过 1M trace/日再按规则采样——聚合永远全量。
"""
import json
import os
import time
from collections import Counter

RUNS_PATH = "data/runs.jsonl"


def export_run(case_id: str, agent_kind: str, trajectory,
               result, tags: dict, policy) -> dict:
    """一次运行 → 一条 JSONL 轨迹。属性名对齐 OTel GenAI 约定。"""
    trace = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case_id": case_id,
        "gen_ai.agent.type": agent_kind,
        # usage(L13:token 与时长是成本治理的原料)
        "gen_ai.usage.total_tokens": trajectory.total_tokens,
        "gen_ai.usage.rounds": trajectory.total_rounds,
        "duration_ms": int(trajectory.duration * 1000),
        "is_timeout": trajectory.is_timeout,
        # 评测裁决(注意与验证门口径的分歧——L26 实证)
        "eval.passed": result.passed,
        "eval.reason": result.reason,
        "eval.keyword_coverage": round(result.keyword_coverage, 3),
        "eval.file_hit": result.file_hit,
        # L26 失败模式
        "failure_modes": tags["modes"],
        "cascade_radius": tags["cascade_radius"],
        # L10/L14 治理状态
        "policy.mode": policy.mode,
        "policy.call_counts": policy.call_counts,
        "breaker.open_tools": sorted(policy.breaker._opened_at.keys()),
    }
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    with open(RUNS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return trace


def summarize(path: str = RUNS_PATH) -> dict:
    """聚合 data/runs.jsonl:通过率、成本、失败模式分布、治理事件。"""
    if not os.path.exists(path):
        return {"runs": 0}
    runs = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    modes: Counter = Counter()
    for r in runs:
        modes.update(r.get("failure_modes", []))
    return {
        "runs": len(runs),
        "pass_rate": sum(1 for r in runs if r.get("eval.passed")) / len(runs),
        "avg_tokens": sum(r.get("gen_ai.usage.total_tokens", 0) for r in runs) / len(runs),
        "avg_duration_s": sum(r.get("duration_ms", 0) for r in runs) / len(runs) / 1000,
        "failure_modes": dict(modes),
        "breaker_events": sum(1 for r in runs if r.get("breaker.open_tools")),
        "by_model": dict(Counter(r.get("gen_ai.request.model", "?") for r in runs)),
    }


def print_summary(summary: dict) -> None:
    if summary.get("runs", 0) == 0:
        return
    print("\n" + "=" * 70)
    print("           📈 运行可观测汇总 (OBSERVABILITY · P17/L13)")
    print("=" * 70)
    print(f"  累计运行 {summary['runs']} 次 | 通过率 {summary['pass_rate']:.0%}"
          f" | 平均 {summary['avg_tokens']:,.0f} token / {summary['avg_duration_s']:.0f}s")
    if summary.get("failure_modes"):
        print(f"  失败模式: {summary['failure_modes']}")
    if summary.get("breaker_events"):
        print(f"  熔断器 OPEN 事件: {summary['breaker_events']} 次")
    print(f"  轨迹落盘: {RUNS_PATH} (OTel GenAI 属性名,全量保留)")
    print("=" * 70 + "\n")
