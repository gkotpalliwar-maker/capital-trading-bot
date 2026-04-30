#!/usr/bin/env python3
"""Capital.com Trading Bot v2.2 - Web Analytics Dashboard"""
import sys, os, sqlite3
from datetime import datetime, timezone
from flask import Flask, render_template_string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DASHBOARD_PORT, DASHBOARD_HOST

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bot.db")
app = Flask(__name__)

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9}
.header{background:#161b22;padding:16px 24px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:20px;color:#58a6ff}.header .ver{color:#8b949e;font-size:13px}
.container{max-width:1400px;margin:0 auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card h3{color:#58a6ff;font-size:14px;margin-bottom:12px;text-transform:uppercase;letter-spacing:1px}
.stat{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #21262d}
.stat:last-child{border-bottom:none}.stat .label{color:#8b949e}.stat .value{font-weight:600}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.blue{color:#58a6ff}
.chart-container{position:relative;height:300px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 6px;color:#8b949e;border-bottom:2px solid #30363d}
td{padding:6px;border-bottom:1px solid #21262d}tr:hover{background:#1c2128}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge-buy{background:#0d3b1e;color:#3fb950}.badge-sell{background:#3d1115;color:#f85149}
.badge-executed{background:#0d2d4a;color:#58a6ff}.badge-skipped,.badge-expired{background:#1c1c1c;color:#8b949e}
.badge-pending{background:#2a1c00;color:#d29922}
.wide{grid-column:1/-1}
</style></head><body>
<div class="header"><h1>Capital.com Trading Bot</h1><span class="ver">v2.2.0 | {{ now }}</span></div>
<div class="container">
<div class="grid">
<div class="card"><h3>Performance (30d)</h3>
<div class="stat"><span class="label">Trades</span><span class="value">{{ stats.total }} ({{ stats.wins }}W/{{ stats.losses }}L)</span></div>
<div class="stat"><span class="label">Win Rate</span><span class="value {% if stats.win_rate >= 50 %}green{% elif stats.win_rate >= 35 %}yellow{% else %}red{% endif %}">{{ "%.1f"|format(stats.win_rate) }}%</span></div>
<div class="stat"><span class="label">Total P&L</span><span class="value {% if stats.total_pnl >= 0 %}green{% else %}red{% endif %}">${{ "%+.2f"|format(stats.total_pnl) }}</span></div>
<div class="stat"><span class="label">Avg R</span><span class="value">{{ "%+.2f"|format(stats.avg_r) }}</span></div>
</div>
<div class="card"><h3>Risk Status</h3>
<div class="stat"><span class="label">Day P&L</span><span class="value {% if risk.daily_pnl >= 0 %}green{% else %}red{% endif %}">${{ "%+.2f"|format(risk.daily_pnl) }}</span></div>
<div class="stat"><span class="label">Open Trades</span><span class="value">{{ risk.open_trades }}/{{ risk.max_open }}</span></div>
<div class="stat"><span class="label">Consec Losses</span><span class="value">{{ risk.consec }}/{{ risk.cooldown }}</span></div>
</div>
</div>
<div class="grid">
<div class="card"><h3>P&L by Combo</h3><div class="chart-container"><canvas id="comboChart"></canvas></div></div>
<div class="card"><h3>P&L by Instrument</h3><div class="chart-container"><canvas id="instChart"></canvas></div></div>
<div class="card"><h3>P&L by Session</h3><div class="chart-container"><canvas id="sessChart"></canvas></div></div>
</div>
<div class="grid"><div class="card wide"><h3>Cumulative P&L</h3><div class="chart-container"><canvas id="eqChart"></canvas></div></div></div>
<div class="grid"><div class="card wide"><h3>Recent Signals (48h)</h3>
<div style="overflow-x:auto"><table>
<tr><th>Time</th><th>Instrument</th><th>Dir</th><th>TF</th><th>Zones</th><th>Conf</th><th>R:R</th><th>Regime</th><th>Status</th></tr>
{% for s in signals %}<tr>
<td>{{ s.timestamp[:16] }}</td><td>{{ s.epic }}</td>
<td><span class="badge badge-{{ s.direction|lower }}">{{ s.direction }}</span></td>
<td>{{ s.timeframe }}</td><td>{{ s.zone_types }}</td><td>{{ s.confluence }}</td>
<td>{{ "%.1f"|format(s.risk_reward) }}</td><td>{{ s.regime or '-' }}</td>
<td><span class="badge badge-{{ s.status }}">{{ s.status }}</span></td>
</tr>{% endfor %}</table></div></div></div>
<div class="grid"><div class="card wide"><h3>Trade Journal</h3>
<div style="overflow-x:auto"><table>
<tr><th>Time</th><th>Instr</th><th>Dir</th><th>Size</th><th>Entry</th><th>Close</th><th>P&L</th><th>R</th><th>Combo</th><th>Regime</th></tr>
{% for t in trades %}<tr>
<td>{{ t.timestamp[:16] }}</td><td>{{ t.epic }}</td>
<td><span class="badge badge-{{ t.direction|lower }}">{{ t.direction }}</span></td>
<td>{{ t.size }}</td><td>{{ t.entry_price }}</td><td>{{ t.close_price or '-' }}</td>
<td class="{% if (t.pnl or 0) > 0 %}green{% else %}red{% endif %}">${{ "%+.2f"|format(t.pnl or 0) }}</td>
<td>{{ "%+.2f"|format(t.pnl_r or 0) }}R</td><td>{{ t.zone_types or '-' }}</td><td>{{ t.regime or '-' }}</td>
</tr>{% endfor %}</table></div></div></div>
</div>
<script>
const cDef={responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#c9d1d9'}}},scales:{x:{ticks:{color:'#8b949e'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}};
function bar(id,labels,data){new Chart(document.getElementById(id),{type:'bar',data:{labels:labels,datasets:[{label:'P&L',data:data,backgroundColor:data.map(v=>v>=0?'#3fb950':'#f85149')}]},options:cDef})}
bar('comboChart',{{ combo_labels|tojson }},{{ combo_pnl|tojson }});
bar('instChart',{{ inst_labels|tojson }},{{ inst_pnl|tojson }});
bar('sessChart',{{ sess_labels|tojson }},{{ sess_pnl|tojson }});
new Chart(document.getElementById('eqChart'),{type:'line',data:{labels:{{ eq_labels|tojson }},datasets:[{label:'Cumulative P&L',data:{{ eq_vals|tojson }},borderColor:'#58a6ff',backgroundColor:'rgba(88,166,255,0.1)',fill:true,tension:0.3,pointRadius:2}]},options:cDef});
</script></body></html>"""

@app.route("/")
def index():
    conn = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = [dict(r) for r in conn.execute("SELECT * FROM trades WHERE status='closed' AND close_time > datetime('now','-30 days')").fetchall()]
    wins = [t for t in rows if (t.get("pnl") or 0) > 0]
    r_vals = [t.get("pnl_r",0) or 0 for t in rows]
    stats = {"total":len(rows),"wins":len(wins),"losses":len(rows)-len(wins),"win_rate":len(wins)/len(rows)*100 if rows else 0,"total_pnl":sum(t.get("pnl",0) or 0 for t in rows),"avg_r":sum(r_vals)/len(r_vals) if r_vals else 0}

    daily_pnl = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed' AND close_time > datetime('now','-24 hours')").fetchone()[0]
    open_t = [dict(r) for r in conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()]
    from config import MAX_DAILY_LOSS, MAX_OPEN_TRADES, COOLDOWN_AFTER_LOSSES
    risk = {"daily_pnl":daily_pnl,"open_trades":len(open_t),"max_open":MAX_OPEN_TRADES,"consec":0,"cooldown":COOLDOWN_AFTER_LOSSES}

    by_combo,by_inst,by_sess = {},{},{}
    for t in rows:
        c=t.get("zone_types") or "?"; by_combo[c]=by_combo.get(c,0)+(t.get("pnl",0) or 0)
        i=t.get("epic") or "?"; by_inst[i]=by_inst.get(i,0)+(t.get("pnl",0) or 0)
        s=t.get("session") or "?"; by_sess[s]=by_sess.get(s,0)+(t.get("pnl",0) or 0)

    eq_labels,eq_vals,cum=[],[],0
    for t in sorted(rows,key=lambda x:x.get("close_time","")):
        cum+=t.get("pnl",0) or 0; eq_labels.append((t.get("close_time") or "")[:10]); eq_vals.append(round(cum,2))

    signals = [dict(r) for r in conn.execute("SELECT * FROM signals WHERE timestamp > datetime('now','-48 hours') ORDER BY id DESC LIMIT 50").fetchall()]
    trades = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 30").fetchall()]
    conn.close()
    return render_template_string(TEMPLATE,now=now,stats=stats,risk=risk,
        combo_labels=list(by_combo.keys()),combo_pnl=[round(v,2) for v in by_combo.values()],
        inst_labels=list(by_inst.keys()),inst_pnl=[round(v,2) for v in by_inst.values()],
        sess_labels=list(by_sess.keys()),sess_pnl=[round(v,2) for v in by_sess.values()],
        eq_labels=eq_labels,eq_vals=eq_vals,signals=signals,trades=trades)

if __name__ == "__main__":
    print(f"Dashboard at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False)
