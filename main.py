import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from dotenv import load_dotenv

load_dotenv()

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
BOT_OFF_LABEL = os.getenv("BOT_OFF_LABEL", "bot-off")

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
    poller_ok = (
        _scheduler is not None
        and _scheduler.running
        and any(j.id == "poller" for j in _scheduler.get_jobs())
    )
    if not poller_ok:
        from fastapi import Response
        return Response(content='{"status":"poller_down"}', status_code=503, media_type="application/json")
    return {"status": "ok"}

async def _process_message(conv_id: int, phone: str, content: str):
    try:
        from integrations.supabase_client import ensure_patient, save_message, get_messages
        from integrations.chatwoot import send_message
        from agent.claude import run_agent

        ensure_patient(phone)
        save_message(phone, "user", content)
        history = get_messages(phone, MAX_HISTORY)

        response_text = await asyncio.to_thread(run_agent, history, conv_id)

        save_message(phone, "assistant", response_text)
        send_message(conv_id, response_text)
    except Exception as e:
        logger.error(f"Error processing message conv={conv_id} phone={phone}: {e}", exc_info=True)

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    if payload.get("event") != "message_created":
        return {"status": "ignored"}

    data = payload.get("data", {})
    if int(data.get("message_type", -1)) != 0:
        return {"status": "ignored"}

    conversation = data.get("conversation", {})
    conv_id = conversation.get("id")

    if BOT_OFF_LABEL in conversation.get("labels", []):
        return {"status": "bot_off"}

    from integrations.chatwoot import get_labels
    if BOT_OFF_LABEL in get_labels(conv_id):
        return {"status": "bot_off"}

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
