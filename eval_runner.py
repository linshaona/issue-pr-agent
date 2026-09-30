import argparse
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


def print_stability_report(all_results: dict[str, list[EvalResult]]):
    """多轮评测的稳定性汇总:单次通过率是抽样,稳定率才是测量"""
    print("\n" + "=" * 92)
    print("                      🎲 稳定性汇总 (STABILITY REPORT)")
    print("=" * 92)
    header = f"{'用例 ID':<24} | {'稳定率':<8} | {'平均轮数':<8} | {'平均Token':<10} | {'平均耗时':<8}"
    print(header)
    print("-" * 92)

    total_pass, total_runs = 0, 0
    stable_cases = 0
    for case_id, results in all_results.items():
        n_pass = sum(1 for r in results if r.passed)
        avg_rounds = sum(r.rounds for r in results) / len(results)
        avg_tokens = sum(r.tokens for r in results) / len(results)
        avg_dur = sum(r.duration for r in results) / len(results)
        total_pass += n_pass
        total_runs += len(results)
        if len({r.passed for r in results}) == 1:
            stable_cases += 1
        print(f"{case_id:<26} | {f'{n_pass}/{len(results)}':<9} | {avg_rounds:<9.1f} | "
              f"{avg_tokens:<11.0f} | {avg_dur:.1f}s")

    print("-" * 92)
    print(f"🎯 总体单次通过率: {total_pass / total_runs * 100:.1f}% ({total_pass}/{total_runs})")
    print(f"📌 结果稳定的用例: {stable_cases}/{len(all_results)}"
          f" (通过/未通过在多次运行间不翻转)")
    print("=" * 92 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Agent 基准自动化评测")
    parser.add_argument("--runs", type=int, default=1,
                        help="每个用例重复运行的次数(>1 时输出稳定性汇总)")
    parser.add_argument("--rewoo", action="store_true",
                        help="用 ReWOO 模式(Planner/Worker/Solver)评测")
    args = parser.parse_args()

    dataset = load_dataset()
    print(f"🚀 开始执行 Agent 基准自动化评测，共 {len(dataset)} 个用例 × {args.runs} 次...")

    base_url, api_key, model_name = init_agent_args()
    client = OpenAI(base_url=base_url, api_key=api_key)

    # 实例化 Agent(两种模式同接口,Scorer 无感)
    if args.rewoo:
        from src.rewoo import ReWOOAgent
        from src.tools import registry as default_registry
        agent = ReWOOAgent(client=client, model=model_name,
                           registry=default_registry,
                           solve_system=Agent.DEFAULT_SYSTEM_PROMPT)
    else:
        agent = Agent(client=client, model=model_name, max_tokens=8192)

    all_results: dict[str, list[EvalResult]] = {}

    for idx, case in enumerate(dataset, 1):
        print(f"\n[{idx}/{len(dataset)}] 正在评测: {case['id']} - {case['title']}")
        print(f"    输入: {case['issue_text'][:60]}...")

        case_results: list[EvalResult] = []
        for run_idx in range(1, args.runs + 1):
            # run() 内部 _init_conversation 会重置会话,Agent 实例可安全复用
            agent.run(case["issue_text"], max_rounds=6)
            result = Scorer.evaluate(case, agent.last_trajectory)
            case_results.append(result)
            print(f"    run {run_idx}/{args.runs}: {result.reason} "
                  f"(轮数: {result.rounds}, token: {result.tokens}, {result.duration:.1f}s)")

        all_results[case["id"]] = case_results

    # 单次跑保留原计分板;多次跑输出稳定性汇总
    if args.runs == 1:
        print_scorecard([r for results in all_results.values() for r in results])
    else:
        print_stability_report(all_results)


if __name__ == "__main__":
    main()