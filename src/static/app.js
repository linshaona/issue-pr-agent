/* ==========================================================================
   ISSUE→PR AGENT · FRONTEND APPLICATION CONTROLLER (src/static/app.js)
   ========================================================================== */

const STATE = {
  activeTab: 'mission',       // 'mission' | 'observability' | 'defense'
  theme: localStorage.getItem('ipa_theme') || 'dark',
  autoRefresh: true,
  selectedRunId: null,
  runFilter: 'all',           // 'all' | 'running' | 'done' | 'error'
  stepFilter: 'all',          // 'all' | 'errors' | 'mutations'
  expandAllSteps: false,
  lastRenderFingerprint: '',
  runs: [],
  killswitch: { active: false, file: '' },
  presets: { eval_cases: [], security_probes: [] },
  summary: {},
  history: { traces: [], test_runs: [], runs_path: '' },
  system: null,
  historySearch: '',
  selectedTraceIndex: null,
};

const PERMISSION_SPECS = {
  default: {
    label: 'default (标准交互)',
    classes: ['READ', 'WRITE', 'EXEC', 'EXTERNAL'],
    tokenCap: '150,000',
    callCaps: 'run_tests≤8 · edit_file≤12 · push≤3 · pr≤2',
    desc: '全类别放行；高危动作由验证门与工具红线独立把守，配标准预算帽。',
  },
  plan: {
    label: 'plan (只读分析)',
    classes: ['READ'],
    tokenCap: '无硬顶 (仅读)',
    callCaps: '禁止一切 WRITE / EXEC / EXTERNAL',
    desc: '仅允许读取与检索工具；连分支都不建，适合纯根因定位与安全沙箱探测。',
  },
  unattended: {
    label: 'unattended (无人值守)',
    classes: ['READ', 'WRITE', 'EXEC', 'EXTERNAL'],
    tokenCap: '100,000 (收紧)',
    callCaps: 'run_tests≤5 · edit_file≤10 · push≤2 · pr≤1',
    desc: '无人值守收紧预算帽，提前掐断异常重试环，减小爆炸半径。',
  },
  bypass: {
    label: 'bypass (一次性容器)',
    classes: ['READ', 'WRITE', 'EXEC', 'EXTERNAL'],
    tokenCap: '无预算熔断',
    callCaps: '无次数上限 (仍受 Kill Switch / Canary 保护)',
    desc: '仅用于可随时销毁的隔离容器环境。',
  },
};

const DIM_LABELS = {
  problem_fit: '问题契合 (Problem Fit)',
  scope_discipline: '范围纪律 (Scope Discipline)',
  assumptions: '假设记录 (Assumptions)',
  verification_quality: '验证质量 (Verification)',
  handoff_readiness: '交接就绪 (Handoff)',
};

const FAILURE_MODE_LABELS = {
  hallucinated_action: { label: '幻觉动作', desc: '调用了不存在的工具或参数键不在 schema 内' },
  tool_misuse: { label: '工具误用/被拒', desc: '工具执行返回错误或被权限策略/注入守卫拦截' },
  cascade: { label: '级联错误', desc: '首个失败步之后仍继续在坏地基上执行后续动作' },
  success_hallucination: { label: '成功幻觉', desc: '报告自称已修复，但缺少测试取证或末次 exit≠0' },
};

/* ---------- Utilities ---------- */
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtNum(n) {
  if (n === null || n === undefined || n === '-') return '-';
  return Number(n).toLocaleString('en-US');
}

function showToast(msg) {
  const wrap = document.getElementById('toast-container');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 220);
  }, 2600);
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const errJson = await res.json();
      detail = errJson.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

/* Lightweight Markdown to Safe HTML Renderer for Final Reports */
function renderMarkdown(md) {
  if (!md) return '<div class="empty-state">暂无报告内容</div>';
  let html = esc(md);

  // Code blocks ```lang ... ```
  html = html.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const langTag = lang ? `<div style="font-size:10px;color:#38bdf8;margin-bottom:4px;text-transform:uppercase">${lang}</div>` : '';
    return `<pre>${langTag}<code>${code.trim()}</code></pre>`;
  });

  // Headings
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

  // Bold & inline code
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');

  // Unordered list items
  html = html.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  html = html.replace(/<\/ul>\s*<ul>/g, '');

  // Line breaks for remaining paragraphs
  html = html
    .split(/\n{2,}/)
    .map(block => {
      const trimmed = block.trim();
      if (!trimmed) return '';
      if (/^<(h1|h2|h3|pre|ul|ol)/i.test(trimmed)) return trimmed;
      return `<p>${trimmed.replace(/\n/g, '<br>')}</p>`;
    })
    .join('\n');

  return `<div class="md-content">${html}</div>`;
}

/* ---------- Theme & Navigation ---------- */
function applyTheme(theme) {
  STATE.theme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('ipa_theme', theme);
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) {
    btn.innerHTML = theme === 'dark' ? '☀️ 浅色蓝图' : '🌙 暗色战术';
  }
}

function toggleTheme() {
  applyTheme(STATE.theme === 'dark' ? 'light' : 'dark');
}

function switchTab(tabName) {
  STATE.activeTab = tabName;
  document.querySelectorAll('.nav-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.view-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === `view-${tabName}`);
  });

  if (tabName === 'observability') {
    loadObservabilityData();
  } else if (tabName === 'defense') {
    loadSystemData();
  }
}

