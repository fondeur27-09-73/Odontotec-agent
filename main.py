import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from dotenv import load_dotenv

load_dotenv()

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("odontotec")

_scheduler = None

@asynccontextmanager
async def lifespan(app):
    global _scheduler
    from scheduler.reminders import start_scheduler
    _scheduler = start_scheduler()
    yield
    _scheduler.shutdown()

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
        from integrations.chatwoot import send_message
        from agent.claude import run_agent

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
