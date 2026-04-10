"""
adapters/controllers/dashboard.py
GET /dashboard — returns a self-contained HTML page.
No template engine needed; the HTML is inlined here.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from infrastructure.container import container
from datetime import datetime, timezone

dashboard_router = APIRouter()

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Watchdog — Bot Monitor</title>
<style>
  :root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e8f0;
    --muted: #64748b;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #f59e0b;
    --blue: #3b82f6;
    --font: 'Segoe UI', system-ui, sans-serif;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font); min-height: 100vh; }}

  header {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 1.25rem 2rem;
    display: flex; align-items: center; justify-content: space-between;
  }}
  header h1 {{ font-size: 1.25rem; font-weight: 600; letter-spacing: .05em; }}
  header span {{ font-size: .8rem; color: var(--muted); }}

  .stats {{
    display: flex; gap: 1rem; padding: 1.5rem 2rem; flex-wrap: wrap;
  }}
  .stat-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 1rem 1.5rem; flex: 1; min-width: 140px;
    text-align: center;
  }}
  .stat-card .value {{ font-size: 2rem; font-weight: 700; }}
  .stat-card .label {{ font-size: .75rem; color: var(--muted); margin-top: .25rem; text-transform: uppercase; letter-spacing: .08em; }}
  .c-green {{ color: var(--green); }}
  .c-red   {{ color: var(--red); }}
  .c-yellow{{ color: var(--yellow); }}
  .c-blue  {{ color: var(--blue); }}

  .section {{ padding: 0 2rem 2rem; }}
  .section h2 {{ font-size: .875rem; color: var(--muted); text-transform: uppercase; letter-spacing: .1em; margin-bottom: .75rem; }}

  table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
  th {{ background: var(--surface); color: var(--muted); font-weight: 500; text-align: left;
        padding: .75rem 1rem; border-bottom: 1px solid var(--border); font-size: .75rem;
        text-transform: uppercase; letter-spacing: .08em; }}
  td {{ padding: .75rem 1rem; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tr:hover td {{ background: rgba(255,255,255,.02); }}

  .badge {{
    display: inline-flex; align-items: center; gap: .4rem;
    padding: .25rem .75rem; border-radius: 999px; font-size: .75rem; font-weight: 600;
  }}
  .badge-online  {{ background: rgba(34,197,94,.15);  color: var(--green); }}
  .badge-offline {{ background: rgba(239,68,68,.15);  color: var(--red); }}
  .badge-unknown {{ background: rgba(100,116,139,.15);color: var(--muted); }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; }}
  .dot-online  {{ background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 2s infinite; }}
  .dot-offline {{ background: var(--red); }}
  .dot-unknown {{ background: var(--muted); }}

  .env-tag {{
    background: rgba(59,130,246,.15); color: var(--blue);
    padding: .15rem .5rem; border-radius: 4px; font-size: .7rem; font-weight: 600;
    text-transform: uppercase;
  }}
  .env-tag.staging {{ background: rgba(245,158,11,.15); color: var(--yellow); }}
  .env-tag.dev     {{ background: rgba(100,116,139,.15); color: var(--muted); }}

  .downtime {{ color: var(--red); font-weight: 600; }}
  .recovered {{ color: var(--green); }}
  .open-badge {{ background: rgba(239,68,68,.15); color: var(--red); padding: .15rem .5rem; border-radius: 4px; font-size: .7rem; }}

  @keyframes pulse {{
    0%,100% {{ opacity: 1; }} 50% {{ opacity: .4; }}
  }}
  .refresh-note {{ font-size: .75rem; color: var(--muted); padding: 0 2rem 1.5rem; }}
</style>
<script>setTimeout(() => location.reload(), 30000);</script>
</head>
<body>
<header>
  <h1>🐕 Watchdog — Bot Fleet Monitor</h1>
  <span>Generated: {generated_at} UTC &nbsp;|&nbsp; Auto-refresh: 30s</span>
</header>

<div class="stats">
  <div class="stat-card"><div class="value c-blue">{total}</div><div class="label">Total Bots</div></div>
  <div class="stat-card"><div class="value c-green">{online}</div><div class="label">Online</div></div>
  <div class="stat-card"><div class="value c-red">{offline}</div><div class="label">Offline</div></div>
  <div class="stat-card"><div class="value c-yellow">{unknown}</div><div class="label">Unknown</div></div>
</div>

<div class="section">
  <h2>Fleet Status</h2>
  <table>
    <thead>
      <tr>
        <th>Status</th>
        <th>Bot ID</th>
        <th>Name</th>
        <th>Environment</th>
        <th>Last Seen (UTC)</th>
        <th>Registered</th>
      </tr>
    </thead>
    <tbody>
      {bot_rows}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Incidents</h2>
  <table>
    <thead>
      <tr>
        <th>Bot ID</th>
        <th>Environment</th>
        <th>Went Offline</th>
        <th>Recovered</th>
        <th>Downtime</th>
      </tr>
    </thead>
    <tbody>
      {incident_rows}
    </tbody>
  </table>
</div>

<p class="refresh-note">⚡ Page auto-refreshes every 30 seconds. Data is real-time from the watchdog service.</p>
</body>
</html>"""


