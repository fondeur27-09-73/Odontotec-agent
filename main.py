import os
import re
import hmac
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from dotenv import load_dotenv

load_dotenv()

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("odontotec")

# Un lock por conversación: dos mensajes rápidos del mismo paciente disparaban dos run_agent en
# paralelo (respuestas duplicadas y riesgo de doble agendado con dos navegadores a la vez).
# Con el lock se procesan en serie; conversaciones distintas siguen en paralelo.
_conv_locks: dict[int, asyncio.Lock] = {}

def _conv_lock(conv_id: int) -> asyncio.Lock:
    lock = _conv_locks.get(conv_id)
    if lock is None:
        lock = _conv_locks.setdefault(conv_id, asyncio.Lock())
    return lock

def _git_commit() -> str:
    """Commit desplegado, sin depender del binario `git` (python:3.11-slim no lo trae). Prioriza
    la env GIT_COMMIT (inyectable por el build) y si no, lee .git/HEAD a mano. 'desconocido' si nada."""
    env = os.getenv("GIT_COMMIT", "").strip()
    if env:
        return env[:12]
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        head = open(os.path.join(here, ".git", "HEAD")).read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            return open(os.path.join(here, ".git", ref)).read().strip()[:12]
        return head[:12]
    except Exception:
        return "desconocido"


def _log_startup_diagnostics():
    """Deja en logs, al arrancar, QUÉ código corre y si el cableado de escritura a Dentidesk está
    presente. Sin esto, verificar "¿está desplegado el fix?" y "¿puede escribir la agenda?" costaba
    navegar la UI de EasyPanel a ciegas (handoff 2026-07-08). MISSING en cualquiera de las tres de
    Dentidesk = ningún agendado/reagendado podrá escribir (candado o falta de daemon)."""
    def _present(name: str) -> str:
        return "SET" if os.getenv(name) else "MISSING"
    logger.info(
        "startup: commit=%s model=%s DENTIDESK_ALLOW_WRITES=%s DENTIDESK_DAEMON_URL=%s "
        "DENTIDESK_DAEMON_SECRET=%s WEBHOOK_SECRET=%s",
        _git_commit(), os.getenv("OPENAI_MODEL", "gpt-4o"),
        _present("DENTIDESK_ALLOW_WRITES"), _present("DENTIDESK_DAEMON_URL"),
        _present("DENTIDESK_DAEMON_SECRET"), _present("WEBHOOK_SECRET"),
    )


@asynccontextmanager
async def lifespan(app):
    _log_startup_diagnostics()
    # init_db aquí y no como efecto secundario de importar agent.tool_handlers: si /data no es
    # escribible, que falle el arranque con error claro, no el primer import.
    from integrations import db
    db.init_db()
    # Scheduler de recordatorios 48h/24h. Si APScheduler no está instalado o falla, no debe tumbar
    # el arranque del agente (Carla sigue respondiendo aunque los recordatorios no corran).
    scheduler = None
    try:
        from scheduler.reminders import start_scheduler
        scheduler = start_scheduler()
    except Exception as e:
        logger.error(f"No se pudo iniciar el scheduler de recordatorios: {e}")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)

