"""
adapters/controllers/dashboard.py
GET /dashboard — self-contained "Mission Control" HTML page.
GET /dashboard/estado — las mismas regiones en JSON, para actualizar sin recargar.

Design lives in _TEMPLATE (HTML + CSS + minimal vanilla JS). Dynamic regions are
marked with %%TOKENS%% and filled via str.replace() — this avoids the .format()
brace-doubling trap, so the CSS stays untouched. All dynamic values that come
from agents are passed through html.escape() (XSS protection, S-3).
"""
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from domain.entities.bot import BotStatus
from infrastructure.container import container
from infrastructure.time import utcnow

dashboard_router = APIRouter()

_FUENTES = Path(__file__).parent / "fuentes"
_FUENTES_PERMITIDAS = {"geist.woff2", "geist-mono.woff2"}

# ── Inline SVG icons (no external icon library) ───────────────────────────
ICON_CHECK = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
ICON_XCIRCLE = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>'
ICON_QMARK = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.5 1.5c0 1.7-2.5 2-2.5 3.5"/><path d="M12 17h.01"/></svg>'
ICON_ALERT = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
ICON_BOT = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4"/><rect x="5" y="6" width="14" height="12" rx="2"/><circle cx="9.5" cy="12" r="1"/><circle cx="14.5" cy="12" r="1"/></svg>'

_ESTADOS = {
    "offline": ("Caído", ICON_XCIRCLE),
    "degradado": ("Degradado", ICON_ALERT),
    "unknown": ("Sin datos", ICON_QMARK),
    "online": ("En línea", ICON_CHECK),
}
_PRIORIDAD = {"offline": 0, "degradado": 1, "unknown": 2, "online": 3}


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


def _hace(dt, now) -> str:
    if dt is None:
        return "nunca"
    segundos = (now - dt).total_seconds()
    return "ahora" if segundos < 3 else f"hace {_fmt_dur(segundos)}"


def _is_anomaly(m) -> bool:
    return (
        (m.llm_error_rate or 0) > 0.05
        or (m.inference_latency_p95_ms or 0) > 3000
        or (m.queue_depth or 0) > 200
    )


def _estado_visible(bot, m) -> str:
    """Estado que ve el operador: un agente en línea con métricas malas cuenta como degradado."""
    if bot.status == BotStatus.ONLINE and m is not None and _is_anomaly(m):
        return "degradado"
    return bot.status.value


# ── Fragment builders ─────────────────────────────────────────────────────
def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _verdict(bots, incidents, counts) -> str:
    offline = [b for b in bots if b.status == BotStatus.OFFLINE]
    active = [i for i in incidents if i.recovered_at is None]
    if offline or active:
        cls, ico = "crit", ICON_ALERT
        state = (_plural(len(active), "incidente activo", "incidentes activos") if active
                 else _plural(len(offline), "bot caído", "bots caídos"))
        if offline:
            b = offline[0]
            desc = f"<b>{escape(b.bot_id)}</b> ({escape(b.environment.value)}) no responde. Requiere atención inmediata."
        else:
            desc = "Hay incidentes activos que requieren atención."
    elif counts["degradado"] > 0:
        cls, ico = "warn", ICON_ALERT
        state = _plural(counts["degradado"], "agente degradado", "agentes degradados")
        desc = "Sigue en línea, pero sus métricas están fuera de rango."
    elif counts["unknown"] > 0:
        cls, ico = "warn", ICON_ALERT
        state = f"{counts['unknown']} en estado desconocido"
        desc = "A la espera del primer heartbeat de algunos agentes."
    else:
        cls, ico = "ok", ICON_CHECK
        state = "Todo operativo"
        desc = f"<b>{counts['online']}</b> de <b>{counts['total']}</b> agentes en línea."
    return (
        f'<div class="veredicto {cls}"><span class="veredicto-ico">{ico}</span><div>'
        f'<p class="veredicto-estado">{state}</p><p class="veredicto-desc">{desc}</p></div></div>'
    )


