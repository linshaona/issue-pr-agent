"""ReWOO:计划与执行分离(P14/L2)。
Planner 一次产出计划 → Worker 纯代码执行(0 次模型调用)→ Solver 一次汇总。
模型在环次数 = 2,与节点数无关。
"""
import json
import re
import time
from typing import Any
from pydantic import BaseModel, ValidationError

from src.context_manager import ToolCallRecord, Trajectory
from src.tools import ToolRegistry


class PlanNode(BaseModel):
    id: str      # "E1", "E2"...
    tool: str
    args: dict
    why: str     # 这一步在排查什么

class Plan(BaseModel):
    nodes: list[PlanNode]


PLANNER_SYSTEM = """你是一名代码排查计划器。根据 GitHub Issue 描述,产出一份用现有工具完成排查的计划。
规则:
1. 只能使用工具目录中列出的工具;
2. 节点 id 依次为 E1、E2、…;后面的节点参数里可用 "#E1" 占位符引用前面节点的完整输出;
3. 先检索定位(search_code_semantic / grep),再读取文件(read_file);不要猜测文件路径;
4. 节点数不超过 8;计划要产出足以撰写根因报告的证据;
5. 只输出 JSON,格式: {"nodes": [{"id":"...","tool":"...","args":{},"why":"..."}]}"""

# 占位符替换:递归处理 args 里的 "#E1"
def _substitute(value: Any, evidence: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"#(E\d+)", lambda m: evidence.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _substitute(v, evidence) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, evidence) for v in value]
    return value

# 计划验证:工具存在、引用先定义、节点数封顶
def _validate(plan: Plan, registry: ToolRegistry, max_nodes: int) -> str | None:
    names = {s["function"]["name"] for s in registry.get_schemas()}
    seen: set[str] = set()
    if len(plan.nodes) > max_nodes:
        return f"节点数 {len(plan.nodes)} 超过上限 {max_nodes}"
    for node in plan.nodes:
        if node.tool not in names:
            return f"节点 {node.id} 使用了不存在的工具 '{node.tool}'"
        for ref in re.findall(r"#(E\d+)", json.dumps(node.args, ensure_ascii=False)):
            if ref not in seen:
                return f"节点 {node.id} 引用了未先定义的 #{ref}"
        seen.add(node.id)
    return None


class ReWOOAgent:
    """与 Agent 同接口(run/double last_trajectory),评测代码零改动"""

    def __init__(self, client, model: str, registry: ToolRegistry,
                 solve_system: str, max_nodes: int = 8):
        self.client = client
        self.model = model
        self.registry = registry
        self.solve_system = solve_system      # 复用你的三段式报告 prompt
        self.max_nodes = max_nodes
        self.last_trajectory: Trajectory | None = None

    def _chat(self, system: str, user: str) -> tuple[str, int]:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=32768,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        tokens = resp.usage.total_tokens if resp.usage else 0
        msg = resp.choices[0].message
        content = msg.content or getattr(msg, "reasoning", "") or ""
        return content, tokens

    def run(self, input_text: str, max_rounds: int = 10) -> str:   # 签名兼容 main.py
        start = time.time()
        self.last_trajectory = Trajectory()
        total_tokens = 0

        # ---------- Planner ----------
        catalog = "\n".join(f"- {s['function']['name']}: "
                            f"{s['function'].get('description', '')[:80]}"
                            for s in self.registry.get_schemas())
        plan_text, tokens = self._chat(
            PLANNER_SYSTEM, f"{input_text}\n\n工具目录:\n{catalog}")
        total_tokens += tokens

        plan: Plan | None = None
        for _ in range(2):                                  # 解析/验证失败带错重试
            try:
                body = plan_text[plan_text.index("{"): plan_text.rindex("}") + 1]
                plan = Plan.model_validate_json(body)
            except (ValueError, ValidationError) as e:
                err = f"JSON 不合格: {e}"
            else:
                err = _validate(plan, self.registry, self.max_nodes)
                if err is None:
                    break
            plan_text, tokens = self._chat(
                PLANNER_SYSTEM + f"\n\n上次输出不合格:{err}。请重新只输出 JSON。",
                f"{input_text}\n\n工具目录:\n{catalog}")
            total_tokens += tokens
        if plan is None:
            self.last_trajectory.final_report = "⚠️ Planner 两次未能产出合格计划"
            self.last_trajectory.total_rounds = 1
            return self.last_trajectory.final_report
        print(f"[Planner] {len(plan.nodes)} 个节点: "
              + " → ".join(f"{n.id}:{n.tool}" for n in plan.nodes))

        # ---------- Worker(0 次模型调用,B2 截断自动生效)----------
        evidence: dict[str, str] = {}
        for node in plan.nodes:
            args = _substitute(node.args, evidence)
            output = str(self.registry.execute(node.tool, args))
            evidence[node.id] = output
            self.last_trajectory.tool_calls.append(ToolCallRecord(
                round=int(node.id[1:]) if node.id[1:].isdigit() else 0,
                name=node.tool, args=args,
                output_snippet=output[:150].replace("\n", " ")))
            print(f"[Worker {node.id}] {node.tool} → {len(output)} 字符")

        # ---------- Solver ----------
        evidence_text = "\n\n".join(
            f"[{nid}] {ev if len(ev) <= 2000 else ev[:2000] + '...[已截断]'}"
            for nid, ev in evidence.items())
        plan_text_pretty = json.dumps(
            [n.model_dump() for n in plan.nodes], ensure_ascii=False, indent=1)
        report, tokens = self._chat(
            self.solve_system,
            f"{input_text}\n\n排查计划:\n{plan_text_pretty}\n\n证据:\n{evidence_text}")
        total_tokens += tokens

        self.last_trajectory.total_tokens = total_tokens
        self.last_trajectory.total_rounds = 2               # Planner + Solver
        self.last_trajectory.duration = time.time() - start
        self.last_trajectory.is_timeout = False
        self.last_trajectory.final_report = report
        return report