import json
import time
from openai import OpenAI
from src.agent import Agent
from src.init_func import init_agent_args
from src.eval import Scorer, EvalResult

DATASET_PATH = "data/eval_dataset.json"


def load_dataset():
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def print_scorecard(results: list[EvalResult]):
    """打印漂亮的评测大板"""
    print("\n" + "=" * 92)
    print("                      📊 AGENT BENCHMARK SCORECARD (基准评测大板)")
    print("=" * 92)
    header = f"{'用例 ID':<22} | {'文件定位':<8} | {'关键词覆盖':<10} | {'步数':<5} | {'Token':<7} | {'耗时':<7} | {'状态'}"
    print(header)
    print("-" * 92)

    total_passed = 0
    total_tokens = 0
    total_duration = 0.0

    for r in results:
        if r.passed:
            total_passed += 1
        total_tokens += r.tokens
        total_duration += r.duration

        file_mark = "✅ 命中" if r.file_hit else "❌ 丢失"
        status_mark = "🟢 PASS" if r.passed else "🔴 FAIL"
        coverage_str = f"{r.keyword_coverage:.0%}"
        duration_str = f"{r.duration:.1f}s"

        print(
            f"{r.case_id:<24} | {file_mark:<10} | {coverage_str:<12} | {r.rounds:<7} | {r.tokens:<8} | {duration_str:<8} | {status_mark}"
        )

    print("-" * 92)
    pass_rate = (total_passed / len(results)) * 100
    avg_duration = total_duration / len(results)
    avg_tokens = total_tokens / len(results)

    print(f"🎯 最终通过率: {pass_rate:.1f}% ({total_passed}/{len(results)})")
    print(f"⚡ 平均耗时: {avg_duration:.2f} 秒 / 题  |  💰 平均消耗: {avg_tokens:.0f} Tokens / 题")
    print("=" * 92 + "\n")


def main():
    dataset = load_dataset()
    print(f"🚀 开始执行 Agent 基准自动化评测，共 {len(dataset)} 个用例...")

    base_url, api_key, model_name = init_agent_args()
    client = OpenAI(base_url=base_url, api_key=api_key)

    # 实例化 Agent（给 6 步充足思考空间）
    agent = Agent(client=client, model=model_name, max_tokens=8192)

    eval_results: list[EvalResult] = []

    for idx, case in enumerate(dataset, 1):
        print(f"\n[{idx}/{len(dataset)}] 正在评测: {case['id']} - {case['title']}")
        print(f"    输入: {case['issue_text'][:60]}...")

        # 运行 Agent 排查
        agent.run(case["issue_text"], max_rounds=6)

        # 自动打分
        result = Scorer.evaluate(case, agent.last_trajectory)
        eval_results.append(result)
        print(f"    结果: {result.reason} (步数: {result.rounds}, 耗时: {result.duration:.1f}s)")

    # 打印最终计分板
    print_scorecard(eval_results)


if __name__ == "__main__":
    main()