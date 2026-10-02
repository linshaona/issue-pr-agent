"""切片9 发布层:FastAPI Web 端(P17/L20 渐进发布的最小形态)。

端点:
  POST /api/runs          提交任务(issue 文本或 GitHub issue URL),后台线程跑 agent
  GET  /api/runs          运行列表(内存注册表)
  GET  /api/runs/{id}     单次运行全貌:报告/评审/失败模式/权限策略/熔断状态
  GET  /api/killswitch    L14 kill switch 状态(Web 端 = "agent 之外"的持有者)
  PUT  /api/killswitch    触发(创建标志文件,live 生效,无需重启)
  DELETE /api/killswitch  人工重开(删除标志文件)
  GET  /api/summary       可观测聚合(P17/L13)
  GET  /                  单页仪表盘(内联 HTML,无构建工具)

安全注记:agent 会执行 LLM 计划的工具调用——本服务默认只绑 127.0.0.1;
裸暴露公网等于把 L27 的攻击面开给全世界。kill switch 由本进程持有,
但检查发生在 agent 的每个动作前(agent 只能读不能写,判据见 tripwire.py)。
"""
import os
import threading
import time
import uuid
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.agent import Agent
from src.failure_tags import tag as tag_failure_modes
from src.handle_github_url import handle_url
from src.init_func import init_agent_args, init_github_token
from src.observability import export_run, summarize, print_summary
from src.reviewer import review as run_review
from src.rewoo import ReWOOAgent
from src.tools import registry as default_registry
from src.tripwire import install_canary_file

_HOME_STATE = os.path.join(os.path.expanduser("~"), ".issue_pr_agent")
KILL_FILE = os.environ.setdefault("AGENT_KILL_FILE", os.path.join(_HOME_STATE, "KILL"))

# 进程级布置:canary 诱饵 + kill 标志路径(子线程跑的 agent 从环境变量读到)
os.makedirs(_HOME_STATE, exist_ok=True)
if "AGENT_CANARY_PATHS" not in os.environ:
    _canary_path, _canary_sentinel = install_canary_file(_HOME_STATE)
    os.environ["AGENT_CANARY_PATHS"] = _canary_path
    os.environ["AGENT_CANARY_SENTINELS"] = _canary_sentinel

app = FastAPI(title="issue-pr-agent", version="0.1.0")
RUNS: dict[str, dict] = {}


class RunRequest(BaseModel):
    issue: str                    # issue 正文,或 GitHub issue URL(owner/repo#123)
    mode: str = "rewoo"           # rewoo | react
    permission_mode: str = "default"  # plan | default | unattended | bypass
    max_rounds: int = 20


def _run_agent(run_id: str, issue_text: str, mode: str,
               permission_mode: str, max_rounds: int) -> None:
    entry = RUNS[run_id]
    try:
        base_url, api_key, model_name = init_agent_args()
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        if mode == "rewoo":
            agent = ReWOOAgent(client=client, model=model_name,
                               registry=default_registry,
                               solve_system=Agent.DEFAULT_SYSTEM_PROMPT,
                               permission_mode=permission_mode)
        else:
            agent = Agent(client=client, model=model_name, max_tokens=8192,
                          permission_mode=permission_mode)

        from src.tools import _read_test_records
        try:
            records_before = len(_read_test_records())
        except Exception:
            records_before = 0

        entry["status"] = "running"
        report = agent.run(issue_text, max_rounds)

        try:
            new_records = _read_test_records()[records_before:]
        except Exception:
            new_records = []
        tags = tag_failure_modes(agent.last_trajectory, default_registry, new_records)
        try:
            review = run_review(client, model_name, issue_text,
                                agent.last_trajectory, new_records)
        except Exception as e:
            review = {"verdict": "review_error", "total": -1,
                      "verdict_reason": str(e)[:120]}

        # 无评测用例,构造占位 result(可观测导出只取字段)
        pseudo = SimpleNamespace(passed=None, reason="web run",
                                 keyword_coverage=0.0, file_hit=False)
        export_run(run_id, mode, agent.last_trajectory, pseudo,
                   tags, agent.policy)

        entry.update(
            status="done",
            report=report,
            review=review,
            failure_modes=tags["modes"],
            cascade_radius=tags["cascade_radius"],
            tokens=agent.last_trajectory.total_tokens,
            rounds=agent.last_trajectory.total_rounds,
            duration_s=round(agent.last_trajectory.duration, 1),
            policy={"mode": agent.policy.mode,
                    "call_counts": agent.policy.call_counts,
                    "breaker_open": sorted(agent.policy.breaker._opened_at.keys())},
            finished_at=time.strftime("%H:%M:%S"),
        )
    except Exception as e:
        entry.update(status="error", error=str(e)[:400],
                     finished_at=time.strftime("%H:%M:%S"))


