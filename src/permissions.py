"""L10 权限模式:工具风险分级 × 会话模式 × 预算熔断(P15/L10 Build)。

把散在四处的控制收编到一个有状态策略对象:
  验证门(request_pr 内)   —— 交付口径的"完成"
  工具红线(commit 拒 main) —— 具体破坏动作
  injection_guard.validate —— 内容级注射扫描(本对象复用为其中一层)
  本对象                    —— 动作类别 × 会话模式 × 预算

六级阶梯取四档(Claude Code 命名):
  plan        只放 READ——分析任务连分支都不建
  default     全类别放行;EXTERNAL 由验证门/工具红线独立把守;预算用宽松帽
  unattended  同 default 的类别,但预算收紧——无人值守=没人看着,帽必须更矮
  bypass      全放行、无预算——官方口径:只配"你舍得扔的一次性容器"

预算熔断:token 帽超限后拒 EXEC/EXTERNAL(贵的与世界接触的动作),
读写在册但模型可以收尾;单工具次数帽掐反复重试环(评测实录:84K token
25 分钟的大循环,第 6 次 run_tests 起就该死)。
scope_creep(越权文件)继续留白:需要 issue 范围的语义解析,模型活。
"""
import os

from src.injection_guard import validate as _injection_validate
from src.tripwire import CanaryRegistry, CircuitBreaker, kill_switch_active

# ---------- 工具风险分级(新增工具必须登记,否则按默认拒) ----------
TOOL_RISK: dict[str, str] = {
    "list_files": "READ",
    "read_file": "READ",
    "grep": "READ",
    "search_code_semantic": "READ",
    "edit_file": "WRITE",
    "create_branch": "WRITE",
    "commit_changes": "WRITE",
    "run_tests": "EXEC",          # 能起任意进程,风险等同 WRITE+
    "push_branch": "EXTERNAL",    # 出网/公网
    "request_pr": "EXTERNAL",
}

# ---------- 预算帽:normal=交互,default 会话;tight=无人值守 ----------
_BUDGETS_NORMAL = {"max_tokens_per_run": 150_000,
                   "max_calls": {"run_tests": 8, "edit_file": 12,
                                 "push_branch": 3, "request_pr": 2}}
_BUDGETS_TIGHT = {"max_tokens_per_run": 100_000,
                  "max_calls": {"run_tests": 5, "edit_file": 10,
                                "push_branch": 2, "request_pr": 1}}

MODES: dict[str, dict] = {
    "plan":       {"classes": {"READ"}, "budgets": None},
    "default":    {"classes": {"READ", "WRITE", "EXEC", "EXTERNAL"},
                   "budgets": _BUDGETS_NORMAL},
    "unattended": {"classes": {"READ", "WRITE", "EXEC", "EXTERNAL"},
                   "budgets": _BUDGETS_TIGHT},
    "bypass":     {"classes": set(TOOL_RISK.values()), "budgets": None},
}


