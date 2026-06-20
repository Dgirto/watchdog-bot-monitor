import os, tempfile
os.environ["DB_PATH"]=tempfile.mktemp(suffix=".db"); os.environ["DB_BACKEND"]="sqlite"
import adapters.repositories.sqlite_repositories as r; r.DB_PATH=os.environ["DB_PATH"]
from fastapi.testclient import TestClient
from main import app

print("=== PROBE 1: metrics no-dict (ej. 123) por WS ===")
with TestClient(app) as c:
    try:
        with c.websocket_connect("/ws/agent?bot_id=probe-01&environment=prod") as ws:
            ws.send_json({"type":"health","seq":1,"metrics":123})
            try:
                resp = ws.receive_json()
                print("  respuesta:", resp)
                # ¿sigue viva la conexión?
                ws.send_json({"type":"heartbeat","seq":2})
                print("  tras metrics malos, heartbeat ->", ws.receive_json())
            except Exception as e:
                print("  CONEXIÓN ROTA tras metrics no-dict:", type(e).__name__, e)
    except Exception as e:
        print("  EXCEPCION:", type(e).__name__, e)

print("=== PROBE 2: Incident.resolve con recovered < offline (clock skew) ===")
from datetime import datetime, timezone, timedelta
from domain.entities.bot import Incident, BotEnvironment
off = datetime(2026,6,19,12,0,0,tzinfo=timezone.utc)
inc = Incident(incident_id="x", bot_id="b", environment=BotEnvironment.PROD, offline_at=off)
inc.resolve(off - timedelta(seconds=30))   # recuperado ANTES de caer
print("  downtime_seconds =", inc.downtime_seconds, "(¿negativo? BUG)")

print("=== PROBE 3: token injection via nombre con %%INCIDENTS%% ===")
import re
from adapters.controllers.api import _ID_RE, _NAME_RE
print("  name '%%INCIDENTS%%' valido?:", bool(_NAME_RE.match("%%INCIDENTS%%")))
print("  bot_id \"'; DROP TABLE bots;--\" valido?:", bool(_ID_RE.match("'; DROP TABLE bots;--")))

print("=== PROBE 4: AlertThrottler._state crece sin limite ===")
import asyncio
from notifications.throttler import AlertThrottler
from domain.entities.bot import Bot, BotStatus
from notifications.manager import StatusChangeEvent, NotificationChannel
class FakeRepo:
    async def find_by_id(self,b,e): return Bot(bot_id=b,name=b,environment=BotEnvironment.PROD,status=BotStatus.ONLINE)
class Spy(NotificationChannel):
    async def send(self,e): pass
async def leak():
    t=AlertThrottler([Spy()],FakeRepo(),confirm_seconds=0.01,cooldown_seconds=0.01)
    for i in range(1000):
        b=Bot(bot_id=f"b{i}",name="x",environment=BotEnvironment.PROD,status=BotStatus.OFFLINE)
        await t.send(StatusChangeEvent(bot=b,previous_status=BotStatus.ONLINE,new_status=BotStatus.OFFLINE,occurred_at=off))
    await asyncio.sleep(0.05)
    print("  _state size tras 1000 bots distintos:", len(t._state))
asyncio.run(leak())
