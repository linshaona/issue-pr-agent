import os
from dotenv import load_dotenv

def init_github_token()->str:
    # 1. 加载当前目录下的 .env 文件
    load_dotenv()

    # 2. 读取环境变量（如果 .env 里没有，返回 None）
    github_token = os.getenv("GITHUB_TOKEN")

    return github_token or ""


def init_agent_args()->tuple[str,str,str]:
    load_dotenv()

    # 2. 读取配置（带上默认值兜底）
    base_url = os.getenv("LLM_BASE_URL", "http://10.102.29.101:11434/v1")
    api_key = os.getenv("LLM_API_KEY", "ollama")  # 本地 ollama 随便填个非空字符串即可
    model_name = os.getenv("LLM_MODEL", "qwen3.5:4B")

    return base_url,api_key,model_name