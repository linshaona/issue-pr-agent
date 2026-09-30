import os
import inspect
from pydantic import create_model
from src.rag import CodeChunker, CodebaseRetriever
import shutil
import subprocess


def generate_schema(func,description : str = None)->dict:
    #  内省：获取函数的签名信息（参数名、类型标注、默认值）
    sig = inspect.signature(func)
    fields = {}

    for name,param in sig.parameters.items():
        # 如果没有写类型注解，默认当 str 处理
        annotation = param.annotation if param.annotation != inspect.Parameter.empty else str
        # 如果没有默认值，使用 ...（Pydantic 表示必填）
        default = param.default if param.default != inspect.Parameter.empty else ...
        fields[name] = (annotation, default)

    # 动态构建 Pydantic 模型
    model = create_model(f"{func.__name__}_args", **fields)

    # 构造 OpenAI 工具调用协议格式
    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": description or func.__doc__ or "",
            "parameters": model.model_json_schema()
        }
    }


class ToolRegistry:
    #策略器注册配合generate_schema自动生成json schema工具调用格式
    def __init__(self):
        self._tools = {}
        self._schemas = []


    def register(self,func = None,*,description : str = None):

        def decorator(f):
            schema = generate_schema(f,description)
            self._tools[f.__name__] = f
            self._schemas.append(schema)
            return f

        if func is None:
            return decorator
        return decorator(func)

    def execute(self,name:str,args:dict):
        """执行指定工具并返回字符串结果（做好异常保护）"""
        if name not in self._tools:
            return f"Error:Tool '{name} not found'"
        try:
            func = self._tools[name]
            exec_result = func(**args)
            if isinstance(exec_result, list):
                return "\n".join(exec_result)
            return str(exec_result)

        except Exception as e:
            return f"Error executing '{name}':{str(e)}"

    def get_schemas(self) -> list[dict]:
        """获取所有已注册工具的 OpenAI 格式定义"""
        return self._schemas


registry = ToolRegistry()

# 定义忽略集合
IGNORE_DIRS = {".git", ".venv", "__pycache__", ".idea", ".vscode", "workspace"}

@registry.register
def list_files(directory: str = ".") -> list[str]:
    """
     递归列出指定目录下的代码和文本文件，忽略系统/虚拟环境目录。
     """
    if not os.path.exists(directory):
        return [f"错误: 目录 '{directory}' 不存在。"]

    file_list = []
    for root, dirs, files in os.walk(directory):
        # 过滤掉不需要遍历的深层目录（原地修改 dirs 阻止 os.walk 深入）
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

        for file in files:
            if file.startswith("."):
                continue  # 忽略类似 .DS_Store 的隐藏文件

            # 获取相对于当前工作目录的相对路径，方便 LLM 识别
            rel_path = os.path.relpath(os.path.join(root, file), start=directory)
            file_list.append(rel_path.replace("\\", "/"))  # 统一用正斜杠

    return file_list

READ_CAP = 8000   # 单次返回字符上限 ≈ 2-3K token
READ_TAIL = 500   # 末尾保留——错误处理、main 块常在文件尾
@registry.register
def read_file(file_path: str, offset: int = 0) -> str:

    """
      安全读取文件文本内容。大文件自动截断:返回开头+末尾,中间以标记省略。
      需要继续读取时,传入上次返回标记中的 offset 值。

      param:file_path
      return:str
    """


    try:

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            # 越界防御:offset 超过文件长度时 content[offset:] 会得到空串,模型无从纠错
            if offset >= len(content):
                return (f"错误: offset={offset} 超出文件末尾(文件共 {len(content)} 字符)。"
                        f"请使用更小的 offset,或不传 offset 从头读取。")
            if offset:
                content = content[offset:]
            if len(content) <= READ_CAP:
                return content
            head_len = READ_CAP - READ_TAIL
            head, tail = content[:head_len], content[-READ_TAIL:]
            return (f"{head}\n[... 已省略 {len(content) - head_len - READ_TAIL} 字符,"
                    f"传 offset={offset + head_len} 继续读取 ...]\n{tail}")

    except FileNotFoundError:
        return f"错误: 未找到文件 '{file_path}'，请使用 list_files 确认文件相对路径是否正确。"
    except IsADirectoryError:
        return f"错误: '{file_path}' 是一个目录，不能用 read_file 读取，请使用 list_files 查看。"
    except Exception as e:
        return f"错误: 读取文件 '{file_path}' 失败: {str(e)}"




