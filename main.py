import os
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

@asynccontextmanager
async def lifespan(app):
    # init_db aquí y no como efecto secundario de importar agent.tool_handlers: si /data no es
    # escribible, que falle el arranque con error claro, no el primer import.
    from integrations import db
    db.init_db()
    yield

app = FastAPI(title="Odontotec Agent", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

def _build_history(conv_id: int) -> list[dict]:
    from integrations.chatwoot import get_conv_messages
    try:
        msgs = get_conv_messages(conv_id)
    except Exception:
        return []
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
    logger.info(f"_process_message start conv={conv_id} phone={phone} content={content!r}")
    try:
        from integrations.chatwoot import send_message, is_bot_off
        from agent.claude import run_agent

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
        logger.error(f"Error processing message conv={conv_id} phone={phone}: {e}", exc_info=True)

def _is_incoming(message_type) -> bool:
    return message_type == 0 or message_type == "incoming"

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

    background_tasks.add_task(_process_message, conv_id, phone, content)
    return {"status": "ok"}