def _vitales(counts, activos) -> str:
    def vital(etiqueta, valor, tono=""):
        return f'<div class="vital {tono}"><dt>{etiqueta}</dt><dd>{valor}</dd></div>'

    return (
        vital("En línea", f'{counts["online"]}<small>/{counts["total"]}</small>')
        + vital("Caídos", counts["offline"], "crit" if counts["offline"] else "")
        + vital("Degradados", counts["degradado"], "warn" if counts["degradado"] else "")
        + vital("Incidentes activos", activos, "crit" if activos else "")
    )


def _fila_bot(bot, m, now) -> str:
    estado = _estado_visible(bot, m)
    etiqueta, ico = _ESTADOS[estado]
    env = bot.environment.value
    ia = '<span class="marca-ia">IA</span>' if m is not None else ""
    clave = escape(f"{bot.bot_id}/{env}", quote=True)
    return (
        f'<li class="bot s-{estado}" data-bot="{clave}" data-estado="{estado}">'
        f'<span class="estado e-{estado}">{ico}{etiqueta}</span>'
        f'<span class="bot-datos"><span class="bot-id">{escape(bot.bot_id)}</span>{ia}'
        f'<span class="bot-nombre">{escape(bot.name)}</span></span>'
        f'<span class="env">{escape(env)}</span>'
        f'<span class="visto">{_hace(bot.last_seen, now)}</span></li>'
    )


def _gauge(label, level, fill, val_html) -> str:
    fill = max(0.0, min(float(fill), 100.0))  # never emit an out-of-range width
    return (
        f'<div class="gauge {level}"><span class="g-lbl">{label}</span>'
        f'<span class="g-track"><span class="g-fill" style="width:{fill:.0f}%"></span></span>'
        f'<span class="g-val">{val_html}</span></div>'
    )


def _gauges(m) -> str:
    out = []
    v = m.inference_latency_p95_ms
    if v is None:
        out.append(_gauge("Latencia p95", "dead", 100, "— <small>ms</small>"))
    else:
        lvl = "ok" if v < 1500 else "warn" if v <= 3000 else "crit"
        out.append(_gauge("Latencia p95", lvl, min(v / 4000 * 100, 100), f"{int(v)}<small> ms</small>"))

    v = m.llm_error_rate
    if v is None:
        out.append(_gauge("Error LLM", "dead", 0, "—"))
    else:
        lvl = "ok" if v < 0.05 else "warn" if v <= 0.2 else "crit"
        out.append(_gauge("Error LLM", lvl, min(v * 200, 100), f"{v * 100:.1f}<small> %</small>"))

    v = m.queue_depth
    if v is None:
        out.append(_gauge("Cola", "dead", 0, "—"))
    else:
        lvl = "ok" if v < 50 else "warn" if v <= 200 else "crit"
        out.append(_gauge("Cola", lvl, min(v / 300 * 100, 100), str(int(v))))
    return "".join(out)


def _instrument(bot, m) -> str:
    if bot.status == BotStatus.OFFLINE:
        icls, chip = "instr down", f'<span class="estado e-offline">{ICON_XCIRCLE}CAÍDO</span>'
        note = "Sin heartbeat reciente: revisar el agente."
    elif _is_anomaly(m):
        icls, chip = "instr anomaly", f'<span class="estado e-degradado">{ICON_ALERT}DEGRADADO</span>'
        note = "Métricas fuera de rango: posible degradación."
    else:
        icls, chip = "instr", f'<span class="estado e-online">{ICON_CHECK}ÓPTIMO</span>'
        note = "Opera dentro de los parámetros normales."
    return (
        f'<article class="{icls}"><div class="instr-head"><div>'
        f'<p class="instr-nombre">{escape(bot.name)}</p>'
        f'<p class="instr-id">{escape(bot.bot_id)} · {escape(bot.environment.value)}</p></div>'
        f'{chip}</div><div class="gauges">{_gauges(m)}</div><p class="instr-nota">{note}</p></article>'
    )


def _incident_row(i, now) -> str:
    env = i.environment.value
    if i.recovered_at is None:
        estado = f'<span class="estado e-offline">{ICON_XCIRCLE}Activo</span>'
        dur = f"en curso · {_fmt_dur((now - i.offline_at).total_seconds())}"
        cls = "incidente abierto"
    else:
        estado = f'<span class="estado e-online">{ICON_CHECK}Recuperado</span>'
        dur = _fmt_dur(i.downtime_seconds or 0)
        cls = "incidente"
    return (
        f'<li class="{cls}"><div><span class="bot-id">{escape(i.bot_id)}</span>'
        f'<span class="env">{escape(env)}</span>'
        f'<p class="incidente-hora">{_fmt_dt(i.offline_at)} UTC</p></div>'
        f'<div class="incidente-fin">{estado}<span class="incidente-dur">{dur}</span></div></li>'
    )