# @registry.register
# def search_code(keyword: str, directory: str = ".") -> str:
#     """在指定目录的代码和文本文件中搜索关键词，返回匹配的文件路径、行号和代码行内容。
#
#     参数:
#         keyword: 要搜索的代码、函数名、变量名或文本关键词。
#         directory: 搜索的起始目录相对路径，默认为当前目录 '.'。
#     """
#     if not os.path.exists(directory):
#         return f"错误: 目录 '{directory}' 不存在。"
#
#     matches = []
#     max_matches = 50  # 限制最大返回条数，防止上下文超限
#
#     for root, dirs, files in os.walk(directory):
#         # 过滤掉忽略目录和隐藏目录
#         dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
#
#         for file in files:
#             if file.startswith("."):
#                 continue
#
#             full_path = os.path.join(root, file)
#             rel_path = os.path.relpath(full_path, start=directory).replace("\\", "/")
#
#             try:
#                 with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
#                     for line_num, line in enumerate(f, 1):
#                         if keyword in line:
#                             # 去除尾部换行符，单行过长也可以适当截断
#                             content = line.strip()
#                             matches.append(f"{rel_path}:{line_num}: {content}")
#
#                             if len(matches) >= max_matches:
#                                 matches.append(f"... (匹配结果过多，仅显示前 {max_matches} 条)")
#                                 return "\n".join(matches)
#             except Exception:
#                 # 遇到无法读取的特殊文件直接跳过
#                 continue
#
#     if not matches:
#         return f"未在目录 '{directory}' 中找到包含 '{keyword}' 的内容。"
#
#     return "\n".join(matches)
#
#

# 全局单例缓存
_retriever: CodebaseRetriever | None = None

def _get_retriever() -> CodebaseRetriever:
    """惰性获取全局向量检索器，首次调用时自动构建索引"""
    global _retriever
    if _retriever is None:
        chunker = CodeChunker(repo_path=".")
        chunks = chunker.chunk_repo()
        _retriever = CodebaseRetriever()
        _retriever.build_index(chunks)
    return _retriever


CHUNK_CHAR_CAP = 1200      # 单块展示上限
SEMANTIC_TOTAL_CAP = 5000  # 单次检索总上限(top_k=5 时单块帽不够,还得有总闸)
@registry.register
def search_code_semantic(query: str, top_k: int = 3) -> str:
    """通过自然语言语义检索代码库中最相关的代码片段（向量相似度匹配）。
    当你不知道确切的函数名、类名或关键字，或者面对较为抽象的功能描述时，使用此工具进行语义匹配。

    :param query: 自然语言描述的搜索意图，例如 '解析命令行传入的仓库链接' 或 '安全读取文件内容'
    :param top_k: 返回的最相关代码块数量，默认 3
    :return: 包含文件路径、行号和代码内容的检索结果
    """
    try:
        retriever = _get_retriever()
        matches = retriever.query(query, top_k=top_k)
        if not matches:
            return "未找到相关的代码片段。"

        # B4: lost-in-the-middle 重排——最强注意力给首尾,最差的沉中间
        pairs = sorted(matches, key=lambda m: m[1], reverse=True)  # 按分数降序
        if len(pairs) > 2:
            first = pairs[::2]  # 第1、3、5名 → 前半段(越靠前越强)
            second = pairs[1::2];
            second.reverse()  # 第2、4名反转 → 尾部(第2名压轴)
            pairs = first + second  # 效果: [s1, s3, s5, ..., s4, s2]
            
        outputs, total = [], 0
        for chunk, score in pairs:
            text = chunk.format_for_display()
            if len(text) > CHUNK_CHAR_CAP:
                text = text[:CHUNK_CHAR_CAP] + "\n[... 块已截断;需完整代码用 grep 定位行号后 read_file ...]"
            if total + len(text) > SEMANTIC_TOTAL_CAP:
                outputs.append("[... 已达单次检索输出上限,请细化 query 提高精度 ...]")
                break
            outputs.append(f"[相似度: {score:.3f}]\n{text}")
            total += len(text)

        return "\n\n".join(outputs)
    except Exception as e:
        return f"语义检索执行失败: {str(e)}"


#排除规则
IGNORE_GLOBS = ["!.git/**", "!.venv/**", "!__pycache__/**",
                "!.idea/**", "!.vscode/**", "!workspace/**"]

