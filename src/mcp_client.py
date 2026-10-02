"""切片7 Part2 · MCP 最小客户端:连自己的服务器,验证工具发现与调用。
用法: .venv/Scripts/python.exe src/mcp_client.py
"""
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = r"D:\AI learning\issue-pr-agent"


async def main() -> None:
    params = StdioServerParameters(
        command=rf"{PROJECT}\.venv\Scripts\python.exe",
        args=[rf"{PROJECT}\src\mcp_server.py"],
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