/* ---------- Task Composer & Presets ---------- */
function updatePolicyPreview() {
  const pmodeEl = document.getElementById('pmode');
  const modeEl = document.getElementById('mode');
  if (!pmodeEl || !modeEl) return;

  const pmode = pmodeEl.value;
  const mode = modeEl.value;
  const spec = PERMISSION_SPECS[pmode] || PERMISSION_SPECS.default;
  const allClasses = ['READ', 'WRITE', 'EXEC', 'EXTERNAL'];

  const pillsHtml = allClasses
    .map(c => {
      const allowed = spec.classes.includes(c);
      return `<span class="risk-pill ${c} ${allowed ? '' : 'off'}">${c}</span>`;
    })
    .join('');

  const modeHint =
    mode === 'rewoo'
      ? 'ReWOO: Planner 一次规划 → Worker 零模型读 + B策略写物化 → Solver 汇总'
      : 'ReAct: 逐轮思考-调用工具循环 + B3 旧输出自动压缩 + 上下文记账';

  const box = document.getElementById('policy-preview-box');
  if (box) {
    box.innerHTML = `
      <div class="policy-preview-row">
        <span style="color:var(--text-secondary);font-weight:600">放行动作类别</span>
        <div class="risk-pills">${pillsHtml}</div>
      </div>
      <div class="policy-preview-row">
        <span style="color:var(--text-secondary)">Token 熔断预算</span>
        <span style="font-family:var(--font-mono);color:var(--text-primary)">${esc(spec.tokenCap)}</span>
      </div>
      <div class="policy-preview-row">
        <span style="color:var(--text-secondary)">单工具次数帽</span>
        <span style="font-family:var(--font-mono);font-size:10.5px;color:var(--text-accent)">${esc(spec.callCaps)}</span>
      </div>
      <div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border-subtle);color:var(--text-muted);font-size:11px">
        <div>⚡ ${esc(modeHint)}</div>
        <div style="margin-top:2px">🛡️ ${esc(spec.desc)}</div>
      </div>
    `;
  }
}

async function loadPresets() {
  try {
    const data = await apiFetch('/api/presets');
    STATE.presets = data;
    const sel = document.getElementById('preset-select');
    if (!sel) return;

    let html = `<option value="">-- 选择基准评测题或安全红队探针一键装填 --</option>`;
    if (data.eval_cases && data.eval_cases.length) {
      html += `<optgroup label="📊 基准评测集 (data/eval_dataset.json)">`;
      data.eval_cases.forEach(c => {
        html += `<option value="eval:${esc(c.id)}">[${esc(c.id)}] ${esc(c.title)} (${esc(c.ground_truth?.difficulty || 'easy')})</option>`;
      });
      html += `</optgroup>`;
    }
    if (data.security_probes && data.security_probes.length) {
      html += `<optgroup label="🛡️ 四层防御与红队探针 (L10/L14/L27)">`;
      data.security_probes.forEach(p => {
        html += `<option value="probe:${esc(p.id)}">${esc(p.title)}</option>`;
      });
      html += `</optgroup>`;
    }
    sel.innerHTML = html;
  } catch (e) {
    console.warn('Failed to load presets:', e);
  }
}

function applyPreset(val) {
  if (!val) return;
  const issueEl = document.getElementById('issue');
  const modeEl = document.getElementById('mode');
  const pmodeEl = document.getElementById('pmode');

  if (val.startsWith('eval:')) {
    const id = val.slice(5);
    const found = (STATE.presets.eval_cases || []).find(c => c.id === id);
    if (found && issueEl) {
      issueEl.value = found.issue_text;
      if (pmodeEl) pmodeEl.value = 'plan'; // 评测定位题推荐 plan 或 default
      updatePolicyPreview();
      showToast(`已装填基准用例: ${found.id}`);
    }
  } else if (val.startsWith('probe:')) {
    const id = val.slice(6);
    const found = (STATE.presets.security_probes || []).find(p => p.id === id);
    if (found && issueEl) {
      issueEl.value = found.issue_text;
      if (modeEl && found.recommended_mode) modeEl.value = found.recommended_mode;
      if (pmodeEl && found.recommended_permission) pmodeEl.value = found.recommended_permission;
      updatePolicyPreview();
      showToast(`已装填安全探针: ${found.title}`);
    }
  }
}

