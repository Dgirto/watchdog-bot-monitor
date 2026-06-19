"""
adapters/controllers/dashboard.py
GET /dashboard — self-contained "Mission Control" HTML page.

Design lives in _TEMPLATE (HTML + CSS + minimal vanilla JS). Dynamic regions are
marked with %%TOKENS%% and filled via str.replace() — this avoids the .format()
brace-doubling trap, so the CSS stays untouched. All dynamic values that come
from agents are passed through html.escape() (XSS protection, S-3).
"""
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from domain.entities.bot import BotStatus
from infrastructure.container import container
from infrastructure.time import utcnow

dashboard_router = APIRouter()

# ── Inline SVG icons (no external icon library) ───────────────────────────
ICON_CHECK = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
ICON_XCIRCLE = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>'
ICON_QMARK = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.5 1.5c0 1.7-2.5 2-2.5 3.5"/><path d="M12 17h.01"/></svg>'
ICON_ALERT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
ICON_CLOCK = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
ICON_BOT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4"/><rect x="5" y="6" width="14" height="12" rx="2"/><circle cx="9.5" cy="12" r="1"/><circle cx="14.5" cy="12" r="1"/></svg>'
ICON_IA_FLAG = '<span class="agent-flag" title="Agente de IA"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="6" width="14" height="12" rx="2"/><path d="M12 3v3"/></svg>IA</span>'


# ── Formatting helpers ────────────────────────────────────────────────────
def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "— sin datos —"


