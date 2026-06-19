# 🐕 Watchdog — Monitor de Disponibilidad de Bots

Monitor ligero de uptime para bots en producción. Cero métricas de hardware.
Seguimiento puro de disponibilidad mediante señales de *heartbeat*.

---

## Esquema de Base de Datos

```sql
-- Bots registrados y su estado actual
CREATE TABLE bots (
    bot_id          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    environment     TEXT    NOT NULL,          -- prod | staging | dev
    status          TEXT    NOT NULL DEFAULT 'unknown',   -- online | offline | unknown
    last_seen       TEXT,                      -- timestamp ISO-8601 UTC
    registered_at   TEXT    NOT NULL,
    PRIMARY KEY (bot_id, environment)          -- un mismo bot puede correr en varios entornos
);

-- Una fila por caída. Se cierra automáticamente con el siguiente heartbeat.
CREATE TABLE incidents (
    incident_id      TEXT PRIMARY KEY,
    bot_id           TEXT NOT NULL,
    environment      TEXT NOT NULL,
    offline_at       TEXT NOT NULL,            -- cuándo el watchdog detectó la caída
    recovered_at     TEXT,                     -- NULL mientras el incidente está activo
    downtime_seconds REAL,                     -- se calcula solo al recuperarse
    FOREIGN KEY (bot_id, environment) REFERENCES bots(bot_id, environment)
);
```

---

## Inicio Rápido

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. (Opcional) configurar mediante variables de entorno
export HEARTBEAT_TIMEOUT=60    # segundos sin heartbeat → offline
export GRACE_PERIOD=15         # margen extra para absorber jitter de red
export WATCHDOG_INTERVAL=30    # frecuencia del barrido (sweep)

# ── Autenticación de heartbeats (HMAC) ───────────────────────────
export HEARTBEAT_AUTH_MODE=warn          # off | warn | enforce
export AGENT_SHARED_SECRET=cambiame       # o por agente: AGENT_SECRETS="bot1:s1,bot2:s2"

# ── Alertas: email primario, Slack secundario ────────────────────
export SENDGRID_API_KEY=SG.xxxxx
export ALERT_EMAIL_SENDER=watchdog@tuempresa.com
export ALERT_EMAIL_RECIPIENTS=guardia@tuempresa.com,sre@tuempresa.com
export ALERT_WEBHOOK_URL=https://hooks.slack.com/...   # opcional

# ── Umbrales inteligentes (anti-glitch / anti-flapping) ──────────
export ALERT_CONFIRM_SECONDS=90     # debe seguir offline este tiempo antes de alertar
export ALERT_COOLDOWN_SECONDS=300   # ventana de silencio tras una alerta

# 3. Ejecutar
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Autenticación de Heartbeats (despliegue sin downtime)

Los heartbeats se firman con HMAC-SHA256 para evitar la suplantación (*spoofing*).
El cliente envía las cabeceras `X-Timestamp` y `X-Signature` (ver
`bot_client_example.py`, pasando `secret=...`). Despliégalo sin romper a los
agentes existentes:

1. **`warn`** (por defecto) — el servidor acepta heartbeats firmados *y* sin firmar,
   registrando una advertencia para los no firmados. Actualiza los agentes a tu ritmo.
2. **`enforce`** — cuando todos los agentes firmen, cambia a `HEARTBEAT_AUTH_MODE=enforce`.
   Los heartbeats sin firma / con firma inválida / caducados (replay) se rechazan con `401`.

## API

| Método | Ruta                       | Descripción                                       |
|--------|----------------------------|---------------------------------------------------|
| WS     | /ws/agent                  | Conexión de agente en tiempo real (heartbeat + salud) |
| POST   | /heartbeat                 | Fallback HTTP — el bot reporta que está vivo       |
| GET    | /status                    | Estado completo de la flota en JSON               |
| GET    | /agents/{bot_id}/health    | Métricas de salud de IA recientes de un agente    |
| GET    | /dashboard                 | Panel web con indicadores visuales                |
| GET    | /health                    | Chequeo de vida del servicio                      |
| GET    | /docs                      | Documentación interactiva de la API (Swagger UI)  |

## Monitoreo en tiempo real con WebSocket

Los agentes se conectan a `/ws/agent` para un monitoreo de baja latencia; los
agentes que no hablan WS siguen usando `POST /heartbeat` (ambos caminos usan la
misma lógica de disponibilidad, así que hay una única fuente de verdad). El
cliente se reconecta automáticamente con backoff exponencial + jitter.

