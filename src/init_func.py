import os
from dotenv import load_dotenv

# 把 .env 锚定到项目根:load_dotenv() 默认沿"调用者脚本位置"向上找,
# 换个目录运行(如沙箱克隆里跑 main.py)就会静默丢配置
_PROJECT_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

def init_github_token()->str:
    load_dotenv(_PROJECT_ENV)

    github_token = os.getenv("GITHUB_TOKEN")

    return github_token or ""


def init_agent_args()->tuple[str,str,str]:
    load_dotenv(_PROJECT_ENV)

    # 2. 读取配置（带上默认值兜底）
    base_url = os.getenv("LLM_BASE_URL", "http://10.102.29.101:11434/v1")
    api_key = os.getenv("LLM_API_KEY", "ollama")  # 本地 ollama 随便填个非空字符串即可
    model_name = os.getenv("LLM_MODEL", "qwen3.5:4B")

    return base_url,api_key,model_name