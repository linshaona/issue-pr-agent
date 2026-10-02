"""L14 三探测器:Kill Switch / 熔断器 / Canary(P15/L14 Build)。

设计总纲:三个探测器都**不信任 agent 的自述**——
  kill switch 读 agent 凭据够不着的标志文件;
  熔断器数工具调用与输出的客观模式;
  canary 等的是"没有正当理由的触碰"。

已知边界(L14 "谁也抓不住"节,记入文档):
  组合攻击(每步全绿、组合有害)与"把探测器编进载荷"的攻击均不可检;
  缓解 = canary 放 cwd 之外(防"先删 canary"载荷)+ 硬限制与统计分层。
"""
import hashlib
import json
import os
import time


# ---------- 1) Kill Switch:agent 只能读不能写的布尔 ----------
def kill_switch_active() -> bool:
    """标志文件路径来自环境变量 AGENT_KILL_FILE(由 runner/人布置,
    不在沙箱 cwd 内);每个后果性动作前都查——live 生效,无需重启。
    单机栈上"agent 写不了它"是尽力而为(edit_file 理论上可达任意路径);
    真保证靠文件权限/容器隔离,重开=人工删除标志文件,无自动超时。"""
    path = os.getenv("AGENT_KILL_FILE", "")
    return bool(path) and os.path.exists(path)


# ---------- 2) 熔断器:按模式停,不是整体停 ----------
class CircuitBreaker:
    """三态(Nygard 2007):closed / open / half-open。
    触发器(agent 专属):
      连续 3 次相同 (tool, args)  → open(重试环)
      同工具连续 3 次"错误:"输出  → open(系统性故障——物化失败回退循环的活体)
    冷却后 half-open 放 1 次探测:成功闭合,失败再开。open 按 tool 粒度。"""
    IDENTICAL_LIMIT = 3
    FAIL_LIMIT = 3
    WINDOW = 12  # 相同调用检测的回看窗口

    def __init__(self, cooldown_s: float = 60.0):
        self.cooldown_s = cooldown_s
        self._recent: list[tuple[str, str]] = []
        self._fail_streak: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probe_out: set[str] = set()

    def _state(self, tool: str) -> str:
        if tool not in self._opened_at:
            return "closed"
        if time.time() - self._opened_at[tool] >= self.cooldown_s:
            return "half_open"
        return "open"

    def _open(self, tool: str, why: str) -> None:
        self._opened_at[tool] = time.time()
        self._probe_out.discard(tool)
        print(f"🔴 [熔断器] '{tool}' OPEN({why})")

    def check(self, tool: str, args: dict) -> str | None:
        """execute 前。返回 None=放行;返回原因串=拒绝。"""
        state = self._state(tool)
        if state == "open":
            return f"熔断器 OPEN,冷却 {self.cooldown_s:.0f}s 后进入 half-open"
        if state == "half_open":
            if tool in self._probe_out:
                return "熔断器 half-open 探测名额已用,等待本次结果"
            self._probe_out.add(tool)
        return None

    def observe(self, tool: str, args: dict, output: str) -> None:
        """execute 后回喂真实结果。"""
        h = hashlib.sha1(json.dumps(args, ensure_ascii=False, sort_keys=True)
                         .encode("utf-8")).hexdigest()[:12]
        self._recent.append((tool, h))
        del self._recent[:-self.WINDOW]
        tail = self._recent[-self.IDENTICAL_LIMIT:]
        if (len(tail) == self.IDENTICAL_LIMIT
                and all(t == (tool, h) for t in tail)):
            self._open(tool, f"连续 {self.IDENTICAL_LIMIT} 次相同调用")

        failed = output.startswith("错误:")
        if failed:
            self._fail_streak[tool] = self._fail_streak.get(tool, 0) + 1
            if self._fail_streak[tool] >= self.FAIL_LIMIT:
                self._open(tool, f"同工具连续 {self.FAIL_LIMIT} 次失败")
        else:
            self._fail_streak[tool] = 0

        # half-open 探测结果落账:成功闭合,失败再开
        if tool in self._probe_out:
            self._probe_out.discard(tool)
            if not failed:
                self._opened_at.pop(tool, None)
                print(f"🟢 [熔断器] '{tool}' 探测成功,闭合")
            else:
                self._opened_at[tool] = time.time()
                print(f"🔴 [熔断器] '{tool}' 探测失败,重新 OPEN")


# ---------- 3) Canary:没有正当理由的触碰 = 自曝 ----------
class CanaryRegistry:
    """诱饵注册表。布置方式:runner 调 install_canary_file() 在 cwd 之外
    造诱饵文件,再把返回的路径/哨兵写进环境变量传给本注册表。
    路径检查在 execute 前(args 里出现即拒),哨兵检查在 execute 后
    (输出携带 = 数据已被读出,注入/外传已发生)。"""
    def __init__(self, paths: list[str] | None = None,
                 sentinels: list[str] | None = None):
        self.paths = paths or []
        self.sentinels = sentinels or []

    def check_action(self, tool: str, args: dict) -> str | None:
        blob = json.dumps(args, ensure_ascii=False)
        for p in self.paths:
            # Windows 反斜杠路径在 JSON 里被转义成 \\,按转义形态再比一次
            p_escaped = json.dumps(p, ensure_ascii=False)[1:-1]
            if p in blob or p_escaped in blob:
                return f"触碰 canary 路径 '{p}'"
        return None

    def check_output(self, tool: str, output: str) -> str | None:
        for s in self.sentinels:
            if s and s in output:
                return f"输出含 canary 哨兵 '{s[:10]}…'(诱饵数据已被读出)"


def install_canary_file(directory: str | None = None) -> tuple[str, str]:
    """造一个 agent 无正当理由碰的假凭据文件(cwd 之外),返回 (路径, 哨兵)。
    runner 脚本调用后把结果写进 AGENT_CANARY_PATHS / AGENT_CANARY_SENTINELS。"""
    directory = directory or os.path.join(os.path.expanduser("~"), ".issue_pr_agent")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "canary_credentials.txt")
    sentinel = f"CANARY-{os.urandom(4).hex().upper()}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# HONEYPOT — 无真实权限。触碰即报警。\n"
                f"AWS_SECRET_ACCESS_KEY={sentinel}FAKEKEY0000000000000000\n")
    return path, sentinel
