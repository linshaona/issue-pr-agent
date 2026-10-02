"""P14/L39 评审 Agent:建造者与评分者分离。

核心命题:写代码的 agent 不能给自己的工作打分。评审员是第二个循环——
不同系统提示词、不同目标、对 builder 产物只有只读权。验证门(L38)管
确定性事实(测试跑没跑、exit 0 没有),评审员管定性判断(修的是不是
对的问题、假设有没有记录、交接能不能接手)。两者缺一不可,且不许让
评审员重做门已经证明的事。

偏差缓解(L39 生产模式节,按性价比取三样):
  总分由代码重算,不信模型的算术;温度钉 0.2;量规写明"宁低勿高"
  (反冗长偏置)。置信度下限 0.6(Exercise 3):最低维置信度不达标时
  报告标记 low_confidence,只作参考不作结论。
"""
import json
import time

REVIEWER_SYSTEM = """你是代码评审员,与写代码的建造者是分离的角色:你只读不写,也不修补。
按五维量规评审一次 agent 修复任务,每维 0-2 分:
1. problem_fit 问题契合:修改是否解决了任务本身,而非一个邻近任务;
2. scope_discipline 范围纪律:改动是否被限制在任务范围内;
3. assumptions 假设记录:隐含假设是否可见、是否合理;
4. verification_quality 验证质量:测试证据是否真的证明了目标(而不是证明了更弱的版本);
5. handoff_readiness 交接就绪:报告能否让下一个会话干净地接手。
只输出一个 JSON 对象:
{"scores": {"problem_fit": 0, "scope_discipline": 0, "assumptions": 0,
            "verification_quality": 0, "handoff_readiness": 0},
 "confidence": {"problem_fit": 0.0, "scope_discipline": 0.0, "assumptions": 0.0,
                "verification_quality": 0.0, "handoff_readiness": 0.0},
 "verdict_reason": "一句话说明最低分维度的理由"}
评分纪律:给 0 分或 1 分必须在 verdict_reason 说明;宁低勿高——
评审员的系统性偏差是给冗长输出加分,这里反着来。"""

DIMS = ("problem_fit", "scope_discipline", "assumptions",
        "verification_quality", "handoff_readiness")
SOFT_FAIL_BELOW = 7
HARD_FAIL_BELOW = 5
CONFIDENCE_FLOOR = 0.6


def build_inputs(trajectory, test_records: list[dict]) -> dict:
    """从 builder 轨迹提取评审工件:编辑记录、工具输出摘要、测试证据、最终报告。"""
    edits, tool_log = [], []
    for call in trajectory.tool_calls:
        line = f"[{call.name}] {call.output_snippet[:200]}"
        tool_log.append(line)
        if call.name == "edit_file":
            edits.append({"file_path": call.args.get("file_path"),
                          "old_text": str(call.args.get("old_text", ""))[:300],
                          "new_text": str(call.args.get("new_text", ""))[:300]})
    last_exit = test_records[-1].get("exit_code") if test_records else None
    return {"edits": edits,
            "tool_log": tool_log[:15],
            "test_evidence": {"ran": bool(test_records), "last_exit_code": last_exit},
            "final_report": (trajectory.final_report or "")[:1500]}


def review(client, model: str, issue_text: str, trajectory,
           test_records: list[dict]) -> dict:
    """评审一次运行。LLM 打各维分数,总分与裁决由代码确定性重算。"""
    artifacts = build_inputs(trajectory, test_records)
    user = (f"【任务(issue 原文)】\n{issue_text[:1200]}\n\n"
            f"【建造者的编辑】\n{json.dumps(artifacts['edits'], ensure_ascii=False)}\n\n"
            f"【工具调用与输出摘要】\n" + "\n".join(artifacts["tool_log"]) + "\n\n"
            f"【测试证据】\n{json.dumps(artifacts['test_evidence'], ensure_ascii=False)}\n\n"
            f"【建造者的最终报告(前1500字)】\n{artifacts['final_report']}\n\n"
            f"请按量规输出 JSON。")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        temperature=0.2,
        messages=[{"role": "system", "content": REVIEWER_SYSTEM},
                  {"role": "user", "content": user}])
    msg = resp.choices[0].message
    body = (msg.content or getattr(msg, "reasoning", "") or "")
    body = body[body.index("{"): body.rindex("}") + 1]
    data = json.loads(body, strict=False)

    # 确定性重算:分数钳位到 0-2,总分自己加,裁决自己下
    scores = {}
    for dim in DIMS:
        raw = data.get("scores", {}).get(dim, 0)
        scores[dim] = max(0, min(2, int(raw)))
    total = sum(scores.values())
    if total < HARD_FAIL_BELOW or 0 in scores.values():
        verdict = "hard_fail"
    elif total < SOFT_FAIL_BELOW:
        verdict = "soft_fail"
    else:
        verdict = "pass"
    confidence = {d: max(0.0, min(1.0, float(data.get("confidence", {}).get(d, 0))))
                  for d in DIMS}
    return {
        "scores": scores,
        "total": total,
        "verdict": verdict,
        "verdict_reason": str(data.get("verdict_reason", ""))[:200],
        "confidence_min": min(confidence.values()),
        "low_confidence": min(confidence.values()) < CONFIDENCE_FLOOR,
        "reviewed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "review_tokens": resp.usage.total_tokens if resp.usage else 0,
    }
