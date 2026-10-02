"""切片7 Part2 · MCP 服务器(v2 SDK):把 issue-pr-agent 的工具注册表暴露为 MCP 协议。

设计:协议适配层,不是重写——
  遍历注册表,add_tool 把每个工具挂成 MCP tool;
  参数 schema 由 v2 从函数类型注解自动生成(与 registry 内省同源);
  tools/call 委托回原函数——错误处理/B2截断/grep 行为全部继承。
任何 MCP 客户端(Claude Desktop、其他 Agent)都能驱动这套工具箱。
"""
import os
import sys

# 作为独立脚本被 MCP 客户端拉起时,sys.path[0] 是 src/,需要补项目根才能 import src.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer

from src.tools import registry

mcp = MCPServer("issue-pr-agent-tools")

for schema in registry.get_schemas():
    fn = schema["function"]
    mcp.add_tool(
        registry._tools[fn["name"]],
        name=fn["name"],
        description=fn.get("description", ""),
    )

if __name__ == "__main__":
    mcp.run()   # stdio 传输
