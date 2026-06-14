import os
import httpx

CHATWOOT_URL = os.getenv("CHATWOOT_URL")
API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
BOT_OFF_LABEL = os.getenv("BOT_OFF_LABEL", "bot-off")

def _headers() -> dict:
    return {"api_access_token": API_TOKEN, "Content-Type": "application/json"}

def _conv_url(conv_id: int, path: str) -> str:
    return f"{CHATWOOT_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/{path}"

def send_message(conversation_id: int, content: str) -> dict:
    r = httpx.post(_conv_url(conversation_id, "messages"), headers=_headers(), json={
        "content": content, "message_type": "outgoing", "private": False
    })
    r.raise_for_status()
    return r.json()

def get_labels(conversation_id: int) -> list[str]:
    r = httpx.get(_conv_url(conversation_id, "labels"), headers=_headers())
    r.raise_for_status()
    return r.json().get("payload", [])

def add_label(conversation_id: int, label: str):
    existing = get_labels(conversation_id)
    if label in existing:
        return
    httpx.post(_conv_url(conversation_id, "labels"), headers=_headers(),
               json={"labels": existing + [label]})

def is_bot_off(conversation_id: int) -> bool:
    return BOT_OFF_LABEL in get_labels(conversation_id)
