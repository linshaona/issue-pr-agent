import json
from typing import Any
from openai import OpenAI

from src.context_manager import Trajectory, ToolCallRecord, ContextAccountant
from src.tools import ToolRegistry, registry as default_registry

import time











class Agent:
    DEFAULT_SYSTEM_PROMPT = """你是一名资深代码排查专家。根据用户提供的 GitHub Issue，你可以自主使用工具（list_files、read_file、search_code 关键字精确搜索、search_code_semantic 自然语言语义检索）探索代码库。
    排查完毕后，请务必按照以下 Markdown 格式给出最终结论：

    ## 1. 根因分析 (Root Cause Analysis)
    详细说明导致该 Bug 的根本原因及逻辑缺陷。

    ## 2. 涉及文件 (Affected Files)
    列出需要修改或相关的文件相对路径及关键代码行。

    ## 3. 修复方案 (Proposed Fix)
    给出具体的修改建议或代码 Diff。"""

    client: OpenAI
    model: str
    system_prompt: str
    registry: ToolRegistry
    tools_schema: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    max_tokens : int
    context_windows : int
    last_trajectory: Trajectory | None = None

    def __init__(
            self,
            client: OpenAI,
            model: str,
            system_prompt: str | None = None,
            registry: ToolRegistry | None = None,
            tools_schema: list[dict[str, Any]] | None = None,
            max_tokens:int = 32768,
            context_windows = 65536,
            last_trajectory : Trajectory | None = None,
    ):

        self.client = client
        self.model = model
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.registry = registry or default_registry
        self.tools_schema = self.registry.get_schemas()
        #self.tools_schema = tools_schema or self.registry.get_schemas()
        self.messages: list[dict[str, Any]] = []
        self.max_tokens: int = max_tokens
        self.context_windows: int = context_windows

    def _init_conversation(self, input_text: str) -> None:
        """重置并初始化本次会话的上下文消息"""
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content":f"{input_text}"}
        ]

    def run(self,input_text:str,max_rounds:int = 10) ->str:
        """
            启动 Agent 核心决策循环

            :param input_text: 格式化后的 Issue 描述文本
            :param max_rounds: 最大思考与调用轮数（防死循环安全阈值）
            :return: Agent 给出的最终结论文本
        """
        self._init_conversation(input_text)

        start_time = time.time()
        self.last_trajectory = Trajectory()

        accountant = ContextAccountant(
            self.messages, self.tools_schema, self.max_tokens, self.context_windows
        )

        for round_idx in range(1,max_rounds+1):

            accountant.snapshot()  # 发送前:本地估算各成分占比

            response = self.client.chat.completions.create(
                model = self.model,
                messages =  self.messages,
                tools = self.tools_schema,
                max_tokens = self.max_tokens,
                extra_body={
                    "options": {
                        "num_ctx": self.context_windows
                    }
                }
            )

            if response.usage:
                self.last_trajectory.total_tokens += response.usage.total_tokens
                if response.usage.prompt_tokens:
                    accountant.record(response.usage.prompt_tokens, round_idx)
                    print(f"finish_reason={response.choices[0].finish_reason}, "
                          f"completion_tokens={response.usage.completion_tokens}")

            response_message = response.choices[0].message
            self.messages.append(response_message)

            if not response_message.tool_calls:
                final_content = response_message.content
                print("模型无工具调用\n")
                if not final_content and getattr(response_message, "reasoning", None):
                    final_content = response_message.reasoning

                self.last_trajectory.final_report = final_content or ""
                self.last_trajectory.total_rounds = round_idx
                self.last_trajectory.duration = time.time() - start_time
                self.last_trajectory.is_timeout = False

                return final_content or "⚠️ 模型返回内容为空，请检查上下文长度或重试。"

            if response_message.tool_calls:
                for tool_call in response_message.tool_calls:
                    func_name = tool_call.function.name

                    # 安全解析工具参数（防止偶发 JSON 格式截断）
                    try:
                        func_args =json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        func_args = {}

                    print(f"调用工具{func_name},参数{func_args}\n")

                    tool_output = self.registry.execute(func_name, func_args)

                    self.last_trajectory.tool_calls.append(
                        ToolCallRecord(
                            round=round_idx,
                            name=func_name,
                            args=func_args,
                            output_snippet=str(tool_output)[:150].replace("\n", " ")
                        )
                    )

                    self.messages.append(
                        {
                            "role":"tool",
                            "tool_call_id":tool_call.id,
                            "content": str(tool_output)
                        }

                    )

        timeout_msg = f"⚠️ Agent 达到最大思考轮数 ({max_rounds})，已自动终止以防止死循环。"
        print(f"\n{timeout_msg}")
        self.last_trajectory.final_report = timeout_msg
        self.last_trajectory.total_rounds = max_rounds
        self.last_trajectory.duration = time.time() - start_time
        self.last_trajectory.is_timeout = True
        return timeout_msg