@app.post("/api/runs")
def submit_run(req: RunRequest):
    if req.mode not in ("rewoo", "react"):
        raise HTTPException(400, "mode 必须是 rewoo | react")
    if req.permission_mode not in ("plan", "default", "unattended", "bypass"):
        raise HTTPException(400, "permission_mode 必须是 plan|default|unattended|bypass")
    issue_text = req.issue.strip()
    if issue_text.startswith(("http://", "https://")) or (
            "/" in issue_text and "#" in issue_text and len(issue_text) < 120):
        token = init_github_token()
        issue_text = handle_url(issue_text, token)
    run_id = uuid.uuid4().hex[:8]
    RUNS[run_id] = {"status": "queued", "mode": req.mode,
                    "permission_mode": req.permission_mode,
                    "issue_preview": issue_text[:120],
                    "submitted_at": time.strftime("%H:%M:%S")}
    threading.Thread(target=_run_agent, daemon=True,
                     args=(run_id, issue_text, req.mode,
                           req.permission_mode, req.max_rounds)).start()
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs")
def list_runs():
    return [{"run_id": k, **{f: v.get(f) for f in
             ("status", "mode", "permission_mode", "issue_preview",
              "submitted_at", "finished_at", "tokens", "rounds")}}
            for k, v in RUNS.items()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "run 不存在")
    return {"run_id": run_id, **RUNS[run_id]}


@app.get("/api/killswitch")
def kill_state():
    return {"active": os.path.exists(KILL_FILE), "file": KILL_FILE}


@app.put("/api/killswitch")
def kill_on():
    os.makedirs(os.path.dirname(KILL_FILE), exist_ok=True)
    open(KILL_FILE, "w").close()
    return {"active": True, "note": "agent 的下一个动作前会被拒绝(live,无需重启)"}


@app.delete("/api/killswitch")
def kill_off():
    if os.path.exists(KILL_FILE):
        os.remove(KILL_FILE)
    return {"active": False, "note": "人工重开完成(无自动超时)"}


@app.get("/api/summary")
def summary():
    return summarize()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>issue-pr-agent</title><style>
body{font-family:system-ui;max-width:900px;margin:24px auto;padding:0 12px;background:#0f172a;color:#e2e8f0}
textarea,select,button{font-size:14px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;padding:8px}
textarea{width:100%;height:90px}button{cursor:pointer;background:#2563eb;border:none;padding:8px 16px}
table{width:100%;border-collapse:collapse;margin-top:12px}td,th{border:1px solid #334155;padding:6px;text-align:left;font-size:13px}
.badge{padding:2px 8px;border-radius:10px;font-size:12px}.running{background:#b45309}.done{background:#15803d}.error{background:#b91c1c}.queued{background:#475569}
#report{white-space:pre-wrap;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:12px;margin-top:12px;font-size:13px;max-height:420px;overflow:auto;display:none}
h1{font-size:20px}h2{font-size:15px;color:#94a3b8}</style></head><body>
<h1>Issue→PR Agent 控制台</h1>
<h2>提交任务(粘贴 issue 正文或 owner/repo#123)</h2>
<textarea id="issue" placeholder="【Bug】greet() 应返回 'hello, world!' ..."></textarea>
<p>模式 <select id="mode"><option value="rewoo">ReWOO</option><option value="react">ReAct</option></select>
权限 <select id="pmode"><option value="default">default</option><option value="plan">plan(只读)</option><option value="unattended">unattended</option><option value="bypass">bypass</option></select>
<button onclick="submit()">提交</button>
<button onclick="kill(true)" style="background:#b91c1c">🚨 Kill Switch</button>
<button onclick="kill(false)">重开</button> <span id="kstate"></span></p>
<h2>运行列表(5s 自动刷新)</h2>
<table id="runs"><tr><th>run</th><th>状态</th><th>模式</th><th>权限</th><th>token</th><th>轮数</th><th>提交</th></tr></table>
<div id="report"></div>
<script>
async function j(u,o){const r=await fetch(u,o);return r.json()}
async function submit(){await j('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({issue:document.getElementById('issue').value,mode:mode.value,permission_mode:pmode.value})});refresh()}
async function kill(on){await j('/api/killswitch',{method:on?'PUT':'DELETE'});refresh()}
async function refresh(){
  const k=await j('/api/killswitch');document.getElementById('kstate').textContent=k.active?'🔒 KILL ACTIVE':'';
  const rs=await j('/api/runs');const t=document.getElementById('runs');
  t.innerHTML='<tr><th>run</th><th>状态</th><th>模式</th><th>权限</th><th>token</th><th>轮数</th><th>提交</th></tr>'+
    rs.map(r=>`<tr onclick="detail('${r.run_id}')" style="cursor:pointer">
      <td>${r.run_id}</td><td><span class="badge ${r.status}">${r.status}</span></td>
      <td>${r.mode}</td><td>${r.permission_mode}</td><td>${r.tokens??'-'}</td><td>${r.rounds??'-'}</td><td>${r.submitted_at}</td></tr>`).join('')}
async function detail(id){const d=await j('/api/runs/'+id);const el=document.getElementById('report');
  el.style.display='block';
  el.textContent=`运行 ${id} [${d.status}] 权限=${d.permission_mode} 熔断=${JSON.stringify(d.policy?.breaker_open||[])}
失败模式: ${JSON.stringify(d.failure_modes||[])} | 级联半径: ${d.cascade_radius??'-'}
评审: ${d.review?.verdict||'-'} (${d.review?.total??'-'}/10) 置信度最低 ${d.review?.confidence_min??'-'}
${d.review?.verdict_reason||''}
────────────────────────────
${d.report||d.error||'(运行中…)'}`}
refresh();setInterval(refresh,5000)
</script></body></html>"""
