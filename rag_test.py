from openai import OpenAI
from src.agent import Agent
from src.init_func import init_agent_args

def test_semantic_agent():
    base_url, api_key, model_name = init_agent_args()
    client = OpenAI(base_url=base_url, api_key=api_key)
    agent = Agent(client=client, model=model_name, max_tokens=8192)

    # 模糊需求：完全不提具体函数名（如 handle_url、re.search），纯自然语言
    vague_issue = (
        "【需求】用户在命令行传入的仓库地址有多种格式，经常报错。"
        "请帮我排查代码库中负责提取仓库地址和编号的核心逻辑在哪个文件，并分析其实现。"
    )

    print("🚀 启动 Agent 进行语义排查...\n")
    report = agent.run(vague_issue, max_rounds=50)
    print("\n" + "=" * 40)
    print("📋 Agent 最终报告：")
    print("=" * 40)
    print(report)

if __name__ == "__main__":
    test_semantic_agent()