class PermissionPolicy:
    """有状态策略:每次工具调用 check() 一次,拒绝消息用"错误:"前缀
    (与工具层约定一致,标签器 tool_misuse 因此覆盖全部被拒调用)。"""

    def __init__(self, mode: str = "default",
                 max_tokens_per_run: int | None = None,
                 max_calls: dict[str, int] | None = None):
        if mode not in MODES:
            raise ValueError(f"未知权限模式 '{mode}',可选: {list(MODES)}")
        spec = MODES[mode]
        self.mode = mode
        self.classes = spec["classes"]
        budgets = spec["budgets"] or {}
        # 显式参数 > 模式默认;显式传 None 表示关掉帽(仅 bypass 场景自担)
        self.max_tokens = (max_tokens_per_run if max_tokens_per_run is not None
                           else budgets.get("max_tokens_per_run"))
        self.max_calls = dict(budgets.get("max_calls", {}))
        if max_calls:
            self.max_calls.update(max_calls)
        self._call_counts: dict[str, int] = {}
        # L14 三探测器:kill/canary 配置走环境变量(runner 布置),熔断器有状态
        self.breaker = CircuitBreaker()
        self.canary = CanaryRegistry(
            paths=[p for p in os.getenv("AGENT_CANARY_PATHS", "").split(os.pathsep) if p],
            sentinels=[s for s in os.getenv("AGENT_CANARY_SENTINELS", "").split(os.pathsep) if s],
        )

    # ---- 供标签器/审计读取 ----
    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)

    def check(self, tool_name: str, args: dict,
              tokens_so_far: int = 0) -> dict:
        """PVE 的 V 入口:kill → canary → 熔断 → 模式类别 → 注射 → 次数帽 → token。
        返回 {"allowed": bool, "reason": str, "findings": [...]}。
        放行时登记本次调用;拒绝不登记(重试同一动作会持续被拒到帽位)。"""

        # 0a) kill switch:每个后果性动作都查,live 生效;触发时零外部行为
        if kill_switch_active():
            print("🚨 [Kill Switch] 已触发,全部动作拒绝(重开=人工删除标志文件)")
            return _refuse("KILL SWITCH 已触发:所有动作禁止(重开需人工清除标志文件)")

        # 0b) canary 路径:execute 前 args 里出现即报警
        canary_hit = self.canary.check_action(tool_name, args)
        if canary_hit:
            print(f"🚨 [Canary] {tool_name}: {canary_hit}——注入/外传企图已坐实")
            return _refuse(f"canary 告警: {canary_hit}")

        # 0c) 熔断器:重试环/系统性故障按 tool 粒度停
        breaker_block = self.breaker.check(tool_name, args)
        if breaker_block:
            return _refuse(f"{tool_name}: {breaker_block}")

        # 1) 会话模式 × 动作类别:plan 模式下连 create_branch 都进不来
        risk = TOOL_RISK.get(tool_name)
        if risk is None:
            return _refuse(f"工具 '{tool_name}' 未登记风险级别,默认拒(新工具请登记 TOOL_RISK)")
        if risk not in self.classes:
            return _refuse(f"模式 '{self.mode}' 不放行 {risk} 类工具 '{tool_name}'"
                           f"(允许类别: {sorted(self.classes)})")

        # 2) 注射扫描(层2 内容级,复用 L27)
        verdict = _injection_validate(tool_name, args)
        if not verdict["allowed"]:
            verdict["reason"] = f"注射扫描: {verdict['reason']}"
            return verdict

        # 3) 单工具次数帽:掐反复重试环
        cap = self.max_calls.get(tool_name)
        if cap is not None and self._call_counts.get(tool_name, 0) >= cap:
            return _refuse(f"'{tool_name}' 已调用 {self._call_counts[tool_name]} 次,"
                           f"达到模式 '{self.mode}' 的次数帽 {cap}——疑似重试环")

        # 4) token 熔断:超帽后拒 EXEC/EXTERNAL(贵的、世界接触类动作)
        if (self.max_tokens is not None and tokens_so_far > self.max_tokens
                and risk in ("EXEC", "EXTERNAL")):
            return _refuse(f"会话已消耗 {tokens_so_far:,} token,超过模式 "
                           f"'{self.mode}' 的预算 {self.max_tokens:,}——熔断 {risk} 类动作")

        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
        findings = list(verdict.get("findings", []))
        if risk == "EXTERNAL":
            findings.append("EXTERNAL 类动作(验证门/工具红线独立把守)")
        return {"allowed": True, "reason": "", "findings": findings}

    def observe(self, tool_name: str, args: dict, output: str) -> list[str]:
        """execute 后回喂真实结果(两个 agent 在 execute 之后各调一行)。
        喂给熔断器数模式;顺带扫输出里的 canary 哨兵——数据被读出时报警。
        返回告警列表(调用方可忽略)。"""
        alarms: list[str] = []
        self.breaker.observe(tool_name, args, output)
        canary_leak = self.canary.check_output(tool_name, output)
        if canary_leak:
            alarms.append(f"{tool_name}: {canary_leak}")
            print(f"🚨 [Canary] {alarms[-1]}")
        return alarms


def _refuse(reason: str) -> dict:
    return {"allowed": False, "reason": reason, "findings": [reason]}
