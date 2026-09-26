import json
from dataclasses import dataclass
from typing import Any
from src.agent import Trajectory


@dataclass
class EvalResult:
    case_id: str
    title: str
    passed: bool              # 最终是否及格
    file_hit: bool            # 是否准确定位到目标文件
    keyword_coverage: float   # 关键词覆盖率 (0.0 ~ 1.0)
    matched_keywords: list[str]
    missing_keywords: list[str]
    rounds: int               # 经历轮数
    tokens: int               # 消耗 Token
    duration: float           # 耗时（秒）
    reason: str               # 判定原因说明


class Scorer:
    """自动化客观评分器：比对 Agent 的运行轨迹与 Ground Truth"""

    @staticmethod
    def evaluate(case: dict[str, Any], trajectory: Trajectory) -> EvalResult:
        ground_truth = case.get("ground_truth", {})
        target_files = ground_truth.get("target_files", [])
        expected_keywords = ground_truth.get("expected_keywords", [])

        # 1. 检查是否超时死循环
        if trajectory.is_timeout:
            return EvalResult(
                case_id=case["id"],
                title=case["title"],
                passed=False,
                file_hit=False,
                keyword_coverage=0.0,
                matched_keywords=[],
                missing_keywords=expected_keywords,
                rounds=trajectory.total_rounds,
                tokens=trajectory.total_tokens,
                duration=trajectory.duration,
                reason="❌ 达到最大步数超时熔断",
            )

        # 收集 Agent 产生的所有文本（包含报告和调用参数）
        report_text = trajectory.final_report.lower()
        tool_activities = " ".join([
            f"{c.name} {json.dumps(c.args)} {c.output_snippet}"
            for c in trajectory.tool_calls
        ]).lower()
        all_agent_text = f"{report_text} {tool_activities}"

        # 2. 文件定位评分：目标文件是否被 Agent 提及或读取
        file_hit = False
        for target in target_files:
            # 兼容绝对路径或斜杠差异
            clean_target = target.replace("\\", "/").lower()
            if clean_target in all_agent_text or target.split("/")[-1].lower() in all_agent_text:
                file_hit = True
                break

        # 3. 根因关键词覆盖度评分
        matched_kw = []
        missing_kw = []
        for kw in expected_keywords:
            if kw.lower() in report_text:
                matched_kw.append(kw)
            else:
                missing_kw.append(kw)

        coverage = len(matched_kw) / max(len(expected_keywords), 1)

        # 4. 综合通过条件：文件命中 + 关键词覆盖率 >= 50%
        passed = file_hit and (coverage >= 0.5)

        reasons = []
        if not file_hit:
            reasons.append("未定位到目标文件")
        if coverage < 0.5:
            reasons.append(f"关键词覆盖不足({coverage:.0%})")
        reason_str = "✅ 通过" if passed else f"❌ 未通过 ({'，'.join(reasons)})"

        return EvalResult(
            case_id=case["id"],
            title=case["title"],
            passed=passed,
            file_hit=file_hit,
            keyword_coverage=coverage,
            matched_keywords=matched_kw,
            missing_keywords=missing_kw,
            rounds=trajectory.total_rounds,
            tokens=trajectory.total_tokens,
            duration=trajectory.duration,
            reason=reason_str,
        )