_COCKPIT_EMPTY = (
    f'<div class="vacio"><span class="vacio-ico">{ICON_BOT}</span>'
    '<p class="t">Sin agentes de IA reportando</p>'
    '<p>Los agentes envían métricas vía WebSocket (mensaje "health").</p></div>'
)

_INCIDENTS_EMPTY = (
    f'<li class="vacio"><span class="vacio-ico">{ICON_CHECK}</span>'
    '<p class="t">Sin incidentes registrados</p>'
    '<p>Toda la flota mantuvo su disponibilidad.</p></li>'
)

_FLOTA_EMPTY = '<li class="vacio"><p class="t">Sin bots registrados</p><p>Esperando el primer heartbeat.</p></li>'


async def _regiones() -> dict:
    """Calcula cada región del panel: la usan la página y la actualización en vivo."""
    bots = await container.bot_repo.find_all()
    incidents = await container.incident_repo.find_all(limit=30)
    latest = await container.health_repo.find_latest_all()
    now = utcnow()

    metricas = {(m.bot_id, m.environment.value): m for m in latest}
    de = lambda b: metricas.get((b.bot_id, b.environment.value))  # noqa: E731

    counts = {"online": 0, "offline": 0, "unknown": 0, "degradado": 0, "total": len(bots)}
    for b in bots:
        counts[b.status.value] = counts.get(b.status.value, 0) + 1
        if _estado_visible(b, de(b)) == "degradado":
            counts["degradado"] += 1

    # La flota siempre muestra primero lo que necesita atención.
    orden = sorted(bots, key=lambda b: (_PRIORIDAD[_estado_visible(b, de(b))], b.environment.value, b.bot_id))
    flota = "".join(_fila_bot(b, de(b), now) for b in orden) or _FLOTA_EMPTY

    con_metricas = [b for b in orden if de(b) is not None]
    agentes = "".join(_instrument(b, de(b)) for b in con_metricas) or _COCKPIT_EMPTY

    abiertos = sum(1 for i in incidents if i.recovered_at is None)
    ordenados = sorted(incidents, key=lambda i: i.recovered_at is not None)
    return {
        "veredicto": _verdict(bots, incidents, counts),
        "vitales": _vitales(counts, abiertos),
        "flota": flota,
        "total-flota": _plural(counts["total"], "bot"),
        "agentes": agentes,
        "total-agentes": _plural(len(con_metricas), "agente"),
        "incidentes": "".join(_incident_row(i, now) for i in ordenados) or _INCIDENTS_EMPTY,
        "total-incidentes": _plural(len(incidents), "evento"),
        "titulo": (f"({counts['offline']}) " if counts["offline"] else "") + "Watchdog · Mission Control",
        "generado": now.strftime("%H:%M:%S"),
    }


@dashboard_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard() -> HTMLResponse:
    regiones = await _regiones()
    html = _TEMPLATE
    for clave, valor in regiones.items():
        html = html.replace(f"%%{clave.upper().replace('-', '_')}%%", valor)
    return HTMLResponse(content=html)


@dashboard_router.get("/dashboard/estado", tags=["Dashboard"])
async def dashboard_estado() -> JSONResponse:
    return JSONResponse(await _regiones(), headers={"Cache-Control": "no-store"})


