"""切片7 Part2 · MCP 最小客户端:连自己的服务器,验证工具发现与调用。
用法: uv run python src/mcp_client.py
"""
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 项目根 = 本文件的上上级目录,换机器/换目录都不用改
PROJECT = str(Path(__file__).resolve().parents[1])
# 用当前解释器拉起服务器,避免写死 .venv 的绝对路径
PYTHON = sys.executable
if os.name == "nt" and not Path(PYTHON).exists():  # 兜底:pythonw 等场景
    PYTHON = str(Path(PROJECT) / ".venv" / "Scripts" / "python.exe")


async def main() -> None:
    params = StdioServerParameters(
        command=PYTHON,
        args=[str(Path(PROJECT) / "src" / "mcp_server.py")],
        cwd=PROJECT,   # 工具的相对路径操作以此为基准(与 Agent 运行语义一致)
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("===MCP 工具发现===")
            for t in tools.tools:
                print(f"- {t.name}: {t.description[:50]}")

            print("===远程调用 grep===")
            result = await session.call_tool(
                "grep", {"pattern": "num_ctx", "glob": "*.py"})
            print(result.content[0].text[:200])

            print("===远程调用 read_file===")
            result = await session.call_tool(
                "read_file", {"file_path": "demo.py"})
            print(result.content[0].text[:120])


if __name__ == "__main__":
    asyncio.run(main())
