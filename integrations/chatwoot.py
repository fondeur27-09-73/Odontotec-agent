import os
import httpx

BOT_OFF_LABEL = os.getenv("BOT_OFF_LABEL", "bot-off")

def _headers() -> dict:
    return {
        "api_access_token": os.getenv("CHATWOOT_API_TOKEN"),
        "Content-Type": "application/json"
    }

def _conv_url(conv_id: int, path: str) -> str:
    url = os.getenv("CHATWOOT_URL")
    account = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
    return f"{url}/api/v1/accounts/{account}/conversations/{conv_id}/{path}"

def send_message(conversation_id: int, content: str) -> dict:
    r = httpx.post(
        _conv_url(conversation_id, "messages"),
        headers=_headers(),
        json={"content": content, "message_type": 1, "private": False},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def get_labels(conversation_id: int) -> list[str]:
    r = httpx.get(_conv_url(conversation_id, "labels"), headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get("payload", [])

def add_label(conversation_id: int, label: str):
    existing = get_labels(conversation_id)
    if label in existing:
        return
    r = httpx.post(
        _conv_url(conversation_id, "labels"),
        headers=_headers(),
        json={"labels": existing + [label]},
        timeout=10
    )
    r.raise_for_status()

def is_bot_off(conversation_id: int) -> bool:
    return BOT_OFF_LABEL in get_labels(conversation_id)

def _base_url() -> str:
    url = os.getenv("CHATWOOT_URL")
    account = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
    return f"{url}/api/v1/accounts/{account}"

def get_open_conversations() -> list:
    r = httpx.get(
        f"{_base_url()}/conversations",
        headers=_headers(),
        params={"status": "open"},
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("payload", [])

def get_conv_messages(conv_id: int) -> list:
    r = httpx.get(
        _conv_url(conv_id, "messages"),
        headers=_headers(),
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("payload", [])
