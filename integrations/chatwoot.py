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


# ---------------------------------------------------------------------------
# Envío proactivo de plantilla WhatsApp (Cloud API vía Chatwoot)
# ---------------------------------------------------------------------------
# Fuera de la ventana de 24h, Meta solo permite plantillas APROBADAS (no texto libre). El
# recordatorio 48h/24h cae ahí, así que se manda como template_params (formato enhanced de
# Chatwoot: processed_params.body = {"1":..,"5":..}). Ver docs/carla-plantilla-confirmacion.md.

WA_TEMPLATE_NAME = os.getenv("WA_TEMPLATE_NAME", "confirmacion_cita")
WA_TEMPLATE_LANG = os.getenv("WA_TEMPLATE_LANG", "es")
WA_TEMPLATE_CATEGORY = os.getenv("WA_TEMPLATE_CATEGORY", "UTILITY")


def _inbox_id() -> int:
    inbox = os.getenv("CHATWOOT_INBOX_ID")
    if not inbox:
        raise RuntimeError("Falta CHATWOOT_INBOX_ID (inbox WhatsApp) para enviar plantillas")
    return int(inbox)


def find_or_create_contact(phone: str, name: str = "") -> tuple[int, str]:
    """Devuelve (contact_id, source_id) para el teléfono en el inbox WhatsApp. Crea el contacto
    si no existe. source_id = identificador del contacto dentro del inbox (lo pide crear conversación).
    phone debe venir en formato E.164 ('+1809...')."""
    inbox_id = _inbox_id()
    # Buscar contacto existente por teléfono
    r = httpx.get(f"{_base_url()}/contacts/search", headers=_headers(),
                  params={"q": phone}, timeout=10)
    r.raise_for_status()
    for c in r.json().get("payload", []):
        cid = c.get("id")
        for ci in c.get("contact_inboxes", []):
            if ci.get("inbox", {}).get("id") == inbox_id:
                return cid, ci.get("source_id")
        # Contacto existe pero sin contact_inbox en este inbox -> crear el vínculo
        if cid:
            src = _create_contact_inbox(cid, inbox_id, phone)
            return cid, src
    # No existe -> crearlo (Chatwoot crea el contact_inbox y devuelve source_id)
    payload = {"inbox_id": inbox_id, "name": name or phone, "phone_number": phone}
    r = httpx.post(f"{_base_url()}/contacts", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    data = r.json().get("payload", {}).get("contact", {}) or r.json().get("payload", {})
    cid = data.get("id")
    for ci in data.get("contact_inboxes", []):
        if ci.get("inbox", {}).get("id") == inbox_id or ci.get("source_id"):
            return cid, ci.get("source_id")
    # Fallback: crear contact_inbox explícito
    return cid, _create_contact_inbox(cid, inbox_id, phone)


def _create_contact_inbox(contact_id: int, inbox_id: int, source_id: str) -> str:
    r = httpx.post(f"{_base_url()}/contacts/{contact_id}/contact_inboxes",
                   headers=_headers(), json={"inbox_id": inbox_id, "source_id": source_id},
                   timeout=10)
    r.raise_for_status()
    return r.json().get("source_id", source_id)


def _create_conversation(contact_id: int, source_id: str, inbox_id: int) -> int:
    r = httpx.post(f"{_base_url()}/conversations", headers=_headers(),
                   json={"inbox_id": inbox_id, "contact_id": contact_id, "source_id": source_id},
                   timeout=10)
    r.raise_for_status()
    return r.json().get("id")


def send_template(phone: str, params: dict, name: str = "",
                  template_name: str | None = None) -> int:
    """Manda la plantilla de confirmación al paciente. `params` = {"1":nombre, "2":fecha,
    "3":motivo, "4":hora, "5":sucursal} (variables del cuerpo). Crea contacto+conversación si hace
    falta. Devuelve conversation_id (para mapear la respuesta del botón). phone en E.164."""
    inbox_id = _inbox_id()
    contact_id, source_id = find_or_create_contact(phone, name)
    conv_id = _create_conversation(contact_id, source_id, inbox_id)
    body = {
        "content": "",
        "message_type": "outgoing",
        "template_params": {
            "name": template_name or WA_TEMPLATE_NAME,
            "category": WA_TEMPLATE_CATEGORY,
            "language": WA_TEMPLATE_LANG,
            "processed_params": {"body": {str(k): str(v) for k, v in params.items()}},
        },
    }
    r = httpx.post(_conv_url(conv_id, "messages"), headers=_headers(), json=body, timeout=15)
    r.raise_for_status()
    return conv_id