La autenticación del handshake usa el mismo HMAC que HTTP, vía query params (los
navegadores no pueden fijar cabeceras WS): `/ws/agent?bot_id=..&environment=..&ts=..&sig=..`

```jsonc
// agente → servidor
{ "type": "heartbeat", "seq": 1 }
{ "type": "health", "seq": 2, "metrics": { "inference_latency_p95_ms": 820, "llm_error_rate": 0.01 } }
// servidor → agente
{ "type": "ack", "seq": 2 }
```

### Métricas de salud de agente IA

Lo que diferencia a un *agente de IA* de un bot genérico: puede estar **online pero
degradado**. Se reportan vía el mensaje `health` y se almacenan por cada reporte:

| Métrica | Significado |
|---------|-------------|
| `inference_latency_p95_ms` | Capacidad de respuesta del LLM |
| `llm_error_rate`           | 0..1 — vivo pero fallando |
| `queue_depth`              | Saturación / backpressure |

Las anomalías (p. ej. tasa de error > 5%, picos de costo) se marcan en los logs.

## Estructura del Proyecto

```
watchdog/
├── domain/
│   ├── entities/bot.py          # Bot, Incident — Python puro, sin dependencias
│   ├── entities/health.py       # HealthMetrics — métricas de agente IA
│   └── interfaces/repositories.py  # Puertos abstractos (bot, incident, health)
├── use_cases/
│   ├── watchdog.py              # ProcessHeartbeat, RunWatchdog
│   └── health.py               # RecordHealth + detección de anomalías
├── adapters/
│   ├── repositories/
│   │   ├── sqlite_repositories.py    # Backend SQLite
│   │   └── postgres_repositories.py  # Backend PostgreSQL (asyncpg)
│   └── controllers/
│       ├── api.py               # POST /heartbeat, GET /status, GET /agents/{id}/health
│       ├── auth.py              # Verificación HMAC (HTTP + WS)
│       ├── ws.py                # WS /ws/agent — transporte en tiempo real
│       └── dashboard.py         # GET /dashboard
├── notifications/
│   ├── manager.py               # NotificationManager + canales (Log/Email/Webhook)
│   └── throttler.py             # Umbrales inteligentes (debounce + cooldown)
├── infrastructure/
│   ├── config.py                # Configuración desde variables de entorno
│   ├── secrets.py               # Secretos HMAC por agente / compartidos
│   ├── time.py                  # Helpers UTC con zona horaria
│   ├── ws_manager.py            # Conexiones WS vivas, rooms por agente
│   └── container.py             # Cableado de inyección de dependencias
├── bot_client_example.py        # Clientes de heartbeat HTTP + WebSocket
├── main.py                      # App FastAPI + tarea en segundo plano del watchdog
└── requirements.txt
```

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

29 tests que cubren seguridad (HMAC, validación), escalabilidad (eliminación del
N+1), alertas (throttler), WebSocket y métricas de IA. No requieren servicios
externos (usan SQLite + el TestClient ASGI).

## Añadir un Canal de Notificación

```python
# notifications/manager.py
class MiCanalPersonalizado(NotificationChannel):
    async def send(self, event: StatusChangeEvent) -> None:
        # tu lógica: PagerDuty, Telegram, SMS, etc.
        ...

# infrastructure/container.py — añadir a la lista de canales
channels.append(MiCanalPersonalizado(...))
```

## Backend de Base de Datos (SQLite o PostgreSQL)

Se elige al arrancar con `DB_BACKEND`; ambos implementan los mismos puertos, así
que no cambia la lógica de negocio.

```bash
# Por defecto — sin configuración
export DB_BACKEND=sqlite
export DB_PATH=watchdog.db

# Producción — escrituras concurrentes, pool de conexiones, listo para HA
export DB_BACKEND=postgres
export DATABASE_URL=postgresql://user:pass@localhost:5432/watchdog
pip install asyncpg   # solo necesario para el backend de postgres
```

`PostgresBotRepository`/`PostgresIncidentRepository` usan un pool de `asyncpg` y
columnas `TIMESTAMPTZ`. El esquema se crea automáticamente al arrancar.

Para añadir otro backend (MySQL, etc.), implementa `IBotRepository` e
`IIncidentRepository` y cablea en `infrastructure/container.py`.