@dashboard_router.get("/dashboard/fuentes/{archivo}", include_in_schema=False)
async def dashboard_fuente(archivo: str) -> FileResponse:
    if archivo not in _FUENTES_PERMITIDAS:
        raise HTTPException(status_code=404)
    return FileResponse(_FUENTES / archivo, media_type="font/woff2",
                        headers={"Cache-Control": "public, max-age=604800"})


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%%TITULO%%</title>
<noscript><meta http-equiv="refresh" content="30"></noscript>
<style>
  @font-face { font-family: "Geist"; src: url("/dashboard/fuentes/geist.woff2") format("woff2"); font-weight: 100 900; font-display: swap; }
  @font-face { font-family: "Geist Mono"; src: url("/dashboard/fuentes/geist-mono.woff2") format("woff2"); font-weight: 100 900; font-display: swap; }

  :root {
    color-scheme: dark;
    --fondo: #0a0a0a; --panel: #141414; --panel-2: #1c1c1c; --linea: #2a2a2a;
    --texto: #fff; --suave: #a8a8a8; --tenue: #8a8a8a;
    --ok: #c8f031; --crit: #ff7a6b; --crit-fondo: #2c1310; --warn: #ffb429; --warn-fondo: #2b2009;
    --sans: "Geist", system-ui, sans-serif; --mono: "Geist Mono", ui-monospace, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--fondo); color: var(--texto); font: 400 14px/1.45 var(--sans); -webkit-font-smoothing: antialiased; }
  h1, h2, p, ul, dl, dd { margin: 0; padding: 0; }
  ul { list-style: none; }
  svg { width: 15px; height: 15px; flex: none; }

  .pagina { max-width: 1400px; margin: 0 auto; padding: 0 22px 22px; }

  .barra { display: flex; align-items: center; justify-content: space-between; gap: 12px; height: 52px; }
  .marca { display: flex; align-items: center; gap: 10px; }
  .marca-logo { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 7px; background: var(--ok); color: #0a0a0a; }
  .marca h1 { font-size: 16px; font-weight: 650; letter-spacing: -.02em; }
  .marca span { color: var(--tenue); font: 12px var(--mono); }
  .vivo { display: flex; align-items: center; gap: 8px; color: var(--suave); font: 12px var(--mono); }
  .vivo i { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); animation: pulso 1.8s infinite; }
  .vivo.sin-conexion { color: var(--crit); }
  .vivo.sin-conexion i { background: var(--crit); animation: none; }
  @keyframes pulso { 50% { opacity: .3; } }

  /* Franja de estado: lo primero y más grande de la página. */
  .estado-general { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 24px; padding: 16px 20px; border: 1px solid var(--linea); border-radius: 14px; background: var(--panel); transition: background-color .3s, border-color .3s; }
  .estado-general:has(.veredicto.crit) { background: var(--crit-fondo); border-color: var(--crit); }
  .estado-general:has(.veredicto.warn) { background: var(--warn-fondo); border-color: #6e5114; }
  .veredicto { display: flex; align-items: center; gap: 14px; min-width: 0; }
  .veredicto-ico { display: grid; place-items: center; flex: none; width: 46px; height: 46px; border-radius: 50%; background: var(--ok); color: #0a0a0a; }
  .veredicto-ico svg { width: 22px; height: 22px; }
  .veredicto.crit .veredicto-ico { background: var(--crit); }
  .veredicto.warn .veredicto-ico { background: var(--warn); }
  .veredicto-estado { font-size: 28px; line-height: 1.15; font-weight: 650; letter-spacing: -.03em; }
  .veredicto-desc { color: var(--suave); }
  .veredicto-desc b { color: var(--texto); font-weight: 550; }
  .vitales { display: grid; grid-template-columns: repeat(4, auto); gap: 30px; }
  .vital dt { color: var(--suave); font: 12px var(--mono); white-space: nowrap; }
  .vital dd { font: 600 26px/1.15 var(--mono); }
  .vital small { color: var(--tenue); font-size: 15px; font-weight: 400; }
  .vital.crit dd { color: var(--crit); }
  .vital.warn dd { color: var(--warn); }

  .rejilla { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap: 14px; margin-top: 14px; align-items: start; }
  .panel { min-width: 0; padding: 14px 16px; border: 1px solid var(--linea); border-radius: 14px; background: var(--panel); }
  .panel-titulo { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
  .panel-titulo h2 { font-size: 15px; font-weight: 600; }
  .panel-titulo span { color: var(--tenue); font: 12px var(--mono); }

  .bot { display: grid; grid-template-columns: 116px minmax(0, 1fr) 76px 112px; align-items: center; gap: 12px; min-height: 46px; padding: 6px 8px; border-top: 1px solid #222; border-radius: 6px; }
  .bot:first-child { border-top: 0; }
  .bot.s-offline { background: var(--crit-fondo); border-color: transparent; }
  .bot.s-degradado { background: var(--warn-fondo); border-color: transparent; }
  .bot.destello { animation: destello 2.4s ease-out; }
  @keyframes destello { 0%, 30% { outline: 2px solid currentColor; outline-offset: -2px; color: var(--crit); } 100% { outline: 2px solid transparent; outline-offset: -2px; } }
  .bot.destello-ok { animation: destello-ok 2.4s ease-out; }
  @keyframes destello-ok { 0%, 30% { outline: 2px solid var(--ok); outline-offset: -2px; } 100% { outline: 2px solid transparent; outline-offset: -2px; } }
  .estado { display: inline-flex; align-items: center; gap: 6px; font-weight: 550; font-size: 13px; white-space: nowrap; }
  .e-online { color: var(--ok); } .e-offline { color: var(--crit); } .e-degradado { color: var(--warn); } .e-unknown { color: var(--suave); }
  .bot-datos { min-width: 0; line-height: 1.3; }
  .bot-id { font: 500 14px var(--mono); }
  .bot-nombre { display: block; overflow: hidden; color: var(--suave); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .marca-ia { margin-left: 7px; padding: 0 5px; border: 1px solid #444; border-radius: 4px; color: var(--suave); font: 11px var(--mono); vertical-align: 1px; }
  .env { color: var(--suave); font: 12px var(--mono); }
  .visto { color: var(--suave); font: 12px var(--mono); text-align: right; white-space: nowrap; }

  .incidentes { max-height: 300px; overflow-y: auto; }
  .incidente { display: flex; justify-content: space-between; gap: 12px; padding: 8px 2px; border-top: 1px solid #222; }
  .incidente:first-child { border-top: 0; }
  .incidente.abierto { margin: 0 -6px; padding-inline: 8px; border: 0; border-radius: 6px; background: var(--crit-fondo); }
  .incidente .env { margin-left: 7px; }
  .incidente-hora { color: var(--tenue); font: 12px var(--mono); }
  .incidente-fin { display: grid; justify-items: end; flex: none; }
  .incidente-dur { color: var(--suave); font: 12px var(--mono); }

  .agentes { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; margin-top: 8px; }
  .instr { padding: 12px 14px; border: 1px solid var(--linea); border-radius: 10px; background: var(--panel-2); }
  .instr.anomaly { border-color: #6e5114; }
  .instr.down { border-color: var(--crit); }
  .instr-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
  .instr-nombre { font-weight: 600; }
  .instr-id { color: var(--suave); font: 12px var(--mono); }
  .gauges { display: grid; gap: 7px; }
  .gauge { display: grid; grid-template-columns: 88px 1fr 72px; align-items: center; gap: 10px; }
  .g-lbl { color: var(--suave); font-size: 13px; }
  .g-track { height: 5px; border-radius: 5px; background: #303030; overflow: hidden; }
  .g-fill { display: block; height: 100%; border-radius: 5px; background: var(--ok); transition: width .4s; }
  .gauge.warn .g-fill { background: var(--warn); } .gauge.crit .g-fill { background: var(--crit); } .gauge.dead .g-fill { background: #444; }
  .g-val { font: 500 13px var(--mono); text-align: right; }
  .g-val small { color: var(--suave); font-size: 12px; }
  .instr-nota { margin-top: 8px; color: var(--suave); font-size: 13px; }

  .vacio { display: grid; justify-items: center; gap: 2px; grid-column: 1 / -1; padding: 22px 8px; color: var(--suave); text-align: center; }
  .vacio .t { color: var(--texto); font-weight: 550; }
  .vacio-ico svg { width: 22px; height: 22px; color: var(--ok); }

  .pie { display: flex; justify-content: space-between; gap: 12px; margin-top: 12px; color: var(--tenue); font: 12px var(--mono); }

  @media (max-width: 1040px) {
    .rejilla, .estado-general { grid-template-columns: minmax(0, 1fr); }
  }
  @media (max-width: 640px) {
    .pagina { padding-inline: 14px; }
    .vitales { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .veredicto-estado { font-size: 23px; }
    .bot { grid-template-columns: minmax(0, 1fr) auto; gap: 2px 10px; }
    .bot .env, .bot .visto { grid-row: 2; }
    .bot .bot-datos { grid-column: 1 / -1; grid-row: 3; }
    .agentes { grid-template-columns: minmax(0, 1fr); }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="pagina">
  <header class="barra">
    <div class="marca">
      <span class="marca-logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 4.4 3.2 8.3 8 9.5 4.8-1.2 8-5.1 8-9.5V5l-8-3Z"/><path d="m9 11.5 2 2 4-4.5"/></svg></span>
      <h1>Watchdog</h1>
      <span>Mission Control</span>
    </div>
    <p class="vivo" id="vivo" role="status"><i aria-hidden="true"></i><span id="vivo-texto">En vivo · <span id="generado">%%GENERADO%%</span> UTC</span></p>
  </header>

  <main>
    <section class="estado-general" aria-label="Estado de la flota">
      <div id="global-health" role="status" aria-live="polite" data-region="veredicto">%%VEREDICTO%%</div>
      <dl class="vitales" data-region="vitales">%%VITALES%%</dl>
    </section>

    <div class="rejilla">
      <section class="panel" aria-labelledby="t-flota">
        <div class="panel-titulo"><h2 id="t-flota">Flota</h2><span data-region="total-flota">%%TOTAL_FLOTA%%</span></div>
        <ul data-region="flota">%%FLOTA%%</ul>
      </section>
      <section class="panel" aria-labelledby="t-incidentes">
        <div class="panel-titulo"><h2 id="t-incidentes">Incidentes</h2><span data-region="total-incidentes">%%TOTAL_INCIDENTES%%</span></div>
        <ul class="incidentes" data-region="incidentes">%%INCIDENTES%%</ul>
      </section>
    </div>

    <section class="panel" id="ai-health" style="margin-top:14px" aria-labelledby="t-agentes">
      <div class="panel-titulo"><h2 id="t-agentes">Cockpit · Salud de agentes de IA</h2><span data-region="total-agentes">%%TOTAL_AGENTES%%</span></div>
      <div class="agentes" data-region="agentes">%%AGENTES%%</div>
    </section>
  </main>

  <footer class="pie"><span>Se actualiza solo cada 2 segundos</span><span>watchdog · mission control</span></footer>
</div>

<script>
  (function () {
    "use strict";
    var INTERVALO = 2000;
    var regiones = {};
    document.querySelectorAll("[data-region]").forEach(function (el) {
      regiones[el.dataset.region] = el;
    });
    var ultimo = {};
    var molde = document.createElement("div");
    var vivo = document.getElementById("vivo");
    var vivoTexto = document.getElementById("vivo-texto");

    function estados() {
      var mapa = {};
      document.querySelectorAll(".bot").forEach(function (b) { mapa[b.dataset.bot] = b.dataset.estado; });
      return mapa;
    }

    function resaltarCambios(antes) {
      document.querySelectorAll(".bot").forEach(function (b) {
        var previo = antes[b.dataset.bot];
        if (!previo || previo === b.dataset.estado) return;
        b.classList.add(b.dataset.estado === "online" ? "destello-ok" : "destello");
      });
    }

    function aplicar(datos) {
      var antes = estados();
      Object.keys(regiones).forEach(function (clave) {
        if (ultimo[clave] === datos[clave]) return;
        // Compara ya normalizado por el navegador: si nada cambió, no repite el aviso de estado.
        molde.innerHTML = datos[clave];
        if (regiones[clave].innerHTML !== molde.innerHTML) regiones[clave].innerHTML = datos[clave];
        ultimo[clave] = datos[clave];
      });
      document.title = datos.titulo;
      resaltarCambios(antes);
      vivo.classList.remove("sin-conexion");
      vivoTexto.textContent = "En vivo · " + datos.generado + " UTC";
    }

    function consultar() {
      if (document.hidden) { setTimeout(consultar, INTERVALO); return; }
      fetch("/dashboard/estado", { cache: "no-store" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(aplicar)
        .catch(function () {
          vivo.classList.add("sin-conexion");
          vivoTexto.textContent = "Sin conexión · reintentando";
        })
        .finally(function () { setTimeout(consultar, INTERVALO); });
    }

    setTimeout(consultar, INTERVALO);
  })();
</script>
</body>
</html>"""
