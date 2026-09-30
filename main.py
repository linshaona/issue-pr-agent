
import sys

from src.handle_github_url import handle_url
from src.init_func import init_github_token,init_agent_args
from src.agent import Agent
from openai import OpenAI

def main():
    # 检查参数个数
    # len(sys.argv) 为 1 时，说明用户只输入了 "python main.py"，没给任何参数
    if len(sys.argv) < 2:
        print("❌ 错误: 缺少必要参数！请提供 GitHub Issue 标识。")
        print("\n使用方法:")
        print("  uv run python main.py <owner/repo#编号 | 完整URL>")
        print("\n示例:")
        print("  uv run python main.py python/cpython#130000")
        print("  uv run python main.py https://github.com/psf/requests/issues/6000")

        # 退出程序，1 表示异常退出
        sys.exit(1)

    # 如果用户输入了 -h 或 --help，友好打印帮助信息
    if sys.argv[1] in ("-h", "--help"):
        print("【GitHub Issue 查看工具】")
        print("用法: uv run python main.py <target>")
        sys.exit(0)  # 0 表示正常退出

    # 检查通过，安全获取参数
    target = sys.argv[1]
    print(f"--> 正在解析目标: {target}")

    use_rewoo = "--rewoo" in sys.argv

    github_token = init_github_token()
    result_issue = handle_url(target,github_token)

    base_url,api_key,model_name = init_agent_args()

    # 在创建 OpenAI 客户端时指定 URL 和 Key
    client = OpenAI(
        base_url=base_url,
        api_key=api_key


    )

    # 在创建 Agent 实例时指定客户端和模型名称
    if use_rewoo:
        from src.rewoo import ReWOOAgent
        from src.tools import registry as default_registry
        agent = ReWOOAgent(
            client=client, model=model_name,
            registry=default_registry,
            solve_system=Agent.DEFAULT_SYSTEM_PROMPT,  # Agent 顶部已导入,直接用
        )
    else:
        agent = Agent(client=client, model=model_name, max_tokens=32768)

    print(agent.run(result_issue, 20))
if __name__ == "__main__":
    main()