/* ---------- Submit Run & Kill Switch ---------- */
async function submitRun() {
  const issueEl = document.getElementById('issue');
  const modeEl = document.getElementById('mode');
  const pmodeEl = document.getElementById('pmode');
  const roundsEl = document.getElementById('max-rounds');
  const submitBtn = document.getElementById('submit-btn');

  const issue = (issueEl?.value || '').trim();
  if (!issue) {
    showToast('⚠️ 请先输入 Issue 描述、短标识 (owner/repo#123) 或选择预设题目');
    issueEl?.focus();
    return;
  }

  const payload = {
    issue,
    mode: modeEl ? modeEl.value : 'rewoo',
    permission_mode: pmodeEl ? pmodeEl.value : 'default',
    max_rounds: roundsEl ? parseInt(roundsEl.value, 10) || 20 : 20,
  };

  try {
    if (submitBtn) submitBtn.disabled = true;
    const res = await apiFetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    STATE.selectedRunId = res.run_id;
    STATE.lastRenderFingerprint = '';
    showToast(`🚀 任务 #${res.run_id} 已提交 (${payload.mode.toUpperCase()} · ${payload.permission_mode})`);
    await refreshAll();
  } catch (e) {
    showToast(`❌ 提交失败: ${e.message}`);
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

async function seedDemoRun() {
  try {
    const res = await apiFetch('/api/runs/demo', { method: 'POST' });
    STATE.selectedRunId = res.run_id;
    STATE.lastRenderFingerprint = '';
    showToast(`✨ 已载入完整演示轨迹 #${res.run_id} (含 Diff 预览与五维评审)`);
    await refreshAll();
  } catch (e) {
    showToast(`❌ 载入演示失败: ${e.message}`);
  }
}

async function setKillSwitch(on) {
  try {
    const res = await apiFetch('/api/killswitch', { method: on ? 'PUT' : 'DELETE' });
    showToast(on ? '🚨 Kill Switch 已触发！所有后续动作立即封锁' : '🟢 Kill Switch 已人工解除');
    await refreshAll();
    if (STATE.activeTab === 'defense') loadSystemData();
  } catch (e) {
    showToast(`❌ 操作失败: ${e.message}`);
  }
}

/* ---------- Live Polling & Mission Control Rendering ---------- */
async function refreshAll() {
  try {
    const [kState, runsList] = await Promise.all([
      apiFetch('/api/killswitch'),
      apiFetch('/api/runs'),
    ]);
    STATE.killswitch = kState;
    STATE.runs = runsList;

    renderKillSwitchState();
    renderRunsList();

    // Auto-select latest run if none selected
    if (!STATE.selectedRunId && runsList.length > 0) {
      STATE.selectedRunId = runsList[0].run_id;
    }

    if (STATE.selectedRunId) {
      await fetchAndRenderDetail(STATE.selectedRunId);
    }
  } catch (e) {
    console.warn('Refresh error:', e);
  }
}

function renderKillSwitchState() {
  const active = Boolean(STATE.killswitch?.active);
  const banner = document.getElementById('kill-banner');
  const statusPill = document.getElementById('kstate-pill');
  const armBtn = document.getElementById('kill-on-btn');
  const disarmBtn = document.getElementById('kill-off-btn');

  if (banner) {
    banner.classList.toggle('active', active);
  }
  if (statusPill) {
    statusPill.innerHTML = active
      ? `<span class="dot danger"></span><span>KILL ACTIVE · 动作全拒</span>`
      : `<span class="dot ok"></span><span>PVE 护栏就绪</span>`;
  }
  if (armBtn) {
    armBtn.classList.toggle('armed', active);
  }
  if (disarmBtn) {
    disarmBtn.style.display = active ? 'inline-flex' : 'none';
  }
}

function setRunFilter(filter) {
  STATE.runFilter = filter;
  document.querySelectorAll('[data-run-filter]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.runFilter === filter);
  });
  renderRunsList();
}

function selectRun(runId) {
  STATE.selectedRunId = runId;
  STATE.lastRenderFingerprint = '';
  renderRunsList();
  fetchAndRenderDetail(runId, true);
}

function renderRunsList() {
  const container = document.getElementById('runs-list-container');
  const countBadge = document.getElementById('runs-count-badge');
  if (countBadge) countBadge.textContent = STATE.runs.length;
  if (!container) return;

  const filtered = STATE.runs.filter(r => {
    if (STATE.runFilter === 'all') return true;
    return r.status === STATE.runFilter;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding:28px 12px">
        <div style="font-size:20px;margin-bottom:6px">📭</div>
        <div style="font-size:12px">当前筛选下暂无会话运行记录</div>
        <div style="font-size:11px;margin-top:4px;color:var(--text-muted)">可在上方选择预设题目提交，或切换到「可观测与历史账本」查看落盘记录</div>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered
    .map(r => {
      const isSelected = r.run_id === STATE.selectedRunId;
      const revBadge = r.review
        ? `<span class="badge ${esc(r.review.verdict)}" style="font-size:9.5px;padding:1px 6px">${esc(r.review.verdict)} ${r.review.total}/10</span>`
        : '';
      const durText = r.duration_s
        ? `${r.duration_s}s`
        : r.elapsed_s
        ? `${r.elapsed_s}s…`
        : '-';
      return `
        <div class="run-item ${isSelected ? 'selected' : ''}" onclick="selectRun('${esc(r.run_id)}')">
          <div class="run-item-top">
            <div style="display:flex;align-items:center;gap:6px">
              <span class="run-id-mono">#${esc(r.run_id)}</span>
              <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
              ${revBadge}
            </div>
            <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">${esc(r.submitted_at || '')}</span>
          </div>
          <div class="run-preview-text">${esc(r.target_input || r.issue_preview || '')}</div>
          <div class="run-item-meta">
            <span>${esc((r.mode || 'rewoo').toUpperCase())} · 权限:${esc(r.permission_mode)}</span>
            <span>🪙 ${fmtNum(r.tokens ?? 0)} tok · ⏱ ${esc(durText)}</span>
          </div>
        </div>
      `;
    })
    .join('');
}

async function fetchAndRenderDetail(runId, force = false) {
  try {
    const d = await apiFetch(`/api/runs/${runId}`);
    const fingerprint = JSON.stringify({
      id: d.run_id,
      status: d.status,
      tokens: d.tokens,
      rounds: d.rounds,
      callsLen: (d.tool_calls || []).length,
      review: d.review?.verdict,
      stepFilter: STATE.stepFilter,
      expandAll: STATE.expandAllSteps,
    });

    // If only elapsed_s changed while running, update timer inline without destroying DOM state
    if (!force && fingerprint === STATE.lastRenderFingerprint) {
      const durEl = document.getElementById('live-duration-val');
      if (durEl && d.status === 'running' && d.elapsed_s !== undefined) {
        durEl.textContent = `${d.elapsed_s}s`;
      }
      return;
    }

    STATE.lastRenderFingerprint = fingerprint;
    renderRunDetail(d);
  } catch (e) {
    console.warn('Failed to fetch run detail:', e);
  }
}

function setStepFilter(f) {
  STATE.stepFilter = f;
  if (STATE.selectedRunId) fetchAndRenderDetail(STATE.selectedRunId, true);
}

function toggleExpandAllSteps() {
  STATE.expandAllSteps = !STATE.expandAllSteps;
  if (STATE.selectedRunId) fetchAndRenderDetail(STATE.selectedRunId, true);
}

function copyReportText(encodedText) {
  const text = decodeURIComponent(encodedText);
  navigator.clipboard.writeText(text).then(() => {
    showToast('📋 诊断与修复报告已复制到剪贴板');
  });
}

function renderRunDetail(d) {
  const el = document.getElementById('detail-stage');
  if (!el) return;

  const tokens = d.tokens ?? 0;
  const maxTokens = d.policy?.max_tokens || (d.permission_mode === 'unattended' ? 100000 : 150000);
  const tokenPct = maxTokens ? Math.min(100, Math.round((tokens / maxTokens) * 100)) : 15;
  const tokenBarClass = tokenPct >= 90 ? 'danger' : tokenPct >= 70 ? 'warn' : '';

  const dur = d.duration_s
    ? `${d.duration_s}s`
    : d.status === 'running'
    ? `${d.elapsed_s ?? 0}s`
    : '-';

  const fm = d.failure_modes || [];
  const fDetail = d.failure_detail || {};
  const hallucSteps = new Set(fDetail.hallucinated_steps || []);
  const misuseSteps = new Set(fDetail.misuse_steps || []);

  const callCounts = d.policy?.call_counts || {};
  const maxCallsSpec = d.policy?.max_calls || {};
  const breakerOpen = d.policy?.breaker_open || [];
  const maxToolCount = Math.max(1, ...Object.values(callCounts));

  // Build Tool Call Quota Histogram
  const toolEntries = Object.entries(callCounts);
  const toolBarsHtml = toolEntries.length
    ? toolEntries
        .map(([tName, count]) => {
          const cap = maxCallsSpec[tName];
          const pct = cap ? Math.min(100, Math.round((count / cap) * 100)) : Math.round((count / maxToolCount) * 100);
          const risk = (d.tool_calls || []).find(c => c.name === tName)?.risk || 'READ';
          return `
            <div class="tool-bar-row">
              <div class="tool-bar-name">
                <span class="risk-pill ${esc(risk)}">${esc(risk)}</span>
                <span title="${esc(tName)}">${esc(tName)}</span>
              </div>
              <div class="tool-bar-track">
                <div class="tool-bar-fill ${esc(risk)}" style="width:${Math.max(8, pct)}%"></div>
              </div>
              <div style="text-align:right;color:var(--text-secondary)">
                ${count}${cap ? ` / ${cap}` : ' 次'}
              </div>
            </div>
          `;
        })
        .join('')
    : `<div class="empty-state" style="padding:20px 0;font-size:12px">尚无放行的工具调用记录</div>`;

  // Build L39 Reviewer 5-Dim Rubric
  let reviewerHtml = '';
  if (d.review && d.review.scores) {
    const rev = d.review;
    const rows = Object.entries(DIM_LABELS)
      .map(([dimKey, dimLabel]) => {
        const score = rev.scores[dimKey] ?? 0;
        const s1Class = score >= 1 ? (score === 2 ? 'filled-2' : 'filled-1') : 'filled-0';
        const s2Class = score === 2 ? 'filled-2' : '';
        return `
          <div class="rubric-row">
            <span style="color:var(--text-secondary)">${esc(dimLabel)}</span>
            <div class="rubric-segments">
              <div class="rubric-seg ${s1Class}"></div>
              <div class="rubric-seg ${s2Class}"></div>
            </div>
            <span style="font-family:var(--font-mono);text-align:right;font-weight:700">${score}/2</span>
          </div>
        `;
      })
      .join('');

    reviewerHtml = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge ${esc(rev.verdict)}">${esc(rev.verdict)} · ${rev.total}/10</span>
          ${
            rev.low_confidence
              ? `<span class="badge soft_fail" title="最低维置信度低于 0.6，仅供人工复核参考">⚠️ 低置信度 (${Number(rev.confidence_min || 0).toFixed(2)})</span>`
              : `<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">置信度下限: ${Number(rev.confidence_min || 0).toFixed(2)}</span>`
          }
        </div>
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">+${fmtNum(rev.review_tokens || 0)} tok</span>
      </div>
      <div class="rubric-list">${rows}</div>
      ${
        rev.verdict_reason
          ? `<div class="verdict-reason-box"><strong>评审员裁决依据：</strong>${esc(rev.verdict_reason)}</div>`
          : ''
      }
    `;
  } else if (d.review && d.review.verdict === 'review_error') {
    reviewerHtml = `
      <div class="empty-state" style="padding:18px 0">
        <span class="badge review_error">REVIEW ERROR</span>
        <div style="margin-top:8px;font-size:12px">${esc(d.review.verdict_reason || '评审阶段异常')}</div>
      </div>
    `;
  } else {
    reviewerHtml = `
      <div class="empty-state" style="padding:24px 0;font-size:12px">
        ${d.status === 'running' ? '⏳ 待 Builder 完成后由独立 Reviewer 量规打分…' : '暂无五维评审数据'}
      </div>
    `;
  }

  // Filter Tool Calls
  const allCalls = d.tool_calls || [];
  const filteredCalls = allCalls.filter((c, i) => {
    const idx = c.index ?? i;
    const isErr = (c.output || '').startsWith('错误:') || (c.output || '').includes('❌') || hallucSteps.has(idx) || misuseSteps.has(idx);
    if (STATE.stepFilter === 'errors') return isErr;
    if (STATE.stepFilter === 'mutations') return ['WRITE', 'EXEC', 'EXTERNAL'].includes(c.risk);
    return true;
  });

  const callsHtml = filteredCalls.length
    ? filteredCalls
        .map((c, i) => {
          const idx = c.index ?? i;
          const outStr = c.output || '';
          const refused = outStr.startsWith('错误:');
          const testFail = !refused && outStr.includes('❌');
          const stateClass = refused ? 'refused' : testFail ? 'test-fail' : 'ok';
          const risk = c.risk || 'READ';
          const isHalluc = hallucSteps.has(idx);

          // Format Args or Diff Preview for edit_file
          let argsBlock = '';
          const fullArgs = c.args_full || {};
          if (c.name === 'edit_file' && (fullArgs.old_text || fullArgs.new_text)) {
            argsBlock = `
              <div class="step-args-box">
                <span class="arg-kv"><span class="arg-k">file_path:</span> <span class="arg-v">${esc(fullArgs.file_path || '')}</span></span>
              </div>
              <div class="diff-preview">
                ${fullArgs.old_text ? `<div class="diff-del">- ${esc(String(fullArgs.old_text).slice(0, 260))}</div>` : ''}
                ${fullArgs.new_text ? `<div class="diff-add">+ ${esc(String(fullArgs.new_text).slice(0, 260))}</div>` : ''}
              </div>
            `;
          } else if (Object.keys(fullArgs).length > 0) {
            const kvHtml = Object.entries(fullArgs)
              .map(([k, v]) => {
                const valStr = typeof v === 'object' ? JSON.stringify(v) : String(v);
                return `<span class="arg-kv"><span class="arg-k">${esc(k)}:</span> <span class="arg-v">${esc(valStr.slice(0, 200))}</span></span>`;
              })
              .join('');
            argsBlock = `<div class="step-args-box">${kvHtml}</div>`;
          } else {
            argsBlock = `<div class="step-args-box">${esc(c.args)}</div>`;
          }

          const openAttr = refused || testFail || STATE.expandAllSteps ? ' open' : '';

          return `
            <div class="step-card ${stateClass}">
              <div class="step-header">
                <div class="step-title-group">
                  <span class="step-index">#${idx + 1} · ${d.mode === 'rewoo' ? `E${c.round || idx + 1}` : `R${c.round}`}</span>
                  <span class="risk-pill ${esc(risk)}">${esc(risk)}</span>
                  <span class="step-tool-name">🔧 ${esc(c.name)}</span>
                  ${refused ? `<span class="badge refused">⛔ 策略拦截 / 报错</span>` : `<span class="badge pass">✓ 放行执行</span>`}
                  ${testFail ? `<span class="badge soft_fail">❌ 测试未过</span>` : ''}
                  ${isHalluc ? `<span class="badge refused">👻 参数/工具幻觉</span>` : ''}
                </div>
              </div>
              ${argsBlock}
              <details class="step-output-details"${openAttr}>
                <summary>查看工具返回摘要 (${outStr.length} chars)</summary>
                <pre class="step-output-pre">${esc(outStr)}</pre>
              </details>
            </div>
          `;
        })
        .join('')
    : `<div class="empty-state" style="padding:32px 0">当前筛选下暂无工具调用步骤</div>`;

  // Failure Modes & Circuit Breaker Banner
  const failurePills = fm
    .map(m => {
      const info = FAILURE_MODE_LABELS[m] || { label: m, desc: '' };
      return `<span class="badge soft_fail" title="${esc(info.desc)}">⚠️ ${esc(info.label)}</span>`;
    })
    .join(' ');

  const breakerAlert = breakerOpen.length
    ? `<span class="badge refused">🔴 熔断器 OPEN: ${esc(breakerOpen.join(', '))}</span>`
    : `<span class="badge pass">🟢 熔断器 CLOSED</span>`;

  const reportRaw = d.report || d.error || '';

  el.innerHTML = `
    <!-- Run Top Governance Header -->
    <div class="panel" style="margin-bottom:16px">
      <div class="panel-header" style="flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span class="run-id-mono" style="font-size:15px">#${esc(d.run_id)}</span>
          <span class="badge ${esc(d.status)}">${esc(d.status)}</span>
          <span class="sys-pill">引擎: <strong>${esc((d.mode || 'rewoo').toUpperCase())}</strong></span>
          <span class="sys-pill">权限: <strong>${esc(d.permission_mode)}</strong></span>
          ${breakerAlert}
          ${failurePills}
          ${d.cascade_radius ? `<span class="badge soft_fail">级联半径: ${d.cascade_radius} 步</span>` : ''}
        </div>
        <div style="font-family:var(--font-mono);font-size:11.5px;color:var(--text-muted)">
          提交于 ${esc(d.submitted_at || '-')} ${d.finished_at ? `· 完成于 ${esc(d.finished_at)}` : ''}
        </div>
      </div>
    </div>

    <!-- 4 Telemetry KPI Cards -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label"><span>累计 Token 消耗</span><span>${tokenPct}% 预算</span></div>
        <div class="kpi-value">${fmtNum(tokens)}</div>
        <div class="progress-track"><div class="progress-fill ${tokenBarClass}" style="width:${tokenPct}%"></div></div>
        <div class="kpi-foot" style="margin-top:5px">预算上限: ${ maxTokens ? fmtNum(maxTokens) : '无上限' }</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>执行轮数 / 规划节点</span><span>${esc((d.mode || '').toUpperCase())}</span></div>
        <div class="kpi-value">${d.rounds ?? '-'} <span style="font-size:13px;color:var(--text-muted)">/ ${allCalls.length} 步工具</span></div>
        <div class="kpi-foot">最大轮数阈值: ${d.max_rounds || 20}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>运行耗时 (Duration)</span><span>实时计时</span></div>
        <div class="kpi-value" id="live-duration-val">${esc(dur)}</div>
        <div class="kpi-foot">${d.is_timeout ? '⚠️ 触发最大步数超时熔断' : '墙钟耗时 (Wall-clock)'}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>L26 失败模式标签</span><span>确定性签名审计</span></div>
        <div class="kpi-value" style="color:${fm.length ? 'var(--sig-amber)' : 'var(--sig-emerald)'}">
          ${d.status === 'done' ? fm.length : '-'}
        </div>
        <div class="kpi-foot">级联暴露半径: ${d.cascade_radius ?? 0} 步</div>
      </div>
    </div>

    <!-- Dual Telemetry: Tool Call Quota & L39 Independent Reviewer -->
    <div class="dual-telemetry-grid">
      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">📊 工具调用分布与次数配额 (L10 PVE)</h3>
          <span class="panel-subtitle">${allCalls.length} total calls</span>
        </div>
        <div class="panel-body">
          <div class="tool-bars-list">${toolBarsHtml}</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">⚖️ 独立评审员五维量规 (L39 Judge)</h3>
          <span class="panel-subtitle">Builder / Reviewer 分离</span>
        </div>
        <div class="panel-body">${reviewerHtml}</div>
      </div>
    </div>

    <!-- Trajectory Timeline Panel -->
    <div class="panel">
      <div class="panel-header" style="flex-wrap:wrap">
        <h3 class="panel-title">🧭 实时执行轨迹与工具证据链 (Trajectory)</h3>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <div class="filter-pills">
            <button class="filter-pill ${STATE.stepFilter === 'all' ? 'active' : ''}" onclick="setStepFilter('all')">全部 (${allCalls.length})</button>
            <button class="filter-pill ${STATE.stepFilter === 'errors' ? 'active' : ''}" onclick="setStepFilter('errors')">仅拦截/报错</button>
            <button class="filter-pill ${STATE.stepFilter === 'mutations' ? 'active' : ''}" onclick="setStepFilter('mutations')">写/执行/外部</button>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="toggleExpandAllSteps()">
            ${STATE.expandAllSteps ? '🔼 折叠输出' : '🔽 展开全部输出'}
          </button>
        </div>
      </div>
      <div class="panel-body">
        <div class="timeline-container">
          <div class="issue-origin-card">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
              <span style="font-weight:700;font-size:12px;color:var(--text-accent)">📝 任务输入 (Issue 原文)</span>
              <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">${(d.issue_full || d.issue_preview || '').length} chars</span>
            </div>
            <div style="font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;color:var(--text-primary);max-height:140px;overflow-y:auto">${esc(d.issue_full || d.issue_preview || '')}</div>
          </div>

          ${callsHtml}
        </div>

        ${
          reportRaw
            ? `
          <div class="report-card">
            <div class="panel-header" style="background:rgba(16, 185, 129, 0.1)">
              <h3 class="panel-title" style="color:var(--sig-emerald)">📑 Agent 最终诊断与修复报告 (Final Report)</h3>
              <button class="btn btn-ghost btn-sm" onclick="copyReportText('${encodeURIComponent(reportRaw)}')">📋 复制 Markdown</button>
            </div>
            <div class="report-body">
              ${renderMarkdown(reportRaw)}
            </div>
          </div>
        `
            : ''
        }
      </div>
    </div>
  `;
}

/* ==========================================================================
   TAB 2: OBSERVABILITY & OTEL GENAI LEDGER
   ========================================================================== */
async function loadObservabilityData() {
  try {
    const [sumData, histData] = await Promise.all([
      apiFetch('/api/summary'),
      apiFetch('/api/history'),
    ]);
    STATE.summary = sumData;
    STATE.history = histData;
    renderObservabilityView();
  } catch (e) {
    showToast(`加载可观测数据失败: ${e.message}`);
  }
}

function onHistorySearch(val) {
  STATE.historySearch = val.trim().toLowerCase();
  renderObservabilityView();
}

function inspectTraceRow(idx) {
  STATE.selectedTraceIndex = idx;
  renderObservabilityView();
}

function renderObservabilityView() {
  const container = document.getElementById('observability-stage');
  if (!container) return;

  const s = STATE.summary || {};
  const traces = STATE.history?.traces || [];
  const testRuns = STATE.history?.test_runs || [];
  const fmCounts = s.failure_modes || {};
  const maxFm = Math.max(1, ...Object.values(fmCounts));

  const filteredTraces = traces.filter(t => {
    if (!STATE.historySearch) return true;
    const blob = JSON.stringify(t).toLowerCase();
    return blob.includes(STATE.historySearch);
  });

  const selectedTrace =
    STATE.selectedTraceIndex !== null
      ? traces.find(t => t._index === STATE.selectedTraceIndex) || filteredTraces[0]
      : filteredTraces[0];

  const fmBarsHtml = Object.entries(FAILURE_MODE_LABELS)
    .map(([key, meta]) => {
      const cnt = fmCounts[key] || 0;
      const pct = Math.round((cnt / maxFm) * 100);
      return `
        <div class="tool-bar-row" style="grid-template-columns:170px 1fr 60px">
          <span title="${esc(meta.desc)}">${esc(meta.label)}</span>
          <div class="tool-bar-track">
            <div class="tool-bar-fill WRITE" style="width:${cnt ? Math.max(10, pct) : 0}%"></div>
          </div>
          <span style="text-align:right;font-family:var(--font-mono)">${cnt} 次</span>
        </div>
      `;
    })
    .join('');

  const traceRowsHtml = filteredTraces
    .map(t => {
      const passed = t['eval.passed'];
      const passBadge =
        passed === true
          ? `<span class="badge pass">PASS</span>`
          : passed === false
          ? `<span class="badge refused">FAIL</span>`
          : `<span class="badge queued">WEB RUN</span>`;
      const fms = (t.failure_modes || []).join(', ') || '-';
      const isSel = selectedTrace && selectedTrace._index === t._index;
      return `
        <tr style="cursor:pointer;${isSel ? 'background:var(--bg-surface-3)' : ''}" onclick="inspectTraceRow(${t._index})">
          <td style="font-family:var(--font-mono);color:var(--text-muted)">#${t._index}</td>
          <td style="font-family:var(--font-mono);font-weight:700;color:var(--text-accent)">${esc(t.case_id)}</td>
          <td>${passBadge}</td>
          <td style="font-family:var(--font-mono)">${esc((t['gen_ai.agent.type'] || 'rewoo').toUpperCase())}</td>
          <td style="font-family:var(--font-mono)">${esc(t['policy.mode'] || 'default')}</td>
          <td style="font-family:var(--font-mono)">${fmtNum(t['gen_ai.usage.total_tokens'])}</td>
          <td style="font-family:var(--font-mono)">${((t.duration_ms || 0) / 1000).toFixed(1)}s</td>
          <td><span style="font-size:11.5px;color:${t.failure_modes?.length ? 'var(--sig-amber)' : 'var(--text-muted)'}">${esc(fms)}</span></td>
          <td style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">${esc(t.time)}</td>
        </tr>
      `;
    })
    .join('');

  const testRowsHtml = testRuns
    .slice(0, 10)
    .map(tr => {
      const ok = tr.exit_code === 0;
      return `
        <tr>
          <td><span class="badge ${ok ? 'pass' : 'refused'}">exit=${tr.exit_code}</span></td>
          <td style="font-family:var(--font-mono)">${esc(tr.command)}</td>
          <td style="font-family:var(--font-mono)">${tr.duration}s</td>
          <td style="font-family:var(--font-mono);color:var(--text-muted)">${esc(tr.time)}</td>
        </tr>
      `;
    })
    .join('');

  container.innerHTML = `
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label"><span>累计落盘轨迹 (OTel)</span><span>data/runs.jsonl</span></div>
        <div class="kpi-value">${fmtNum(s.runs || 0)}</div>
        <div class="kpi-foot">全量保留 100% 错误与高成本轨迹</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>评测通过率 (Pass Rate)</span><span>客观 Scorer</span></div>
        <div class="kpi-value">${s.runs ? (s.pass_rate * 100).toFixed(1) + '%' : '-'}</div>
        <div class="kpi-foot">文件定位命中 + 关键词覆盖 ≥ 50%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>平均单次成本</span><span>Tokens / Duration</span></div>
        <div class="kpi-value">${s.runs ? fmtNum(Math.round(s.avg_tokens)) : '-'}</div>
        <div class="kpi-foot">平均耗时: ${s.runs ? s.avg_duration_s.toFixed(1) + 's' : '-'}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label"><span>熔断器触发事件</span><span>Circuit Breaker</span></div>
        <div class="kpi-value" style="color:${s.breaker_events ? 'var(--sig-rose)' : 'var(--sig-emerald)'}">${s.breaker_events || 0}</div>
        <div class="kpi-foot">连续 3 次同参调用或报错自动 OPEN</div>
      </div>
    </div>

    <div class="dual-telemetry-grid">
      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">🔬 L26 历史失败模式签名分布</h3>
          <span class="panel-subtitle">确定性规则检出</span>
        </div>
        <div class="panel-body">
          <div class="tool-bars-list">${fmBarsHtml}</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">🔍 OTel GenAI 轨迹属性检视器</h3>
          <span class="panel-subtitle">${selectedTrace ? `Trace #${selectedTrace._index} (${selectedTrace.case_id})` : '点击下方行查看'}</span>
        </div>
        <div class="panel-body">
          ${
            selectedTrace
              ? `<pre class="step-output-pre" style="max-height:175px;margin:0">${esc(JSON.stringify(selectedTrace, null, 2))}</pre>`
              : `<div class="empty-state" style="padding:20px">暂无轨迹记录</div>`
          }
        </div>
      </div>
    </div>

    <div class="panel" style="margin-bottom:18px">
      <div class="panel-header" style="flex-wrap:wrap">
        <h3 class="panel-title">📜 OpenTelemetry GenAI 历史运行账本 (data/runs.jsonl)</h3>
        <div style="display:flex;align-items:center;gap:8px">
          <input
            type="text"
            class="form-input"
            style="width:240px;padding:5px 10px"
            placeholder="搜索 case_id / 失败模式 / 权限..."
            value="${esc(STATE.historySearch)}"
            oninput="onHistorySearch(this.value)"
          />
          <button class="btn btn-ghost btn-sm" onclick="loadObservabilityData()">🔄 刷新账本</button>
        </div>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>序号</th>
              <th>Case / Run ID</th>
              <th>裁决状态</th>
              <th>架构</th>
              <th>权限模式</th>
              <th>Tokens</th>
              <th>耗时</th>
              <th>失败模式 (L26)</th>
              <th>记录时间</th>
            </tr>
          </thead>
          <tbody>
            ${traceRowsHtml || `<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-muted)">无匹配的轨迹记录</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">🧪 验证门测试取证记录 (data/test_runs.jsonl · L38 Gate Evidence)</h3>
        <span class="panel-subtitle">request_pr 放行前置条件: 末次 exit_code === 0</span>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>退出码</th>
              <th>执行命令 (白名单校验)</th>
              <th>耗时</th>
              <th>时间戳</th>
            </tr>
          </thead>
          <tbody>
            ${testRowsHtml || `<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--text-muted)">暂无测试执行记录</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

/* ==========================================================================
   TAB 3: 4-LAYER DEFENSE STACK & SYSTEM CARD
   ========================================================================== */
async function loadSystemData() {
  try {
    STATE.system = await apiFetch('/api/system');
    renderDefenseView();
  } catch (e) {
    showToast(`加载系统架构信息失败: ${e.message}`);
  }
}

function renderDefenseView() {
  const container = document.getElementById('defense-stage');
  if (!container || !STATE.system) return;

  const sys = STATE.system;
  const sec = sys.security || {};
  const tools = sys.tools || [];

  const toolRows = tools
    .map(t => `
      <tr>
        <td style="font-family:var(--font-mono);font-weight:700;color:var(--text-accent)">${esc(t.name)}</td>
        <td><span class="risk-pill ${esc(t.risk)}">${esc(t.risk)}</span></td>
        <td style="font-family:var(--font-mono);font-size:11.5px">${esc((t.params || []).join(', ') || '(无参数)')}</td>
        <td style="color:var(--text-secondary)">${esc(t.description)}</td>
      </tr>
    `)
    .join('');

  container.innerHTML = `
    <!-- 4-Layer Security Pipeline Visual -->
    <div class="defense-pipeline">
      <div class="defense-layer-card">
        <div class="defense-layer-num">LAYER 01 · P15/L10 & L14</div>
        <h4 class="defense-layer-title">🛡️ PermissionPolicy & 三探测器</h4>
        <ul class="defense-layer-list">
          <li><strong>Kill Switch</strong>: 标志文件实时阻断一切后果性动作</li>
          <li><strong>Canary 诱饵</strong>: cwd 外假凭据，触碰路径或输出含哨兵立即报警</li>
          <li><strong>Circuit Breaker</strong>: 连续 3 次同参或报错按工具粒度自动 OPEN</li>
          <li><strong>风险矩阵 × 预算帽</strong>: READ/WRITE/EXEC/EXTERNAL 四档隔离</li>
        </ul>
      </div>

      <div class="defense-layer-card">
        <div class="defense-layer-num">LAYER 02 · P14/L27</div>
        <h4 class="defense-layer-title">🧬 确定性注入守卫 & 工具红线</h4>
        <ul class="defense-layer-list">
          <li><strong>指令形签名拦截</strong>: 扫描中英越狱/覆写指令与密钥外传企图</li>
          <li><strong>导航键豁免</strong>: 区分路径标识符与写世界内容，降低误拒率</li>
          <li><strong>命令白名单</strong>: <code>run_tests</code> 仅放行 python/pytest/pip，禁管道与 curl</li>
          <li><strong>分支红线</strong>: <code>commit_changes</code> 硬编码拒绝直推 ${esc((sec.protected_branches || []).join('/'))}</li>
        </ul>
      </div>

      <div class="defense-layer-card">
        <div class="defense-layer-num">LAYER 03 · P14/L38</div>
        <h4 class="defense-layer-title">⛩️ 验证门 (Verification Gate)</h4>
        <ul class="defense-layer-list">
          <li><strong>测试取证硬约束</strong>: 无 <code>test_runs.jsonl</code> 记录或末次 <code>exit≠0</code> 一律封锁 PR</li>
          <li><strong>空改动拦截</strong>: <code>git diff --stat</code> 为空禁止开空手 PR</li>
          <li><strong>保护分支核验</strong>: 当前处于 main/master 直接 block</li>
          <li><strong>重复 PR 检查</strong>: 自动查询远端开放 PR 防重提</li>
        </ul>
      </div>

      <div class="defense-layer-card">
        <div class="defense-layer-num">LAYER 04 · P14/L26 & L39</div>
        <h4 class="defense-layer-title">⚖️ 失败模式标签器 + 独立评审员</h4>
        <ul class="defense-layer-list">
          <li><strong>幻觉动作检出</strong>: 比对原始 args 与注册表 JSON Schema</li>
          <li><strong>级联半径计量</strong>: 统计首败步之后继续暴露的执行步数</li>
          <li><strong>成功幻觉取证</strong>: 交叉核对报告自述与真实测试退出码</li>
          <li><strong>五维独立评审</strong>: 温度 0.2、宁低勿高、代码确定性重算总分</li>
        </ul>
      </div>
    </div>

    <!-- Live Tripwire & Runtime Configuration -->
    <div class="dual-telemetry-grid">
      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">🚨 L14 实时探针与凭据布防状态</h3>
          <span class="panel-subtitle">零模型自述依赖</span>
        </div>
        <div class="panel-body">
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">Kill Switch 状态</span>
            <span class="badge ${sec.kill_switch_active ? 'refused' : 'pass'}">
              ${sec.kill_switch_active ? '🔒 ACTIVE (全部拒绝)' : '🟢 ARMED (正常放行)'}
            </span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">Kill Switch 标志路径</span>
            <span style="font-family:var(--font-mono);font-size:11px">${esc(sec.kill_switch_file)}</span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">Canary 蜜罐诱饵文件</span>
            <span class="badge ${sec.canary_armed ? 'pass' : 'soft_fail'}">
              ${sec.canary_armed ? '🐤 已布防 (Honeypot Active)' : '⚠️ 未就绪'}
            </span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0">
            <span style="color:var(--text-secondary)">Canary 监控路径</span>
            <span style="font-family:var(--font-mono);font-size:11px">${esc((sec.canary_paths || []).join(', '))}</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3 class="panel-title">⚙️ 系统卡运行基座 (System Card · P18/L26)</h3>
          <span class="panel-subtitle">v${esc(sys.version)}</span>
        </div>
        <div class="panel-body">
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">基座模型 (LLM Model)</span>
            <span style="font-family:var(--font-mono);font-weight:700;color:var(--text-accent)">${esc(sys.llm?.model)}</span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">推理端点 (Base URL)</span>
            <span style="font-family:var(--font-mono);font-size:11.5px">${esc(sys.llm?.base_url)}</span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0;border-bottom:1px solid var(--border-subtle)">
            <span style="color:var(--text-secondary)">GitHub Token 状态</span>
            <span class="badge ${sys.llm?.github_token_configured ? 'pass' : 'soft_fail'}">
              ${sys.llm?.github_token_configured ? '已配置 (5000 req/hr)' : '未配置 (匿名 60 req/hr)'}
            </span>
          </div>
          <div class="policy-preview-row" style="padding:6px 0">
            <span style="color:var(--text-secondary)">受保护红线分支</span>
            <span style="font-family:var(--font-mono)">${esc((sec.protected_branches || []).join(', '))}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Tool Registry & Risk Matrix Table -->
    <div class="panel">
      <div class="panel-header">
        <h3 class="panel-title">🧰 工具注册表与风险分级矩阵 (ToolRegistry · ${tools.length} Tools)</h3>
        <span class="panel-subtitle">动态 Pydantic Schema 生成 + 幻觉参数键自动剥离</span>
      </div>
      <div class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>工具名称</th>
              <th>风险等级 (TOOL_RISK)</th>
              <th>Schema 参数签名</th>
              <th>功能契约说明</th>
            </tr>
          </thead>
          <tbody>${toolRows}</tbody>
        </table>
      </div>
    </div>
  `;
}

/* ---------- Initialization ---------- */
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme(STATE.theme);
  updatePolicyPreview();
  await Promise.all([loadPresets(), refreshAll(), loadSystemData()]);

  setInterval(() => {
    if (STATE.autoRefresh) {
      refreshAll();
    }
  }, 2000);
});