@registry.register
def grep(pattern: str, path: str = ".", glob: str = "",
         ignore_case: bool = False, max_results: int = 50) -> str:
    """用正则表达式在代码库中精确搜索(ripgrep)。
    适合:已知确切的标识符、函数名、错误字符串、配置键,或可用正则表达的模式。
    不适合:只有模糊的自然语言意图、不知道关键词——这时用 search_code_semantic。

    :param pattern: 正则表达式,如 'def \\\\w+_handler'、'num_ctx'、'TODO|FIXME'
    :param path: 搜索起始目录或单个文件,默认当前目录
    :param glob: 按文件名过滤,如 '*.py',可空
    :param ignore_case: 忽略大小写,默认 False
    :param max_results: 最多返回行数,防止上下文超限
    :return: 每行格式 '文件路径:行号: 内容'
    """
    rg = shutil.which("rg")
    if rg is None:
        return "错误: 未找到 ripgrep,请先安装"

    cmd = [rg, "--no-heading", "--line-number", "--color", "never",
           "--max-columns", "200"]                # 单行过长自动省略:B2 在工具层的触手
    if ignore_case:
        cmd.append("-i")
    if glob:
        cmd += ["--glob", glob]
    for g in IGNORE_GLOBS:                        # ← 上一版框架漏掉的:不排除 .venv
        cmd += ["--glob", g]                      #    rg 会一头扎进几千个第三方文件
    cmd += [pattern, path]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=30)
    except subprocess.TimeoutExpired:
        return "错误: 搜索超时,请缩小 path 或加 glob 过滤"

    if proc.returncode == 1:                      # rg 退出码:0=有匹配 1=无匹配 2=出错
        return f"未找到匹配 '{pattern}'。提示:检查正则与大小写,或改用 search_code_semantic"
    if proc.returncode != 0:
        return f"错误: ripgrep 失败: {proc.stderr[:200]}"

    lines = proc.stdout.splitlines()
    if len(lines) > max_results:
        lines = lines[:max_results] + [f"... (共 {len(lines)} 条,仅显示前 {max_results} 条)"]
    return "\n".join(lines)
# TOOLS_SCHEMA = [
#     {
#         "type": "function",
#         "function": {
#             "name": "list_files",
#             "description": "列出指定目录下的文件列表。用于了解代码库的文件结构，定位可能包含 Bug 或相关逻辑的文件。",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "directory": {
#                         "type": "string",
#                         "description": "要查看的目录相对路径，默认为当前项目根目录 '.'"
#                     }
#                 },
#                 "required": []  # directory 允许为空或不传
#             }
#         }
#     },
#     {
#         "type": "function",
#         "function": {
#             "name": "read_file",
#             "description": "读取指定代码文件或文本文件的完整内容。用于查看源代码、排查具体错误逻辑。",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "file_path": {
#                         "type": "string",
#                         "description": "要读取的文件相对路径，例如 'src/main.py' 或 'README.md'"
#                     }
#                 },
#                 "required": ["file_path"]  # file_path 是必填项
#             }
#         }
#     }
# ]
#
#
# # 1. 建立工具名称到函数的映射表
# TOOL_MAP = {
#     "list_files": list_files,
#     "read_file": read_file,
# }
#
#
# # 2. 统一分发执行函数
# def execute_tool(name: str, args: dict) -> str:
#     """根据大模型给出的工具名和参数，动态找到对应函数并执行"""
#     tool_fn = TOOL_MAP.get(name)
#
#     # 兜底：如果模型幻觉编造了一个不存在的工具名
#     if not tool_fn:
#         return f"错误: 不存在名为 '{name}' 的工具。"
#
#     try:
#         # **args 是字典解包，相当于把 {"file_path": "a.py"} 变成 file_path="a.py" 传给函数
#         result = tool_fn(**args)
#
#         # 保证返回给 LLM 的一定是字符串（如果是列表转成换行或字符串）
#         if isinstance(result, list):
#             return "\n".join(result)
#         return str(result)
#     except Exception as e:
#         return f"执行工具 '{name}' 时出错: {e}"


# if __name__ == "__main__":
#     import json
#     print("=== 自动生成的工具 Schema ===")
#     print(json.dumps(registry.get_schemas(), indent=2, ensure_ascii=False))
#
#     print("\n=== 测试执行工具 ===")
#     result = registry.execute("list_files", {"directory": "."})
#     print("执行结果（截取前200字符）:", result[:200])
#
#     print("\n=== 测试 search_code 工具 ===")
#     search_res = registry.execute("search_code", {"keyword": "ToolRegistry"})
#     print(search_res)