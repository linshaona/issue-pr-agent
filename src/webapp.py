"""切片9 发布层:FastAPI Web 端(P17/L20 渐进发布的最小形态)。

端点:
  POST /api/runs          提交任务(issue 文本或 GitHub issue URL),后台线程跑 agent
  GET  /api/runs          运行列表
  GET  /api/runs/{id}     运行全貌 + 运行中实时的工具调用轨迹与 token 计数
  GET  /api/killswitch    L14 kill switch 状态(Web 端 = "agent 之外"的持有者)
  PUT  /api/killswitch    触发(创建标志文件,live 生效,无需重启)
  DELETE /api/killswitch  人工重开(删除标志文件)
  GET  /api/summary       可观测聚合(P17/L13)
  GET  /                  控制台仪表盘(内联 HTML,无构建工具)

运行中实时性:run 线程把 agent 对象挂在 entry["_agent"],详情端点轮询
last_trajectory 读到"到现在为止"的工具调用与 token(ReWOO 已逐节点回写)。
安全注记:agent 会执行 LLM 计划的工具调用——只绑 127.0.0.1,
裸暴露公网等于把 L27 的攻击面开给全世界。
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
from src.observability import export_run, summarize
from src.reviewer import review as run_review
from src.rewoo import ReWOOAgent
from src.tools import registry as default_registry
from src.tripwire import install_canary_file

_HOME_STATE = os.path.join(os.path.expanduser("~"), ".issue_pr_agent")
KILL_FILE = os.environ.setdefault("AGENT_KILL_FILE", os.path.join(_HOME_STATE, "KILL"))

os.makedirs(_HOME_STATE, exist_ok=True)
if "AGENT_CANARY_PATHS" not in os.environ:
    _canary_path, _canary_sentinel = install_canary_file(_HOME_STATE)
    os.environ["AGENT_CANARY_PATHS"] = _canary_path
    os.environ["AGENT_CANARY_SENTINELS"] = _canary_sentinel

app = FastAPI(title="issue-pr-agent", version="0.2.0")
RUNS: dict[str, dict] = {}


class RunRequest(BaseModel):
    issue: str
    mode: str = "rewoo"               # rewoo | react
    permission_mode: str = "default"  # plan | default | unattended | bypass
    max_rounds: int = 20


def _json_safe(entry: dict, live: bool = False) -> dict:
    """剥离下划线前缀的内部键(线程/agent 对象),序列化安全。"""
    out = {k: v for k, v in entry.items() if not k.startswith("_")}
    if live and "_agent" in entry:
        traj = getattr(entry["_agent"], "last_trajectory", None)
        if traj is not None:
            out["tool_calls"] = [
                {"round": c.round, "name": c.name,
                 "args": str(c.args)[:220],
                 "output": c.output_snippet[:260]}
                for c in traj.tool_calls]
            out["tokens"] = traj.total_tokens
            out["rounds"] = traj.total_rounds
    return out


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
        entry["_agent"] = agent          # 详情端点轮询它拿实时轨迹/token

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

        pseudo = SimpleNamespace(passed=None, reason="web run",
                                 keyword_coverage=0.0, file_hit=False)
        export_run(run_id, mode, agent.last_trajectory, pseudo, tags, agent.policy)

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
                    "issue_preview": issue_text[:160],
                    "_issue_full": issue_text,
                    "submitted_at": time.strftime("%H:%M:%S")}
    threading.Thread(target=_run_agent, daemon=True,
                     args=(run_id, issue_text, req.mode,
                           req.permission_mode, req.max_rounds)).start()
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs")
def list_runs():
    return [{"run_id": k, **_json_safe(v)} for k, v in RUNS.items()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "run 不存在")
    return {"run_id": run_id, **_json_safe(RUNS[run_id], live=True)}


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
<title>issue-pr-agent 控制台</title><style>
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',system-ui;background:#0b1220;color:#e2e8f0}
header{display:flex;align-items:center;gap:14px;padding:12px 20px;background:#0f172a;border-bottom:1px solid #1e293b;position:sticky;top:0;z-index:5}
header h1{font-size:17px;margin:0}header .sub{font-size:12px;color:#64748b}
.kbtn{margin-left:auto}
button,select,textarea{font-size:13px;border-radius:8px;border:1px solid #334155;background:#16213a;color:#e2e8f0;padding:8px 12px}
button{cursor:pointer;border:none}button:hover{filter:brightness(1.15)}
.primary{background:#2563eb}.danger{background:#b91c1c}.ghost{background:#1e293b}
main{display:grid;grid-template-columns:340px 1fr;gap:16px;padding:16px;max-width:1280px;margin:0 auto}
.card{background:#101a2e;border:1px solid #1e293b;border-radius:12px;padding:14px}
textarea{width:100%;height:96px;resize:vertical}
label{font-size:12px;color:#94a3b8;display:block;margin:8px 0 4px}
.row{display:flex;gap:8px;margin-top:10px}.row select{flex:1}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{padding:7px 6px;text-align:left;border-bottom:1px solid #1e293b}
tbody tr{cursor:pointer}tbody tr:hover{background:#16213a}
.badge{padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600}
.queued{background:#334155}.running{background:#b45309}.done{background:#15803d}.error{background:#b91c1c}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
.stat{background:#101a2e;border:1px solid #1e293b;border-radius:12px;padding:10px 12px}
.stat .v{font-size:20px;font-weight:700}.stat .k{font-size:11px;color:#64748b}
.tokenpanel{display:flex;align-items:flex-end;gap:4px;height:64px;margin:8px 0 2px}
.tokenpanel .bar{flex:1;background:linear-gradient(180deg,#3b82f6,#1d4ed8);border-radius:4px 4px 0 0;min-width:6px}
.transcript{display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 240px);overflow:auto;padding-right:4px}
.bubble{background:#16213a;border:1px solid #1e293b;border-radius:12px;padding:10px 12px;font-size:13px;white-space:pre-wrap}
.bubble.user{background:#1e3a5f;align-self:flex-start;max-width:92%}
.bubble.final{background:#14261a;border-color:#1d4d2a}
.tcall{background:#101a2e;border:1px solid #1e293b;border-left:3px solid #3b82f6;border-radius:10px;padding:9px 12px;font-size:12.5px}
.tcall.refused{border-left-color:#ef4444}.tcall.ok{border-left-color:#22c55e}
.tcall .tname{font-weight:700;font-family:Consolas,monospace;color:#93c5fd}
.tcall .targs{font-family:Consolas,monospace;color:#94a3b8;font-size:11.5px;word-break:break-all;margin-top:3px}
.tcall details{margin-top:5px}.tcall summary{cursor:pointer;color:#64748b;font-size:11.5px}
.tcall pre{margin:4px 0 0;white-space:pre-wrap;color:#cbd5e1;font-size:11.5px;max-height:180px;overflow:auto}
.rev{padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600}
.rev.pass{background:#15803d}.rev.soft_fail{background:#b45309}.rev.hard_fail{background:#b91c1c}.rev.review_error{background:#475569}
.empty{color:#475569;text-align:center;padding:40px 0;font-size:13px}
h3{font-size:13px;color:#94a3b8;margin:14px 0 6px}
</style></head><body>
<header><h1>Issue→PR Agent</h1><span class="sub">ReWOO · 四层防御 · 评审员 · 可观测</span>
<span id="kstate" class="sub"></span>
<button class="danger kbtn" onclick="kill(true)">🚨 Kill Switch</button>
<button class="ghost" onclick="kill(false)">重开</button></header>
<main>
<div>
  <div class="card">
    <label>任务(issue 正文 / owner/repo#123)</label>
    <textarea id="issue" placeholder="【Bug】greet() 应返回 'hello, world!' ..."></textarea>
    <div class="row">
      <div style="flex:1"><label>模式</label><select id="mode" style="width:100%"><option value="rewoo">ReWOO</option><option value="react">ReAct</option></select></div>
      <div style="flex:1"><label>权限</label><select id="pmode" style="width:100%"><option value="default">default</option><option value="plan">plan 只读</option><option value="unattended">unattended</option><option value="bypass">bypass</option></select></div>
    </div>
    <div class="row"><button class="primary" style="flex:1" onclick="submit()">▶ 提交任务</button></div>
  </div>
  <h3>运行列表</h3>
  <div class="card" style="padding:4px 10px"><table id="runs"></table></div>
</div>
<div id="detail"><div class="empty">← 提交任务或点击左侧列表查看运行</div></div>
</main>
<script>
let sel=null;
async function j(u,o){const r=await fetch(u,o);return r.json()}
async function submit(){const b=document.getElementById('issue').value;if(!b.trim())return;
  await j('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({issue:b,mode:mode.value,permission_mode:pmode.value})});refresh()}
async function kill(on){await j('/api/killswitch',{method:on?'PUT':'DELETE'});refresh()}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
async function refresh(){
  const k=await j('/api/killswitch');
  document.getElementById('kstate').textContent=k.active?'🔒 KILL ACTIVE — agent 全部动作被拒':'';
  const rs=await j('/api/runs');
  document.getElementById('runs').innerHTML='<tr><th>run</th><th>状态</th><th>权限</th><th>token</th></tr>'+
    rs.map(r=>`<tr onclick="sel='${r.run_id}';detail()" style="${r.run_id===sel?'background:#1e293b':''}">
      <td style="font-family:monospace">${r.run_id}</td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>${r.permission_mode}</td><td>${r.tokens??'-'}</td></tr>`).join('');
  if(sel)detailLight()}
let lastCalls=-1;
async function detailLight(){const d=await j('/api/runs/'+sel);if(d.tool_calls&&d.tool_calls.length!==lastCalls){lastCalls=d.tool_calls.length;render(d)}}
async function detail(){lastCalls=-1;const d=await j('/api/runs/'+sel);render(d)}
function render(d){
  const el=document.getElementById('detail');
  const dur=d.duration_s?d.duration_s+'s':(d.status==='running'?'运行中…':'-');
  const rev=d.review?`<span class="rev ${d.review.verdict}">评审 ${d.review.verdict} ${d.review.total}/10</span>`:'';
  const fm=(d.failure_modes||[]);
  const cc=(d.policy&&d.policy.call_counts)||{};
  const mx=Math.max(1,...Object.values(cc));
  const brk=(d.policy&&d.policy.breaker_open&&d.policy.breaker_open.length)?`<div style="color:#f87171;font-size:12px">🔴 熔断开启: ${d.policy.breaker_open.join(', ')}</div>`:'';
  const calls=(d.tool_calls||[]).map(c=>{
    const refused=(c.output||'').startsWith('错误:');
    return `<div class="tcall ${refused?'refused':'ok'}">
      <span class="tname">🔧 ${c.name}</span> <span style="color:#475569;font-size:11px">round ${c.round}</span>
      ${refused?'<span class="badge error" style="margin-left:6px">被拒</span>':''}
      <div class="targs">${esc(c.args)}</div>
      <details${refused?' open':''}><summary>输出</summary><pre>${esc(c.output)}</pre></details></div>`}).join('');
  el.innerHTML=`
  <div class="stats">
    <div class="stat"><div class="v">${d.tokens??0}</div><div class="k">tokens</div></div>
    <div class="stat"><div class="v">${d.rounds??'-'}</div><div class="k">轮数/节点</div></div>
    <div class="stat"><div class="v">${dur}</div><div class="k">耗时</div></div>
    <div class="stat"><div class="v">${d.status==='done'?fm.length:'-'}</div><div class="k">失败模式${fm.length?' ⚠️':''}</div></div>
  </div>
  <div class="card">
    <div style="display:flex;align-items:center;gap:10px">
      <span class="badge ${d.status}">${d.status}</span>
      <span style="font-size:12px;color:#94a3b8">权限 ${d.permission_mode} · 模式 ${d.mode}</span>${rev}
      ${fm.length?`<span style="font-size:12px;color:#fbbf24">⚠️ ${fm.join(', ')}(半径 ${d.cascade_radius})</span>`:''}
    </div>
    <h3 style="margin-top:10px">Token 面板</h3>
    <div class="tokenpanel">${Object.entries(cc).map(([t,n])=>`<div class="bar" style="height:${Math.max(8,n/mx*100)}%" title="${t}: ${n}"></div>`).join('')||'<span class="sub" style="font-size:11px;color:#475569">(尚无工具调用)</span>'}</div>
    <div style="font-size:11px;color:#64748b">${Object.entries(cc).map(([t,n])=>`${t}×${n}`).join(' · ')||'按工具调用分布,高度=调用次数'}</div>${brk}
  </div>
  <h3>执行轨迹</h3>
  <div class="transcript">
    <div class="bubble user">📝 ${esc(d.issue_preview)}…</div>
    ${calls||'<div class="empty">(尚无工具调用)</div>'}
    ${d.status==='done'||d.status==='error'?`<div class="bubble final">${esc((d.report||d.error||'').slice(0,2400))}</div>`:''}
  </div>`}
refresh();setInterval(refresh,2000)
</script></body></html>"""
