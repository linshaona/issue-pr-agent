"""轨迹记录 + B1 上下文账单:发送前本地估算各成分占比,响应后用 API 真值校准打印。

ContextAccountant 独立于 Agent:数据全部通过构造参数显式传入(messages 持引用)。
切片 5 后续的历史压缩(B3)与重排(B4)策略计划住进这个模块。
"""
import json
from dataclasses import dataclass, field
from typing import Any

#轨迹记录
@dataclass
class ToolCallRecord:
    round: int           # 发生在第几轮
    name: str            # 调用的工具名 (如 'search_code_semantic')
    args: dict           # 传入的参数
    output_snippet: str  # 工具返回结果的简略摘要 (前 150 字符)

@dataclass
class Trajectory:
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_tokens: int = 0      # 累计消耗 Token 数
    total_rounds: int = 0      # 实际经历的轮数
    duration: float = 0.0      # 实际耗时（秒）
    is_timeout: bool = False   # 是否触发防死循环熔断
    final_report: str = ""     # 最终输出的报告


def estimate_tokens(text: str) -> int:
    """粗略估算:中文 ≈ 1 token/字,其余 ≈ 1 token/4 字符。只看数量级,不求精确。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def _msg_content(msg: Any) -> str:
    """messages 里既有 dict 也有 SDK 消息对象,统一取 content"""
    if isinstance(msg, dict):
        return msg.get("content") or ""
    return getattr(msg, "content", None) or ""


def _msg_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


COMPONENTS = ("system_prompt", "tools_schema", "issue", "tool_outputs", "history")


COMPRESS_THRESHOLD = 4000  # tool_outputs 超过此估算 token 数才触发
SNIPPET_CHARS = 150        # 摘要长度

class ContextAccountant:
    """每轮上下文账单:snapshot() 在发送前调用,record() 在响应后调用。"""

    def __init__(self, messages: list, tools_schema: list[dict],
                 max_tokens: int, context_windows: int):
        self.messages = messages              # 持引用不拷贝——Agent 每轮追加,这里实时可见
        self.tools_schema = tools_schema
        self.input_budget = context_windows - max_tokens
        self._last_bill: dict[str, int] | None = None
        self._prev_total: int = 0

    def snapshot(self) -> dict[str, int]:
        """发送前对 messages 逐条归堆估算,得到各成分 token 占比"""
        bill = {"system_prompt": 0, "issue": 0, "tool_outputs": 0, "history": 0}
        for i, msg in enumerate(self.messages):
            est = estimate_tokens(_msg_content(msg))
            role = _msg_role(msg)
            if role == "system":
                bill["system_prompt"] += est
            elif role == "user" and i == 1:
                bill["issue"] += est
            elif role == "tool":
                bill["tool_outputs"] += est
            else:
                bill["history"] += est
        # tools_schema 不在 messages 里,单独算(每轮固定成本)
        bill["tools_schema"] = estimate_tokens(
            json.dumps(self.tools_schema, ensure_ascii=False)
        )
        self._last_bill = bill
        return bill

    def record(self, true_total: int, round_idx: int) -> None:
        """响应后:明细 = 本地占比 × 真实总数;总行与新增量来自 API 的 prompt_tokens"""
        bill = self._last_bill or {}
        scale = true_total / (sum(bill.values()) or 1)
        delta = true_total - self._prev_total
        self._prev_total = true_total

        print(f"[Round {round_idx}] 输入 {true_total:,} / {self.input_budget:,} "
              f"({true_total / self.input_budget:.0%}) | 本轮新增 {delta:+,}")
        for comp in COMPONENTS:
            print(f"  {comp:<15} ~{int(bill.get(comp, 0) * scale):>7,}")
        if true_total > self.input_budget * 0.9:
            print("  ⚠️ 接近预算上限,较早的工具输出将被 Ollama 静默挤出窗口")

    def compress(self) -> int:
        """B3:发送前把旧工具输出降级为摘要。返回节省的估算 token 数。"""
        tool_idxs = [i for i, m in enumerate(self.messages) if _msg_role(m) == "tool"]
        if not tool_idxs:
            return 0
        total = sum(estimate_tokens(_msg_content(self.messages[i])) for i in tool_idxs)
        if total <= COMPRESS_THRESHOLD:
            return 0

        # 尾部连续的 tool 消息块 = 最近一轮(并行调用会是多个),保持全文
        last_block: set[int] = set()
        for i in range(len(self.messages) - 1, -1, -1):
            if _msg_role(self.messages[i]) == "tool":
                last_block.add(i)
            elif last_block:
                break

        saved = 0
        for i in tool_idxs:
            if i in last_block:
                continue
            msg = self.messages[i]
            content = _msg_content(msg)
            if len(content) <= SNIPPET_CHARS * 4:  # 本来就小,不值得动
                continue
            summary = content[:SNIPPET_CHARS].replace("\n", " ")
            new = (f"[已压缩:原约 {estimate_tokens(content)} token。摘要: {summary}... "
                   f"需要完整内容请重新调用该工具]")
            saved += estimate_tokens(content) - estimate_tokens(new)
            msg["content"] = new  # dict 直改,配对不受影响

        if saved:
            print(f"[B3 压缩] 旧工具输出降级,节省 ~{saved:,} token(最近一轮保持全文)")
        return saved