def _fmt_dur(seconds: float) -> str:
    s = int(seconds or 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


def _is_anomaly(m) -> bool:
    return (
        (m.llm_error_rate or 0) > 0.05
        or (m.session_cost_usd or 0) > 10
        or (m.inference_latency_p95_ms or 0) > 3000
        or (m.queue_depth or 0) > 200
    )


# ── Fragment builders ─────────────────────────────────────────────────────
def _verdict(bots, incidents, counts) -> str:
    offline = [b for b in bots if b.status == BotStatus.OFFLINE]
    active = [i for i in incidents if i.recovered_at is None]
    if offline or active:
        cls, ico = "crit", ICON_ALERT
        if active:
            n = len(active)
            state = f"{n} incidente{'s' if n != 1 else ''} activo{'s' if n != 1 else ''}"
        else:
            n = len(offline)
            state = f"{n} bot{'s' if n != 1 else ''} caído{'s' if n != 1 else ''}"
        if offline:
            b = offline[0]
            desc = f"<b>{escape(b.bot_id)}</b> ({escape(b.environment.value)}) sin responder. Requiere atención inmediata."
        else:
            desc = "Hay incidentes activos que requieren atención."
    elif counts["unknown"] > 0:
        cls, ico = "warn", ICON_ALERT
        state = f"{counts['unknown']} en estado desconocido"
        desc = "A la espera del primer heartbeat de algunos agentes."
    else:
        cls, ico = "ok", ICON_CHECK
        state = "Todo operativo"
        total = counts["online"] + counts["offline"] + counts["unknown"]
        desc = f"<b>{counts['online']}</b> de <b>{total}</b> agentes online."
    return (
        f'<div class="verdict {cls}" id="global-health" role="status" aria-live="polite">'
        f'<div class="verdict-top"><span class="verdict-ico" aria-hidden="true">{ico}</span>'
        f'<div><div class="verdict-kicker">Estado de la flota</div>'
        f'<div class="verdict-state">{state}</div></div></div>'
        f'<div class="verdict-desc">{desc}</div></div>'
    )


def _spark(status) -> str:
    if status == "online":
        spec = [(7, ""), (12, ""), (9, "live"), (15, "live"), (8, "live"), (13, "live"), (10, "live"), (14, "live"), (11, "live")]
    elif status == "offline":
        spec = [(8, ""), (13, ""), (7, ""), (15, ""), (9, ""), (11, ""), (3, "dead"), (3, "dead"), (3, "dead")]
    else:
        spec = [(6, ""), (9, ""), (5, ""), (7, "live"), (4, ""), (8, ""), (3, ""), (6, ""), (4, "")]
    bars = "".join(
        (f'<i class="{c}" style="height:{h}px"></i>' if c else f'<i style="height:{h}px"></i>')
        for h, c in spec
    )
    return f'<span class="heartbeat" aria-hidden="true">{bars}</span>'


def _tile(bot, is_ai) -> str:
    s, env = bot.status.value, bot.environment.value
    ico = {"online": ICON_CHECK, "offline": ICON_XCIRCLE, "unknown": ICON_QMARK}[s]
    chip = '<span class="agent-chip">IA</span>' if is_ai else ""
    return (
        f'<article class="tile s-{s}"><div class="tile-top">'
        f'<span class="tile-status"><span class="dot dot-{s}" aria-hidden="true"></span>{ico}{s.upper()}</span>'
        f'{chip}</div>'
        f'<div class="tile-id">{escape(bot.bot_id)}</div>'
        f'<div class="tile-name">{escape(bot.name)}</div>'
        f'<div class="tile-foot"><span class="env env-{env}">{escape(env)}</span>{_spark(s)}</div></article>'
    )


def _gauge(label, level, fill, val_html) -> str:
    return (
        f'<div class="gauge {level}"><span class="g-lbl">{label}</span>'
        f'<span class="g-track"><span class="g-fill" style="width:{fill:.0f}%"></span></span>'
        f'<span class="g-val">{val_html}</span></div>'
    )


def _gauges(m) -> str:
    out = []
    v = m.inference_latency_p95_ms
    if v is None:
        out.append(_gauge("p95 latencia", "dead", 100, "— <small>ms</small>"))
    else:
        lvl = "ok" if v < 1500 else "warn" if v <= 3000 else "crit"
        out.append(_gauge("p95 latencia", lvl, min(v / 4000 * 100, 100), f"{int(v)}<small>ms</small>"))

    v = m.tokens_per_sec
    if v is None:
        out.append(_gauge("tokens/seg", "dead", 0, "—"))
    else:
        out.append(_gauge("tokens/seg", "ok", min(v / 150 * 100, 100), f"{v:g}"))

    v = m.llm_error_rate
    if v is None:
        out.append(_gauge("error LLM", "dead", 0, "—"))
    else:
        lvl = "ok" if v < 0.05 else "warn" if v <= 0.2 else "crit"
        out.append(_gauge("error LLM", lvl, min(v * 200, 100), f"{v * 100:.1f}<small>%</small>"))

    v = m.queue_depth
    if v is None:
        out.append(_gauge("cola", "dead", 0, "—"))
    else:
        lvl = "ok" if v < 50 else "warn" if v <= 200 else "crit"
        out.append(_gauge("cola", lvl, min(v / 300 * 100, 100), str(int(v))))

    v = m.session_cost_usd
    if v is None:
        out.append(_gauge("costo sesión", "dead", 0, "<small>$</small>0.00"))
    else:
        lvl = "ok" if v < 10 else "warn" if v <= 50 else "crit"
        out.append(_gauge("costo sesión", lvl, min(v / 50 * 100, 100), f"<small>$</small>{v:.2f}"))
    return "".join(out)


def _instrument(bot, m) -> str:
    if bot.status == BotStatus.OFFLINE:
        icls, chip = "instr down", '<span class="state-chip down">● DOWN</span>'
        note = f'<div class="instr-note crit">{ICON_XCIRCLE} Sin heartbeat reciente · revisar el agente</div>'
    elif _is_anomaly(m):
        icls, chip = "instr anomaly", '<span class="state-chip degraded">▲ DEGRADADO</span>'
        note = f'<div class="instr-note warn">{ICON_ALERT} Métricas fuera de rango — posible degradación</div>'
    else:
        icls, chip = "instr", '<span class="state-chip ok">● ÓPTIMO</span>'
        note = f'<div class="instr-note">{ICON_CHECK} Operando dentro de parámetros normales</div>'
    return (
        f'<article class="{icls}"><div class="instr-head"><div class="instr-id">'
        f'<span class="mk" aria-hidden="true">{ICON_BOT}</span>'
        f'<div><div class="n">{escape(bot.name)}</div>'
        f'<div class="i">{escape(bot.bot_id)} · {escape(bot.environment.value)}</div></div></div>'
        f'{chip}</div><div class="gauges">{_gauges(m)}</div>{note}</article>'
    )


def _fleet_row(bot, is_ai) -> str:
    s, env = bot.status.value, bot.environment.value
    badge = {
        "online": f'<span class="badge badge-online">{ICON_CHECK}ONLINE</span>',
        "offline": f'<span class="badge badge-offline">{ICON_XCIRCLE}OFFLINE</span>',
        "unknown": f'<span class="badge badge-unknown">{ICON_QMARK}UNKNOWN</span>',
    }[s]
    flag = ICON_IA_FLAG if is_ai else ""
    rowcls = ' class="row-crit"' if s == "offline" else ""
    seen_cls = "ts" if bot.last_seen else "ts dim"
    return (
        f"<tr{rowcls}><td>{badge}</td>"
        f'<td><span class="bot-id">{escape(bot.bot_id)}</span>{flag}</td>'
        f'<td class="bot-name">{escape(bot.name)}</td>'
        f'<td><span class="env env-{env}">{escape(env)}</span></td>'
        f'<td><span class="{seen_cls}">{_fmt_dt(bot.last_seen)}</span></td>'
        f'<td><span class="ts dim">{_fmt_dt(bot.registered_at)}</span></td></tr>'
    )


def _incident_row(i, now) -> str:
    env = i.environment.value
    if i.recovered_at is None:
        rowcls = ' class="row-crit"'
        rec = f'<span class="pill pill-active">{ICON_CLOCK}ACTIVO</span>'
        dt = f'<span class="downtime downtime-open">en curso · {_fmt_dur((now - i.offline_at).total_seconds())}</span>'
    else:
        rowcls = ""
        rec = f'<span class="pill pill-recovered">{ICON_CHECK}{_fmt_dt(i.recovered_at)}</span>'
        dt = f'<span class="downtime downtime-done">{_fmt_dur(i.downtime_seconds or 0)}</span>'
    return (
        f"<tr{rowcls}><td><span class=\"bot-id\">{escape(i.bot_id)}</span></td>"
        f'<td><span class="env env-{env}">{escape(env)}</span></td>'
        f'<td><span class="ts">{_fmt_dt(i.offline_at)}</span></td>'
        f"<td>{rec}</td><td>{dt}</td></tr>"
    )


_COCKPIT_EMPTY = (
    '<div class="empty" style="grid-column:1/-1">'
    f'<div class="ico" aria-hidden="true">{ICON_BOT}</div>'
    '<div class="t">Sin agentes de IA reportando</div>'
    '<div class="s">Los agentes envían métricas vía WebSocket (mensaje "health").</div></div>'
)

_INCIDENTS_EMPTY = (
    '<tr><td colspan="5" style="padding:0"><div class="empty">'
    f'<div class="ico" aria-hidden="true">{ICON_CHECK}</div>'
    '<div class="t">Sin incidentes registrados 🎉</div>'
    '<div class="s">Toda la flota mantuvo disponibilidad en la ventana observada.</div></div></td></tr>'
)


@dashboard_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard() -> HTMLResponse:
    bots = await container.bot_repo.find_all()
    incidents = await container.incident_repo.find_all(limit=30)
    latest = await container.health_repo.find_latest_all()

    metrics_by_key = {(m.bot_id, m.environment.value): m for m in latest}
    ai_keys = set(metrics_by_key.keys())

    counts = {"online": 0, "offline": 0, "unknown": 0}
    for b in bots:
        counts[b.status.value] = counts.get(b.status.value, 0) + 1
    total = len(bots)
    now = utcnow()

    # Matrix — outages first for fastest glance
    prio = {"offline": 0, "unknown": 1, "online": 2}
    matrix_bots = sorted(bots, key=lambda b: (prio.get(b.status.value, 3), b.environment.value, b.bot_id))
    matrix = "".join(_tile(b, (b.bot_id, b.environment.value) in ai_keys) for b in matrix_bots) or (
        '<div class="empty" style="grid-column:1/-1"><div class="t">Sin bots registrados</div>'
        '<div class="s">Esperando el primer heartbeat.</div></div>'
    )

    # Cockpit — AI agents (those reporting health), worst state first
    instruments = []
    for b in bots:
        m = metrics_by_key.get((b.bot_id, b.environment.value))
        if m is None:
            continue
        rank = 0 if b.status == BotStatus.OFFLINE else (1 if _is_anomaly(m) else 2)
        instruments.append((rank, _instrument(b, m)))
    instruments.sort(key=lambda x: x[0])
    cockpit = "".join(h for _, h in instruments) or _COCKPIT_EMPTY
    ai_count = len(instruments)

    # Fleet table — stable order
    fleet_bots = sorted(bots, key=lambda b: (b.environment.value, b.bot_id))
    fleet_rows = "".join(_fleet_row(b, (b.bot_id, b.environment.value) in ai_keys) for b in fleet_bots)

    incident_rows = "".join(_incident_row(i, now) for i in incidents) if incidents else _INCIDENTS_EMPTY

    replacements = {
        "%%VERDICT%%": _verdict(bots, incidents, counts),
        "%%STAT_TOTAL%%": str(total),
        "%%STAT_ONLINE%%": str(counts["online"]),
        "%%STAT_OFFLINE%%": str(counts["offline"]),
        "%%STAT_UNKNOWN%%": str(counts["unknown"]),
        "%%OFFLINE_ALERT%%": "alert" if counts["offline"] > 0 else "",
        "%%GENERATED_AT%%": now.strftime("%Y-%m-%d %H:%M:%S"),
        "%%MATRIX%%": matrix,
        "%%AI_COUNT%%": f"{ai_count} agente" + ("s" if ai_count != 1 else ""),
        "%%COCKPIT%%": cockpit,
        "%%FLEET_COUNT%%": f"{total} bot" + ("s" if total != 1 else ""),
        "%%FLEET_ROWS%%": fleet_rows,
        "%%INCIDENT_COUNT%%": f"{len(incidents)} evento" + ("s" if len(incidents) != 1 else ""),
        "%%INCIDENTS%%": incident_rows,
    }
    html = _TEMPLATE
    for token, value in replacements.items():
        html = html.replace(token, value)
    return HTMLResponse(content=html)


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Watchdog — Sala de Control de Flota</title>
<style>
  :root {
    --void:    #06080d;
    --bg:      #0a0e15;
    --rail:    #0c111a;
    --panel:   #0f141e;
    --panel-2: #131a26;
    --raise:   #18202e;
    --line:    #1d2632;
    --line-2:  #283344;
    --hair:    rgba(140,160,190,.07);

    --txt:   #e8eef7;
    --txt-2: #92a0b6;
    --txt-3: #586577;
    --txt-4: #3a4555;

    --ok:   #34d27b;  --ok-d:  #1f8f54;  --ok-s:  rgba(52,210,123,.13);  --ok-l: rgba(52,210,123,.34);
    --crit: #ff5a52;  --crit-d:#c0322c;  --crit-s:rgba(255,90,82,.14);   --crit-l:rgba(255,90,82,.42);
    --warn: #ffb429;  --warn-d:#b97e10;  --warn-s:rgba(255,180,41,.13);  --warn-l:rgba(255,180,41,.36);
    --info: #58a6ff;  --info-s:rgba(88,166,255,.13);  --info-l:rgba(88,166,255,.32);
    --neut: #6f7d92;  --neut-s:rgba(111,125,146,.16);

    --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { font-size: 16px; }
  body {
    background: var(--bg);
    color: var(--txt);
    font-family: var(--sans);
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
    font-feature-settings: "tnum" 1;
  }
  :focus-visible { outline: 2px solid var(--info); outline-offset: 2px; border-radius: 3px; }
  .mono { font-family: var(--mono); }

  .app {
    display: grid;
    grid-template-columns: 272px 1fr;
    min-height: 100vh;
  }

  .rail {
    background: var(--rail);
    border-right: 1px solid var(--line-2);
    display: flex; flex-direction: column;
    position: sticky; top: 0; height: 100vh;
    overflow-y: auto;
  }
  .rail-brand {
    display: flex; align-items: center; gap: 12px;
    padding: 18px 20px; border-bottom: 1px solid var(--line);
  }
  .brand-mark {
    width: 40px; height: 40px; border-radius: 10px; flex: none;
    display: grid; place-items: center;
    background: linear-gradient(150deg, var(--raise), #0a0f17);
    border: 1px solid var(--line-2);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.05);
  }
  .brand-mark svg { width: 23px; height: 23px; }
  .brand-name { font-size: 1rem; font-weight: 800; letter-spacing: .18em; }
  .brand-tag { font-size: .64rem; color: var(--txt-3); letter-spacing: .1em; text-transform: uppercase; margin-top: 2px; font-family: var(--mono); }

  .verdict {
    margin: 16px; border-radius: 12px; padding: 18px 16px;
    border: 1px solid var(--line-2); background: var(--panel);
    position: relative; overflow: hidden;
  }
  .verdict::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; }
  .verdict.crit { background: linear-gradient(160deg, var(--crit-s), var(--panel) 60%); border-color: var(--crit-l); }
  .verdict.crit::before { background: var(--crit); box-shadow: 0 0 20px var(--crit); }
  .verdict.warn { background: linear-gradient(160deg, var(--warn-s), var(--panel) 60%); border-color: var(--warn-l); }
  .verdict.warn::before { background: var(--warn); box-shadow: 0 0 20px var(--warn); }
  .verdict.ok::before { background: var(--ok); box-shadow: 0 0 20px var(--ok); }
  .verdict-top { display: flex; align-items: center; gap: 9px; }
  .verdict-ico { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; flex: none; }
  .verdict.crit .verdict-ico { background: var(--crit-s); color: var(--crit); }
  .verdict.warn .verdict-ico { background: var(--warn-s); color: var(--warn); }
  .verdict.ok   .verdict-ico { background: var(--ok-s); color: var(--ok); }
  .verdict-ico svg { width: 19px; height: 19px; }
  .verdict-kicker { font-size: .6rem; letter-spacing: .16em; text-transform: uppercase; color: var(--txt-3); font-family: var(--mono); }
  .verdict-state { font-size: 1.04rem; font-weight: 800; letter-spacing: -.01em; line-height: 1.1; margin-top: 1px; }
  .verdict.crit .verdict-state { color: #ff8b85; }
  .verdict.warn .verdict-state { color: var(--warn); }
  .verdict.ok   .verdict-state { color: var(--ok); }
  .verdict-desc { font-size: .76rem; color: var(--txt-2); margin-top: 12px; }
  .verdict-desc b { color: var(--txt); font-family: var(--mono); }

  .vitals { padding: 0 16px; display: flex; flex-direction: column; gap: 8px; }
  .vital {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 14px; border-radius: 10px;
    border: 1px solid var(--line); background: var(--panel);
    position: relative;
  }
  .vital-ico { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none; }
  .vital-ico svg { width: 16px; height: 16px; }
  .vital[data-tone="total"]   .vital-ico { background: var(--neut-s); color: var(--txt-2); }
  .vital[data-tone="online"]  .vital-ico { background: var(--ok-s);   color: var(--ok); }
  .vital[data-tone="offline"] .vital-ico { background: var(--crit-s); color: var(--crit); }
  .vital[data-tone="unknown"] .vital-ico { background: var(--warn-s); color: var(--warn); }
  .vital-label { flex: 1; font-size: .72rem; text-transform: uppercase; letter-spacing: .1em; color: var(--txt-2); font-weight: 600; }
  .vital-num { font-family: var(--mono); font-size: 1.7rem; font-weight: 800; line-height: 1; letter-spacing: -.02em; }
  .vital[data-tone="total"]   .vital-num { color: var(--txt); }
  .vital[data-tone="online"]  .vital-num { color: var(--ok); }
  .vital[data-tone="offline"] .vital-num { color: var(--crit); }
  .vital[data-tone="unknown"] .vital-num { color: var(--warn); }
  .vital[data-tone="offline"].alert { border-color: var(--crit-l); animation: vitalpulse 2.6s infinite; }
  @keyframes vitalpulse { 0%,100% { box-shadow: 0 0 0 0 rgba(255,90,82,0); } 50% { box-shadow: 0 0 0 3px rgba(255,90,82,.09); } }

  .rail-foot { margin-top: auto; padding: 16px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
  .clock-lbl { font-size: .58rem; letter-spacing: .16em; text-transform: uppercase; color: var(--txt-3); font-family: var(--mono); }
  .clock-val { font-family: var(--mono); font-size: .82rem; color: var(--txt-2); }
  .clock-val b { color: var(--txt); }
  .live {
    display: inline-flex; align-items: center; gap: 8px; align-self: flex-start;
    font-family: var(--mono); font-size: .68rem; font-weight: 700; letter-spacing: .06em;
    color: var(--ok); padding: 6px 11px; border-radius: 999px;
    background: var(--ok-s); border: 1px solid var(--ok-l);
  }
  .beacon { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 0 rgba(52,210,123,.6); animation: beacon 2.4s infinite; }
  @keyframes beacon { 0% { box-shadow: 0 0 0 0 rgba(52,210,123,.5); } 70% { box-shadow: 0 0 0 7px rgba(52,210,123,0); } 100% { box-shadow: 0 0 0 0 rgba(52,210,123,0); } }

  .board {
    background:
      linear-gradient(var(--hair) 1px, transparent 1px),
      linear-gradient(90deg, var(--hair) 1px, transparent 1px),
      radial-gradient(900px 500px at 90% -8%, rgba(88,166,255,.05), transparent 60%),
      var(--bg);
    background-size: 38px 38px, 38px 38px, 100% 100%, 100% 100%;
    padding: 24px clamp(18px, 2.4vw, 36px) 40px;
    min-width: 0;
  }

  .sec { margin-top: 30px; }
  .sec:first-child { margin-top: 4px; }
  .sec-head { display: flex; align-items: center; gap: 13px; margin-bottom: 14px; }
  .sec-head h2 { font-size: .78rem; font-weight: 700; text-transform: uppercase; letter-spacing: .17em; color: var(--txt-2); display: flex; align-items: center; gap: 9px; white-space: nowrap; }
  .sec-head h2 svg { width: 15px; height: 15px; color: var(--txt-3); }
  .sec-head .rule { flex: 1; height: 1px; background: repeating-linear-gradient(90deg, var(--line-2) 0 6px, transparent 6px 12px); }
  .sec-head .tag { font-family: var(--mono); font-size: .68rem; color: var(--txt-3); border: 1px solid var(--line); padding: 3px 10px; border-radius: 6px; white-space: nowrap; }

  .matrix { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
  .tile {
    position: relative; border-radius: 12px; padding: 14px;
    background: var(--panel); border: 1px solid var(--line-2); overflow: hidden;
  }
  .tile::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px; }
  .tile.s-online::before  { background: var(--ok); }
  .tile.s-offline::before { background: var(--crit); box-shadow: 0 0 16px var(--crit); }
  .tile.s-unknown::before { background: var(--warn); }
  .tile.s-offline { border-color: var(--crit-l); background: linear-gradient(180deg, var(--crit-s), var(--panel) 70%); }
  .tile-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .tile-status { display: inline-flex; align-items: center; gap: 7px; font-size: .68rem; font-weight: 800; letter-spacing: .08em; }
  .tile.s-online  .tile-status { color: var(--ok); }
  .tile.s-offline .tile-status { color: #ff8b85; }
  .tile.s-unknown .tile-status { color: var(--warn); }
  .tile-status svg { width: 13px; height: 13px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .dot-online { background: var(--ok); animation: beacon 2.4s infinite; }
  .dot-offline { background: var(--crit); box-shadow: 0 0 7px var(--crit); }
  .dot-unknown { background: var(--warn); }
  .agent-chip { font-family: var(--mono); font-size: .58rem; font-weight: 700; letter-spacing: .06em; color: var(--info); background: var(--info-s); border: 1px solid var(--info-l); padding: 2px 6px; border-radius: 5px; }
  .tile-id { font-family: var(--mono); font-size: .92rem; font-weight: 700; margin-top: 11px; color: var(--txt); }
  .tile-name { font-size: .76rem; color: var(--txt-2); margin-top: 2px; }
  .tile-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 12px; }
  .env { font-family: var(--mono); font-size: .64rem; font-weight: 700; padding: 2px 7px; border-radius: 5px; border: 1px solid transparent; }
  .env::before { content: "›"; opacity: .5; margin-right: 3px; }
  .env-prod    { background: var(--info-s); color: var(--info); border-color: var(--info-l); }
  .env-staging { background: var(--warn-s); color: var(--warn); border-color: var(--warn-l); }
  .env-dev     { background: var(--neut-s); color: var(--txt-2); border-color: var(--line-2); }
  .heartbeat { display: flex; align-items: flex-end; gap: 2px; height: 18px; }
  .heartbeat i { width: 3px; border-radius: 1px; background: var(--txt-4); }
  .tile.s-online  .heartbeat i.live { background: var(--ok); }
  .tile.s-offline .heartbeat i.dead { background: var(--crit); }
  .tile.s-unknown .heartbeat i.live { background: var(--warn); }

  .panel { background: var(--panel); border: 1px solid var(--line-2); border-radius: 12px; overflow: hidden; }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; min-width: 720px; }
  caption { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  thead th { background: var(--panel-2); color: var(--txt-3); font-weight: 600; text-align: left; padding: 11px 16px; border-bottom: 1px solid var(--line-2); font-size: .65rem; text-transform: uppercase; letter-spacing: .1em; white-space: nowrap; }
  tbody td { padding: 12px 16px; border-bottom: 1px solid var(--line); vertical-align: middle; white-space: nowrap; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr { transition: background .12s; }
  tbody tr:hover td { background: rgba(140,160,190,.04); }
  tbody tr.row-crit td { background: rgba(255,90,82,.05); }
  tbody tr.row-crit:hover td { background: rgba(255,90,82,.08); }
  .bot-id { font-family: var(--mono); font-size: .8rem; color: var(--txt); background: var(--raise); border: 1px solid var(--line-2); padding: 3px 8px; border-radius: 6px; }
  .bot-name { font-weight: 500; }
  .ts { font-family: var(--mono); font-size: .78rem; color: var(--txt-2); }
  .ts.dim { color: var(--txt-3); }
  .agent-flag { display: inline-flex; align-items: center; gap: 4px; font-size: .62rem; font-weight: 700; letter-spacing: .05em; color: var(--info); margin-left: 7px; }
  .agent-flag svg { width: 11px; height: 11px; }

  .badge { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px 4px 9px; border-radius: 999px; font-size: .71rem; font-weight: 700; letter-spacing: .04em; border: 1px solid transparent; white-space: nowrap; }
  .badge svg { width: 13px; height: 13px; flex: none; }
  .badge-online  { background: var(--ok-s); color: var(--ok); border-color: var(--ok-l); }
  .badge-offline { background: var(--crit-s); color: #ff8b85; border-color: var(--crit-l); }
  .badge-unknown { background: var(--warn-s); color: var(--warn); border-color: var(--warn-l); }

  .pill { display: inline-flex; align-items: center; gap: 6px; font-family: var(--mono); font-size: .69rem; font-weight: 700; letter-spacing: .05em; padding: 3px 9px; border-radius: 6px; border: 1px solid transparent; }
  .pill svg { width: 11px; height: 11px; }
  .pill-active { background: var(--crit-s); color: #ff8b85; border-color: var(--crit-l); }
  .pill-recovered { background: var(--ok-s); color: var(--ok); border-color: var(--ok-l); }
  .downtime { font-family: var(--mono); font-weight: 700; }
  .downtime-open { color: var(--crit); }
  .downtime-done { color: var(--txt-2); }

  .empty { padding: 44px 24px; text-align: center; }
  .empty .ico { width: 46px; height: 46px; margin: 0 auto 12px; border-radius: 12px; display: grid; place-items: center; background: var(--ok-s); color: var(--ok); }
  .empty .ico svg { width: 24px; height: 24px; }
  .empty .t { font-size: .98rem; font-weight: 650; }
  .empty .s { font-size: .82rem; color: var(--txt-3); margin-top: 4px; }

  #ai-health { scroll-margin-top: 20px; }
  .cockpit { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }
  .instr { background: var(--panel); border: 1px solid var(--line-2); border-radius: 12px; padding: 16px 18px; position: relative; overflow: hidden; }
  .instr::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: var(--neut); }
  .instr.anomaly::before { background: var(--warn); }
  .instr.down::before { background: var(--crit); box-shadow: 0 0 16px var(--crit); }
  .instr-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 15px; }
  .instr-id { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .instr-id .mk { width: 32px; height: 32px; border-radius: 8px; flex: none; display: grid; place-items: center; background: var(--info-s); color: var(--info); }
  .instr-id .mk svg { width: 18px; height: 18px; }
  .instr-id .n { font-weight: 650; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .instr-id .i { font-family: var(--mono); font-size: .68rem; color: var(--txt-3); }
  .state-chip { font-family: var(--mono); font-size: .62rem; font-weight: 800; letter-spacing: .07em; padding: 4px 9px; border-radius: 6px; white-space: nowrap; }
  .state-chip.ok { background: var(--ok-s); color: var(--ok); }
  .state-chip.degraded { background: var(--warn-s); color: var(--warn); }
  .state-chip.down { background: var(--crit-s); color: #ff8b85; }

  .gauges { display: flex; flex-direction: column; gap: 11px; }
  .gauge { display: grid; grid-template-columns: 92px 1fr 74px; align-items: center; gap: 11px; }
  .gauge .g-lbl { font-size: .64rem; text-transform: uppercase; letter-spacing: .06em; color: var(--txt-3); }
  .gauge .g-track { height: 7px; border-radius: 4px; background: var(--raise); overflow: hidden; position: relative; }
  .gauge .g-fill { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--ok); }
  .gauge.warn .g-fill { background: var(--warn); }
  .gauge.crit .g-fill { background: var(--crit); }
  .gauge.dead .g-fill { background: var(--txt-4); }
  .gauge .g-val { font-family: var(--mono); font-size: .82rem; font-weight: 700; text-align: right; color: var(--txt); }
  .gauge .g-val small { color: var(--txt-3); font-weight: 600; font-size: .64rem; }
  .gauge.warn .g-val { color: var(--warn); }
  .gauge.crit .g-val { color: var(--crit); }
  .gauge.dead .g-val { color: var(--txt-3); }
  .instr-note { font-size: .72rem; color: var(--txt-3); margin-top: 13px; display: flex; align-items: center; gap: 7px; padding-top: 12px; border-top: 1px solid var(--line); }
  .instr-note.crit { color: #ff8b85; }
  .instr-note.warn { color: var(--warn); }
  .instr-note svg { width: 13px; height: 13px; flex: none; }
  .ai-toggle { display: inline-flex; align-items: center; gap: 7px; font-size: .68rem; font-family: var(--mono); color: var(--txt-3); border: 1px solid var(--line); background: var(--panel-2); padding: 4px 10px; border-radius: 7px; cursor: pointer; }
  .ai-toggle:hover { color: var(--txt-2); border-color: var(--line-2); }

  .board-foot { margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .foot-note { font-size: .76rem; color: var(--txt-3); display: flex; align-items: center; gap: 8px; }
  .foot-note svg { width: 14px; height: 14px; }
  .foot-meta { font-family: var(--mono); font-size: .7rem; color: var(--txt-4); }

  @media (max-width: 960px) {
    .app { grid-template-columns: 1fr; }
    .rail { position: static; height: auto; flex-direction: column; }
    .rail-brand { border-bottom: 1px solid var(--line); }
    .verdict { margin: 14px 16px; }
    .vitals { flex-direction: row; flex-wrap: wrap; }
    .vital { flex: 1; min-width: 150px; }
    .rail-foot { margin-top: 0; flex-direction: row; align-items: center; justify-content: space-between; }
  }
  @media (max-width: 540px) {
    .vital { min-width: 130px; }
    .matrix { grid-template-columns: 1fr; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; }
  }
</style>
</head>
<body>
<div class="app">

  <aside class="rail">
    <div class="rail-brand">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="#92a0b6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2 4 5v6c0 4.4 3.2 8.3 8 9.5 4.8-1.2 8-5.1 8-9.5V5l-8-3Z"/><path d="M9 11.5l2 2 4-4.5"/>
        </svg>
      </div>
      <div>
        <div class="brand-name">WATCHDOG</div>
        <div class="brand-tag">Mission Control</div>
      </div>
    </div>

    %%VERDICT%%

    <div class="vitals" aria-label="Resumen de la flota">
      <div class="vital" data-tone="total">
        <span class="vital-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg></span>
        <span class="vital-label">Total</span>
        <span class="vital-num" id="stat-total">%%STAT_TOTAL%%</span>
      </div>
      <div class="vital" data-tone="online">
        <span class="vital-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></span>
        <span class="vital-label">Online</span>
        <span class="vital-num" id="stat-online">%%STAT_ONLINE%%</span>
      </div>
      <div class="vital %%OFFLINE_ALERT%%" data-tone="offline">
        <span class="vital-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg></span>
        <span class="vital-label">Offline</span>
        <span class="vital-num" id="stat-offline">%%STAT_OFFLINE%%</span>
      </div>
      <div class="vital" data-tone="unknown">
        <span class="vital-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.5 1.5c0 1.7-2.5 2-2.5 3.5"/><path d="M12 17h.01"/></svg></span>
        <span class="vital-label">Unknown</span>
        <span class="vital-num" id="stat-unknown">%%STAT_UNKNOWN%%</span>
      </div>
    </div>

    <div class="rail-foot">
      <div>
        <div class="clock-lbl">Generado · UTC</div>
        <div class="clock-val"><b id="generated-timestamp">%%GENERATED_AT%%</b></div>
      </div>
      <span class="live" title="Actualización automática cada 30 segundos">
        <span class="beacon" aria-hidden="true"></span> LIVE · 30s
      </span>
    </div>
  </aside>

  <main class="board">

    <section class="sec" aria-label="Flota — vista de mosaico">
      <div class="sec-head">
        <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg> Matriz de la Flota</h2>
        <div class="rule"></div>
        <span class="tag">vistazo rápido</span>
      </div>
      <div class="matrix">%%MATRIX%%</div>
    </section>

    <section class="sec" id="ai-health" aria-label="Salud de agentes de IA">
      <div class="sec-head">
        <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="2"/><path d="M12 7V4"/><circle cx="9" cy="13" r="1"/><circle cx="15" cy="13" r="1"/><path d="M9 17h6"/></svg> Cockpit · Salud de Agentes IA</h2>
        <div class="rule"></div>
        <span class="tag">%%AI_COUNT%%</span>
        <button class="ai-toggle" id="ai-toggle" type="button" aria-expanded="true" aria-controls="cockpit">ocultar</button>
      </div>
      <div class="cockpit" id="cockpit">%%COCKPIT%%</div>
    </section>

    <section class="sec" aria-label="Estado de la flota (detalle)">
      <div class="sec-head">
        <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/></svg> Estado de la Flota</h2>
        <div class="rule"></div>
        <span class="tag">%%FLEET_COUNT%%</span>
      </div>
      <div class="panel">
        <div class="table-scroll">
          <table>
            <caption>Estado de la flota: una fila por bot</caption>
            <thead>
              <tr>
                <th scope="col">Estado</th><th scope="col">Bot ID</th><th scope="col">Nombre</th>
                <th scope="col">Entorno</th><th scope="col">Última vez visto (UTC)</th><th scope="col">Fecha de registro</th>
              </tr>
            </thead>
            <tbody id="fleet-tbody">%%FLEET_ROWS%%</tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="sec" aria-label="Incidentes recientes">
      <div class="sec-head">
        <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg> Incidentes Recientes</h2>
        <div class="rule"></div>
        <span class="tag">%%INCIDENT_COUNT%%</span>
      </div>
      <div class="panel">
        <div class="table-scroll">
          <table>
            <caption>Incidentes recientes: una fila por caída</caption>
            <thead>
              <tr>
                <th scope="col">Bot ID</th><th scope="col">Entorno</th><th scope="col">Cuándo cayó (UTC)</th>
                <th scope="col">Recuperado (UTC)</th><th scope="col">Downtime</th>
              </tr>
            </thead>
            <tbody id="incidents-tbody">%%INCIDENTS%%</tbody>
          </table>
        </div>
      </div>
    </section>

    <footer class="board-foot">
      <div class="foot-note">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.2-8.5"/><path d="M21 3v6h-6"/></svg>
        <span id="refresh-note">La página se actualiza automáticamente cada <b>30&nbsp;segundos</b>. Datos en tiempo real del servicio Watchdog.</span>
      </div>
      <div class="foot-meta">watchdog · mission control</div>
    </footer>

  </main>
</div>

<script>
  (function () {
    "use strict";
    setTimeout(function () { location.reload(); }, 30000);

    var btn = document.getElementById("ai-toggle");
    var grid = document.getElementById("cockpit");
    if (btn && grid) {
      btn.addEventListener("click", function () {
        var hidden = grid.style.display === "none";
        grid.style.display = hidden ? "" : "none";
        btn.setAttribute("aria-expanded", String(hidden));
        btn.textContent = hidden ? "ocultar" : "mostrar";
      });
    }
  })();
</script>
</body>
</html>"""
