"""
API HTTP interna del contenedor "daemon" — el ÚNICO puente entre el servicio del agente
(odontotec) y el navegador persistente de Dentidesk. NO es la API de Dentidesk (esa solo lee
agenda, ver integrations/dentidesk.py) — es propia de este proyecto.

POR QUÉ una API en vez de exponer CDP directo: el protocolo CDP de Chrome no tiene autenticación
— cualquiera que lo alcance controla el navegador logueado por completo (cookies, formularios,
todo). Exponer el puerto 9222 entre contenedores sería un agujero de seguridad real aunque sea
"solo red interna". Por eso el CDP se queda SIEMPRE en 127.0.0.1 dentro de este mismo contenedor
(ver scripts/dentidesk_daemon.py), y lo único que cruza la red es esta API, protegida por
DENTIDESK_DAEMON_SECRET (mismo patrón que WEBHOOK_SECRET en main.py).

Uso (dentro del contenedor "daemon", después de que el navegador ya está logueado):
    uvicorn scripts.dentidesk_daemon_api:app --host 0.0.0.0 --port 8100

El lado del agente (integrations/dentidesk_playwright.py) le habla a esto cuando
DENTIDESK_DAEMON_URL está seteado — ver ese módulo para el cliente HTTP.
"""
import hmac
import logging
import os
import threading
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dentidesk-daemon-api")

# Fuerza el modo CDP local ANTES de importar integrations.dentidesk_playwright: ese módulo lee
# DENTIDESK_CDP_URL/DENTIDESK_DAEMON_URL como constantes de módulo al importarse, así que el
# orden importa. DENTIDESK_DAEMON_URL debe quedar VACÍO aquí (si no, este mismo proceso intentaría
# llamarse a sí mismo por HTTP en vez de usar Playwright/CDP de verdad).
os.environ.setdefault("DENTIDESK_CDP_URL", "http://127.0.0.1:9222")
os.environ["DENTIDESK_DAEMON_URL"] = ""

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from integrations import dentidesk_playwright as dp

app = FastAPI(title="dentidesk-daemon-api")

DAEMON_SECRET = os.getenv("DENTIDESK_DAEMON_SECRET", "")
if not DAEMON_SECRET:
    print("ADVERTENCIA: DENTIDESK_DAEMON_SECRET no configurado — la API acepta cualquier origen.")


def require_auth(x_daemon_secret: str = Header(default="")) -> None:
    if not DAEMON_SECRET:
        return  # sin secreto configurado: acepta todo (solo para pruebas locales, ver advertencia arriba)
    if not hmac.compare_digest(x_daemon_secret, DAEMON_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")


class CrearCitaBody(BaseModel):
    cedula: str
    patient_name: str
    phone: str
    doctor_label: str
    fecha_iso: str
    time: str
    procedimiento: str = ""
    sucursal: str = "214"


class MoverCitaBody(BaseModel):
    id_agenda: str
    fecha_actual_iso: str
    patient_name: str
    nueva_fecha_iso: str
    nueva_hora: str
    sucursal: str = "214"
    doctor_label: str = ""
    nuevo_doctor_label: str = ""
    nuevo_procedimiento: str = ""


# Todas las escrituras comparten UN solo navegador/página real (ver dentidesk_daemon.py). Sin
# este lock, dos citas pedidas casi al mismo tiempo (dos pacientes distintos por WhatsApp)
# abrirían dos modales sobre la MISMA página a la vez y se pisarían entre sí. Serializa: la
# segunda espera a que la primera termine, en vez de correr en paralelo.
_write_lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/crear_cita", dependencies=[Depends(require_auth)])
def crear_cita(body: CrearCitaBody):
    with _write_lock:
        try:
            return dp.create_appointment(**body.model_dump())
        except Exception as e:
            # Traceback COMPLETO al log del daemon: sin esto, el 502 no dice POR QUÉ falló
            # create_appointment (el detalle solo va en el body de la respuesta, que nadie loguea).
            logger.error("crear_cita FALLÓ: %s\n%s", e, traceback.format_exc())
            raise HTTPException(status_code=502, detail=str(e))


@app.post("/mover_cita", dependencies=[Depends(require_auth)])
def mover_cita(body: MoverCitaBody):
    with _write_lock:
        try:
            return dp.move_appointment(**body.model_dump())
        except Exception as e:
            logger.error("mover_cita FALLÓ: %s\n%s", e, traceback.format_exc())
            raise HTTPException(status_code=502, detail=str(e))
