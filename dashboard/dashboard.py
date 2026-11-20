"""Flask dashboard server for arbitrage tracking."""
from flask import Flask, jsonify, render_template_string
from datetime import datetime

import arb_db

app = Flask(__name__)


def _fmt_ts(ts: float) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Arbitrage Dashboard</title>
<style>
  :root { --bg:#0b0c15; --card:#121320; --text:#e0e0e0; --muted:#8892a0;
          --green:#00d084; --red:#ff4757; --blue:#3498db; --yellow:#f1c40f; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         background:var(--bg); color:var(--text); }
  header { padding:20px 30px; border-bottom:1px solid #1a1d2d; display:flex;
           align-items:center; justify-content:space-between; }
  h1 { margin:0; font-size:1.4rem; }
  .live { display:flex; align-items:center; gap:8px; color:var(--green); font-size:.85rem; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--green); animation:pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px;
          padding:20px 30px; }
  .card { background:var(--card); border-radius:10px; padding:16px 18px; }
  .card h3 { margin:0 0 6px; font-size:.75rem; text-transform:uppercase; color:var(--muted); letter-spacing:.05em; }
  .card .big { font-size:1.6rem; font-weight:700; }
  .green { color:var(--green); } .red { color:var(--red); } .blue { color:var(--blue); }
  .section { padding:0 30px 20px; }
  .section h2 { font-size:1rem; margin:0 0 10px; color:var(--muted); text-transform:uppercase; }
  table { width:100%; border-collapse:collapse; font-size:.85rem; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid #1a1d2d; }
  th { color:var(--muted); font-weight:500; }
  tr:hover { background:#181b2b; }
  .status { padding:2px 8px; border-radius:4px; font-size:.75rem; font-weight:600; }
  .status.ok { background:rgba(0,208,132,.15); color:var(--green); }
  .status.fail { background:rgba(255,71,87,.15); color:var(--red); }
  .status.wait { background:rgba(241,196,15,.15); color:var(--yellow); }
  .chart-wrap { background:var(--card); border-radius:10px; padding:14px; }
  canvas { width:100% !important; height:260px !important; }
  .flex { display:flex; gap:16px; flex-wrap:wrap; }
  .flex > .chart-wrap { flex:1 1 420px; }
  @media(max-width:700px){ .grid{grid-template-columns:1fr} .flex > .chart-wrap{flex:1 1 100%;} }
</style>
</head>
<body>
<header>
  <h1>⚡ Arbitrage Ops Dashboard</h1>
  <div class="live"><span class="dot"></span>Live — refreshes every 30s</div>
</header>

<div class="grid" id="kpi"></div>

<div class="section">
  <h2>Cumulative Profit & Scatter</h2>
  <div class="flex">
    <div class="chart-wrap"><canvas id="cumChart"></canvas></div>
    <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
  </div>
</div>

<div class="section">
  <h2>Recent Spreads (last 50)</h2>
  <div class="chart-wrap">
    <table id="spreadsTable"><thead><tr>
      <th>Time</th><th>DEX1</th><th>DEX2</th><th>Pair</th><th>Spread %</th><th>Amount In</th><th>Gas</th><th>Net Profit</th>
    </tr></thead><tbody></tbody></table>
  </div>
</div>

<div class="section">
  <h2>Recent Opportunities (last 50)</h2>
  <div class="chart-wrap">
    <table id="oppTable"><thead><tr>
      <th>Found</th><th>Executed</th><th>Spread @ Discovery</th><th>Actual Profit</th><th>Slippage</th>
    </tr></thead><tbody></tbody></table>
  </div>
</div>

<script>
// Minimal canvas chart helpers (no external deps)
function drawLineChart(canvas, data, label, color) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height, pad = 36;
  if (!data.length) { ctx.fillStyle = '#8892a0'; ctx.font = '14px monospace'; ctx.fillText('No data', w/2-30, h/2); return; }
  const xs = data.map(d => d.timestamp);
  const ys = data.map(d => d.cumulative_profit);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const rx = (v) => pad + (v - minX) / (maxX - minX || 1) * (w - pad*2);
  const ry = (v) => h - pad - (v - minY) / (maxY - minY || 1) * (h - pad*2);
  ctx.clearRect(0,0,w,h);
  // grid
  ctx.strokeStyle = '#1a1d2d'; ctx.lineWidth = 1;
  for (let i=0;i<=4;i++){ const y = pad + (h-pad*2)*(i/4); ctx.beginPath(); ctx.moveTo(pad,y); ctx.lineTo(w-pad,y); ctx.stroke(); }
  // line
  ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
  data.forEach((d,i) => { const x=rx(d.timestamp), y=ry(d.cumulative_profit); i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.stroke();
  // labels
  ctx.fillStyle = '#8892a0'; ctx.font = '12px monospace';
  ctx.fillText(label, pad, 18);
  ctx.fillText(maxY.toFixed(4), 6, pad+12);
  ctx.fillText(minY.toFixed(4), 6, h-pad);
  ctx.fillText(new Date(minX*1000).toLocaleTimeString(), pad, h-10);
  ctx.fillText(new Date(maxX*1000).toLocaleTimeString(), w-pad-60, h-10);
}

function drawScatter(canvas, data) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height, pad = 36;
  if (!data.length) { ctx.fillStyle = '#8892a0'; ctx.font = '14px monospace'; ctx.fillText('No data', w/2-30, h/2); return; }
  const xs = data.map(d => d.gas_used);
  const ys = data.map(d => d.profit_eth);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const rx = (v) => pad + (v - minX) / (maxX - minX || 1) * (w - pad*2);
  const ry = (v) => h - pad - (v - minY) / (maxY - minY || 1) * (h - pad*2);
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = '#1a1d2d'; ctx.lineWidth = 1;
  for (let i=0;i<=4;i++){ const y = pad + (h-pad*2)*(i/4); ctx.beginPath(); ctx.moveTo(pad,y); ctx.lineTo(w-pad,y); ctx.stroke(); }
  data.forEach(d => {
    ctx.beginPath();
    ctx.fillStyle = d.status === 'success' ? '#00d084' : d.status === 'failed' ? '#ff4757' : '#f1c40f';
    ctx.arc(rx(d.gas_used), ry(d.profit_eth), 3, 0, Math.PI*2);
    ctx.fill();
  });
  ctx.fillStyle = '#8892a0'; ctx.font = '12px monospace';
  ctx.fillText('Gas Used vs Profit ETH', pad, 18);
  ctx.fillText(maxY.toFixed(4), 6, pad+12);
  ctx.fillText(minY.toFixed(4), 6, h-pad);
  ctx.fillText(Math.round(minX), pad, h-10);
  ctx.fillText(Math.round(maxX), w-pad-40, h-10);
}

function fmtTS(ts) {
  const d = new Date(ts*1000);
  return d.toISOString().slice(0,19).replace('T',' ');
}

function statusClass(s) {
  return s==='success'?'ok':s==='failed'?'fail':'wait';
}

async function load() {
  const [stats, opps, spreads] = await Promise.all([
    fetch('/api/stats').then(r=>r.json()),
    fetch('/api/opportunities').then(r=>r.json()),
    fetch('/api/spreads').then(r=>r.json())
  ]);

  // KPIs
  const kpi = document.getElementById('kpi');
  const e = stats.executions || {};
  const s = stats.spreads || {};
  const o = stats.opportunities || {};
  kpi.innerHTML = `
    <div class="card"><h3>Total Spreads Logged</h3><div class="big blue">${s.total_logged||0}</div></div>
    <div class="card"><h3>Success Rate</h3><div class="big ${e.success_rate_pct>=80?'green':'red'}">${(e.success_rate_pct||0).toFixed(1)}%</div></div>
    <div class="card"><h3>Total Profit</h3><div class="big green">${(e.total_profit_eth||0).toFixed(6)} ETH</div></div>
    <div class="card"><h3>Avg Spread</h3><div class="big blue">${(s.avg_spread_pct||0).toFixed(3)}%</div></div>
    <div class="card"><h3>Opportunities</h3><div class="big">${o.total_found||0} <span style="font-size:.9rem;color:var(--muted)">(${o.executed||0} exec)</span></div></div>
    <div class="card"><h3>Pending / Failed</h3><div class="big">${e.pending||0} <span style="color:var(--yellow)">/</span> ${e.failures||0}</div></div>
  `;

  // Spreads table
  const stb = document.querySelector('#spreadsTable tbody');
  stb.innerHTML = (spreads||[]).map(r => `<tr>
    <td>${fmtTS(r.timestamp)}</td>
    <td>${r.dex1}</td><td>${r.dex2}</td><td>${r.token_pair}</td>
    <td class="${r.spread_pct>0?'green':'red'}">${r.spread_pct.toFixed(4)}%</td>
    <td>${r.amount_in!=null?r.amount_in.toFixed(4):'-'}</td>
    <td>${r.gas_cost!=null?r.gas_cost.toFixed(6):'-'}</td>
    <td class="${(r.net_profit||0)>0?'green':'red'}">${r.net_profit!=null?r.net_profit.toFixed(6):'-'}</td>
  </tr>`).join('');

  // Opportunities table
  const ot = document.querySelector('#oppTable tbody');
  ot.innerHTML = (opps||[]).map(r => `<tr>
    <td>${fmtTS(r.found_at)}</td>
    <td>${r.executed_at?fmtTS(r.executed_at):'<span class="status wait">PENDING</span>'}</td>
    <td class="${r.spread_at_discovery>0?'green':'red'}">${r.spread_at_discovery.toFixed(4)}%</td>
    <td class="${(r.actual_profit||0)>0?'green':'red'}">${r.actual_profit!=null?r.actual_profit.toFixed(6):'-'}</td>
    <td>${r.slippage!=null?r.slippage.toFixed(4):'-'}</td>
  </tr>`).join('');

  // Charts
  drawLineChart(document.getElementById('cumChart'), stats.cumulative_profit || [], 'Cumulative Profit ETH', '#00d084');
  drawScatter(document.getElementById('scatterChart'), stats.execution_scatter || []);
}

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/stats")
def api_stats():
    stats = arb_db.get_stats()
    stats["cumulative_profit"] = arb_db.get_cumulative_profit()
    stats["execution_scatter"] = arb_db.get_execution_scatter()
    return jsonify(stats)


@app.route("/api/opportunities")
def api_opportunities():
    return jsonify(arb_db.get_recent_opportunities(50))


@app.route("/api/spreads")
def api_spreads():
    return jsonify(arb_db.get_recent_spreads(50))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