app = FastAPI(title="Odontotec Agent", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

def _build_history(conv_id: int) -> list[dict]:
    from integrations.chatwoot import get_conv_messages
    # Si Chatwoot falla al devolver el historial, el fallo SUBE. Antes se tragaba y se devolvía []:
    # Carla seguía contestando pero con amnesia total (sin nombre, sin cédula, sin lo ya acordado),
    # y podía duplicar una cita o contradecir lo dicho. Peor que callarse, y sin rastro en logs.
    # Ahora cae en el handler de _process_message: CRITICAL en el log y el paciente recibe aviso.
    msgs = get_conv_messages(conv_id)
    history = []
    for m in msgs:
        mt = m.get("message_type")
        content = m.get("content") or ""
        if not content:
            continue
        if mt == 0:
            history.append({"role": "user", "content": content})
        elif mt == 1:
            history.append({"role": "assistant", "content": content})
    return history[-MAX_HISTORY:]

async def _process_message(conv_id: int, phone: str, content: str):
    from integrations.chatwoot import send_message, is_bot_off
    from agent.claude import run_agent

    logger.info(f"_process_message start conv={conv_id} phone={phone} content={content!r}")
    try:
        async with _conv_lock(conv_id):
            # Conversación escalada a humano (label bot-off): Carla NO contesta encima del agente
            # humano. Sin este chequeo, escalate_to_human ponía el label pero el webhook seguía
            # procesando cada mensaje entrante como si nada (bug auditoría 2026-07-05, #4).
            if await asyncio.to_thread(is_bot_off, conv_id):
                logger.info(f"_process_message skip conv={conv_id}: bot-off (escalada a humano)")
                return

            history = _build_history(conv_id)
            if not history or history[-1].get("role") != "user" or history[-1].get("content") != content:
                history.append({"role": "user", "content": content})

            response_text = await asyncio.to_thread(run_agent, history, conv_id, phone)
            logger.info(f"_process_message response conv={conv_id}: {response_text!r}")
            send_message(conv_id, response_text)
            logger.info(f"_process_message sent conv={conv_id}")
    except Exception as e:
        # Un fallo aquí (LLM sin saldo -> 402, timeout, daemon caído, respuesta vacía del modelo)
        # moría en un logger.error mientras el webhook ya había contestado 200: por fuera todo se
        # veía sano y Carla simplemente no hablaba. Dos días de depuración a ciegas (402 de
        # OpenRouter, 2026-07-13). Ahora: CRITICAL con el status HTTP si lo hay, y el paciente
        # recibe una respuesta en vez de quedarse hablando solo.
        status = getattr(e, "status_code", None)
        logger.critical(
            f"_process_message FALLÓ conv={conv_id} phone={phone} status={status}: {e}",
            exc_info=True,
        )
        try:
            send_message(conv_id, "Permítame un momento, por favor. Enseguida le atiendo.")
        except Exception as notify_err:
            logger.critical(
                f"_process_message tampoco pudo avisarle al paciente conv={conv_id}: {notify_err}"
            )

def _is_incoming(message_type) -> bool:
    return message_type == 0 or message_type == "incoming"


def _norm_txt(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    return s.lower().strip()


_BUTTON_KEYWORDS = {
    "confirmar": "CONFIRMAR",
    "cancelar": "CANCELAR",
    "reagendar": "REAGENDAR",
    "reprogramar": "REAGENDAR",
}


def _button_action(content: str) -> str | None:
    """Detecta la respuesta de BOTÓN de la plantilla de confirmación. Los botones mandan texto
    corto y determinista ('Confirmar', '✅ Confirmar', 'Reagendar cita'); se normaliza (sin emoji/
    acentos/puntuación) y se busca la palabra clave como PALABRA COMPLETA.

    NO debe dispararse con frases conversacionales ('sí, quiero confirmar mi cita para el martes'):
    esas van al agente. Por eso se exige que el mensaje sea corto (respuesta de botón, ≤3 palabras)
    y que la palabra clave sea una palabra entera — un substring incrustado ('conRfirmado') no
    cuenta, y una confirmación normal de PASO 5 no se secuestra al handler de recordatorios (bug
    2026-07-08: caía en _handle_button, no hallaba cita en la DB de recordatorios y abortaba)."""
    words = re.findall(r"[a-z]+", _norm_txt(content))
    if not words or len(words) > 3:
        return None
    for w in words:
        action = _BUTTON_KEYWORDS.get(w)
        if action:
            return action
    return None


async def _handle_button(conv_id: int, phone: str, action: str) -> None:
    """Ejecuta la acción del botón de forma determinista (sin LLM): CONFIRMAR/CANCELAR pegan
    directo a la API de Dentidesk (rápido, sin navegador). REAGENDAR NO entra aquí — se deja
    al flujo Carla. Mapea teléfono→IdAgenda vía el registro del recordatorio."""
    from integrations import dentidesk, db
    from integrations.chatwoot import send_message, is_bot_off
    try:
        if await asyncio.to_thread(is_bot_off, conv_id):
            logger.info(f"_handle_button skip conv={conv_id}: bot-off")
            return
        cita = await asyncio.to_thread(db.buscar_cita_por_phone, phone)
        if not cita:
            logger.warning(f"_handle_button conv={conv_id}: sin cita para phone={phone}")
            send_message(conv_id, "No encontramos una cita asociada a este número. "
                                  "Por favor escríbanos y con gusto le ayudamos.")
            return
        id_agenda = cita["id_agenda"]
        location = cita.get("location")
        if action == "CONFIRMAR":
            await asyncio.to_thread(dentidesk.confirm_appointment, id_agenda, location)
            send_message(conv_id, "¡Listo! Su cita quedó confirmada. ¡Le esperamos! 🦷")
        elif action == "CANCELAR":
            await asyncio.to_thread(
                dentidesk.update_status, id_agenda,
                dentidesk.STATUS["cancelado_email"], location)
            send_message(conv_id, "Su cita fue cancelada. Cuando quiera reagendar, escríbanos.")
        await asyncio.to_thread(db.guardar_respuesta, id_agenda, action)
        logger.info(f"_handle_button conv={conv_id} action={action} id_agenda={id_agenda} OK")
    except Exception as e:
        logger.error(f"_handle_button conv={conv_id} action={action}: {e}", exc_info=True)
        send_message(conv_id, "Tuvimos un problema procesando su respuesta. Un momento, le ayudamos.")

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    # Auth por token compartido: sin esto, cualquiera que alcance la URL puede forjar payloads
    # (Carla contestaría y escribiría en el CRM). Chatwoot no firma webhooks -> se agrega
    # ?token=XXX a la URL del webhook en Chatwoot y se valida aquí. Si WEBHOOK_SECRET no está
    # definido, se acepta todo (compatibilidad hasta configurar ambos lados) y se avisa en logs.
    secret = os.getenv("WEBHOOK_SECRET", "")
    if secret:
        supplied = request.query_params.get("token", "") or request.headers.get("x-webhook-token", "")
        if not hmac.compare_digest(supplied, secret):
            logger.warning("webhook rechazado: token inválido o ausente")
            raise HTTPException(status_code=401, detail="unauthorized")
    else:
        logger.warning("WEBHOOK_SECRET no configurado — el webhook acepta cualquier origen")

    payload = await request.json()
    logger.info(f"webhook payload: {payload}")

    if payload.get("event") != "message_created":
        return {"status": "ignored"}

    # Chatwoot sends message fields at the payload root, not nested under "data"
    data = payload.get("data") or payload
    if not _is_incoming(data.get("message_type")):
        return {"status": "ignored"}

    conversation = data.get("conversation", {})
    conv_id = conversation.get("id")

    phone = conversation.get("meta", {}).get("sender", {}).get("phone_number")
    if not phone:
        return {"status": "no_phone"}

    content = data.get("content", "")
    for att in data.get("attachments", []):
        if not content and att.get("file_type") in ("audio", "audio_file"):
            from utils.audio import transcribe_audio
            content = transcribe_audio(att.get("data_url", ""))
            break

    if not content:
        return {"status": "empty"}

    # Interceptor de botones de la plantilla de confirmación: CONFIRMAR/CANCELAR se resuelven
    # deterministas (API Dentidesk, sin LLM ni navegador). REAGENDAR sí cae al agente para pedir
    # la nueva fecha.
    action = _button_action(content)
    if action in ("CONFIRMAR", "CANCELAR"):
        background_tasks.add_task(_handle_button, conv_id, phone, action)
        return {"status": "button", "action": action}

    background_tasks.add_task(_process_message, conv_id, phone, content)
    return {"status": "ok"}
