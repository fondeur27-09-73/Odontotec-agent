import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
BOT_OFF_LABEL = os.getenv("BOT_OFF_LABEL", "bot-off")

@asynccontextmanager
async def lifespan(app):
    from scheduler.reminders import start_scheduler
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()

app = FastAPI(title="Odontotec Agent", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()

    if payload.get("event") != "message_created":
        return {"status": "ignored"}

    data = payload.get("data", {})
    if int(data.get("message_type", -1)) != 0:
        return {"status": "ignored"}

    conversation = data.get("conversation", {})
    conv_id = conversation.get("id")

    # Check bot-off label from payload first (fast path)
    if BOT_OFF_LABEL in conversation.get("labels", []):
        return {"status": "bot_off"}

    # Double-check via Chatwoot API (labels may have been added after last sync)
    from integrations.chatwoot import get_labels, send_message
    if BOT_OFF_LABEL in get_labels(conv_id):
        return {"status": "bot_off"}

    phone = conversation.get("meta", {}).get("sender", {}).get("phone_number")
    if not phone:
        return {"status": "no_phone"}

    # Handle text content and audio attachments
    content = data.get("content", "")
    for att in data.get("attachments", []):
        if not content and att.get("file_type") in ("audio", "audio_file"):
            from utils.audio import transcribe_audio
            content = transcribe_audio(att.get("data_url", ""))
            break

    if not content:
        return {"status": "empty"}

    from integrations.supabase_client import ensure_patient, save_message, get_messages
    ensure_patient(phone)
    save_message(phone, "user", content)
    history = get_messages(phone, MAX_HISTORY)

    from agent.claude import run_agent
    response_text = run_agent(history, conv_id)

    save_message(phone, "assistant", response_text)
    send_message(conv_id, response_text)

    return {"status": "ok"}