def _fmt_dt(dt) -> str:
    if dt is None:
        return '<span style="color:#64748b">—</span>'
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_downtime(seconds: float | None) -> str:
    if seconds is None:
        return '<span class="open-badge">OPEN</span>'
    if seconds < 60:
        return f'<span class="downtime">{seconds:.0f}s</span>'
    if seconds < 3600:
        return f'<span class="downtime">{seconds/60:.1f}m</span>'
    return f'<span class="downtime">{seconds/3600:.2f}h</span>'


@dashboard_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard() -> HTMLResponse:
    bots = await container.bot_repo.find_all()
    incidents = await container.incident_repo.find_all(limit=30)

    counts = {"online": 0, "offline": 0, "unknown": 0}
    for b in bots:
        counts[b.status.value] = counts.get(b.status.value, 0) + 1

    bot_rows = ""
    for b in sorted(bots, key=lambda x: (x.environment.value, x.bot_id)):
        s = b.status.value
        env = b.environment.value
        env_cls = "staging" if env == "staging" else ("dev" if env == "dev" else "")
        bot_rows += f"""
        <tr>
          <td><span class="badge badge-{s}"><span class="dot dot-{s}"></span>{s.upper()}</span></td>
          <td><code>{b.bot_id}</code></td>
          <td>{b.name}</td>
          <td><span class="env-tag {env_cls}">{env}</span></td>
          <td>{_fmt_dt(b.last_seen)}</td>
          <td>{_fmt_dt(b.registered_at)}</td>
        </tr>"""

    incident_rows = ""
    if not incidents:
        incident_rows = '<tr><td colspan="5" style="text-align:center;color:#64748b;padding:2rem">No incidents recorded yet 🎉</td></tr>'
    for i in incidents:
        env = i.environment.value
        env_cls = "staging" if env == "staging" else ("dev" if env == "dev" else "")
        recovered = f'<span class="recovered">{_fmt_dt(i.recovered_at)}</span>' if i.recovered_at else '<span class="open-badge">ACTIVE</span>'
        incident_rows += f"""
        <tr>
          <td><code>{i.bot_id}</code></td>
          <td><span class="env-tag {env_cls}">{env}</span></td>
          <td>{_fmt_dt(i.offline_at)}</td>
          <td>{recovered}</td>
          <td>{_fmt_downtime(i.downtime_seconds)}</td>
        </tr>"""

    html = _HTML_TEMPLATE.format(
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        total=len(bots),
        bot_rows=bot_rows,
        incident_rows=incident_rows,
        **counts,
    )
    return HTMLResponse(content=html)
