"""L27 注入防御:确定性 PVE Validator(P14/L27 Build)。

每次工具调用在 registry.execute 之前过一遍;拒绝消息以"错误:"前缀回给模型
(与工具层错误约定一致,标签器的 tool_misuse 签名因此覆盖被拒调用)。

设计取舍:课上的标准 PVE 用"便宜小模型"当验证器;4B 本地栈上再叠模型
没有意义,本层的"便宜验证器"就是纯代码——与验证门同源:检测必须确定性。
生产升级路径:独立小模型判"该动作是否符合用户意图"(Exercise 5 量法:
合法流量上误拒率应≈0,当前签名式实现以此为准绳调精度)。

已知代价:签名式扫描会漏报变种、可能误报极端措辞;高危面(push/PR)另有
验证门与工具红线独立把守,本层不是唯一防线——纵深防御(L27 教条#5)。
"""
import os
import re

# ---------- 层1:run_tests 命令白名单(教条#2 导航白名单,打最锋利面) ----------
# 合法测试命令(python/pytest/pip)用不到外传/链式/改向;命中即拒
ALLOWED_CMD_TOKENS = ("python", "python3", "pytest", "pip", "pip3")
BLOCKED_CMD_PATTERNS = (
    "curl", "wget", "iwr", "invoke-webrequest", "certutil",       # 外传/下载
    "|", "&&", "||", ";", "`", "$(", ">", "<",                    # 链式/改向
)

# ---------- 层2:注射模式扫描(教条#1 检索内容一律不可信) ----------
# 高精度签名:指令形语句(动词+宾语/改写任务),不用裸关键词——
# 项目文档里正常讨论"系统提示词"不应被误伤
INJECTION_PATTERNS = [
    re.compile(r"忽略.{0,8}(之前|上面|以上|先前|前面|系统|规则|指令)"),
    # 空格分隔的指令短语,不匹配 ignore_previous 这类合法标识符
    re.compile(r"(ignore|disregard|forget|override)\s+"
               r"((all|the|my)\s+)?(previous|prior|above|earlier)\s+"
               r"(instructions?|prompts?|rules?|commands?)", re.I),
    re.compile(r"(ignore|disregard|override)\s+((the|your)\s+)?"
               r"system\s+(prompt|instructions?|message)", re.I),
    re.compile(r"(你的新任务|你的真正任务|任务改为|you are now|"
               r"new (task|goal|instruction) is)", re.I),
    re.compile(r"(输出|打印|发送|贴出|泄露|reveal|print|send|exfiltrate)"
               r".{0,24}(\.env|api[ _-]?key|token|password|密钥|凭据|credentials)", re.I),
    re.compile(r"</?(instruction|directive|system[-_ ]?command)>", re.I),
]

# ---------- 层3:高危面备案(教条#3 每步评估的记账侧) ----------
# 真正的拦截在验证门(request_pr)与工具红线(commit 拒 main),这里只留审计痕
SENSITIVE_TOOLS = {"push_branch", "request_pr", "commit_changes"}


def _refuse(reason: str) -> dict:
    return {"allowed": False, "reason": reason, "findings": [reason]}


def _check_command(command: str) -> dict | None:
    """run_tests 专用:白名单前缀 + 禁用模式。返回 refuse 字典或 None。"""
    first = command.strip().split(" ", 1)[0].strip("\"'").lower() if command.strip() else ""
    if not any(first == t or first.startswith(t) for t in ALLOWED_CMD_TOKENS):
        return _refuse(f"run_tests 命令 '{first or '(空)'}' 不在白名单 {list(ALLOWED_CMD_TOKENS)}")
    lowered = command.lower()
    for pat in BLOCKED_CMD_PATTERNS:
        if pat in lowered:
            return _refuse(f"run_tests 命令含被禁模式 '{pat}'(外传/链式/改向)")
    return None


# 内容承载参数才做注射扫描;导航性键(file_path/path 等)豁免——
# 真实教训(2026-10-02 评测):planner 编造的路径恰好含"忽略系统"字样被误拒,
# 路径是标识符不是内容,注入扫描的对象是会写进世界的内容
NAVIGATION_KEYS = {"file_path", "path", "directory", "glob", "pattern"}


def _scan_strings(value, findings: list[str], path: str = "args",
                  key: str | None = None) -> None:
    """递归扫描 args 里内容承载的字符串值的注射签名。"""
    if isinstance(value, str):
        if key not in NAVIGATION_KEYS:
            for pat in INJECTION_PATTERNS:
                m = pat.search(value)
                if m:
                    findings.append(f"{path} 含注射签名 '{m.group(0)[:40]}'")
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_strings(v, findings, f"{path}.{k}", key=k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_strings(v, findings, f"{path}[{i}]", key=key)


def validate(tool_name: str, args: dict) -> dict:
    """PVE 的 V:每次工具调用执行前的确定性验证。
    返回 {"allowed": bool, "reason": str, "findings": [...]}。"""
    findings: list[str] = []

    if tool_name == "run_tests":
        command = str(args.get("command", ""))
        verdict = _check_command(command)
        if verdict:
            return verdict

    _scan_strings(args, findings)
    if findings:
        return _refuse("参数含指令形注射签名: " + "; ".join(findings[:3]))

    if tool_name in SENSITIVE_TOOLS:
        findings.append(f"{tool_name} 为高危工具(由验证门与工具红线独立把守)")

    return {"allowed": True, "reason": "", "findings": findings}
