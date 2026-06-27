"""
Integración Dentidesk (CRM dental, app.dentidesk.com).

Contrato real descubierto 2026-06-27 (ver memoria dentidesk-api-contract).
Base: https://app.dentidesk.com/api — todos los endpoints POST JSON.

  - users/authentication.php   {email, password}        -> {token}   (token UN SOLO USO)
  - agenda/getAgendaDay.php    {Token, IdLocation, Date} -> {data:[citas]}   LECTURA
  - agenda/getAgendaStatus.php {Token, IdAgenda}         -> status           LECTURA (formato tentativo)
  - agenda/updateAgenda.php    {Token, IdAgenda, IdStatus} -> ...            ESCRITURA (candado)

REGLAS:
  * El token es de un solo uso -> cada llamada hace su propio login().
  * La respuesta viene en latin-1 (no utf-8 pese al header) -> se decodifica como latin-1.
  * Las funciones de ESCRITURA (updateAgenda) NO se ejecutan en desarrollo. Hay un candado
    por env DENTIDESK_ALLOW_WRITES; sin él, lanzan RuntimeError. En producción Carla lo activa.
"""
import os
import json
import httpx

_BASE = os.getenv("DENTIDESK_BASE", "https://app.dentidesk.com/api").rstrip("/")
_TIMEOUT = 20

# IdLocation por sucursal (del PDF de credenciales)
LOCATIONS = {"arroyo_hondo": "214", "naco": "215", "haina": "216"}
DEFAULT_LOCATION = os.getenv("DENTIDESK_LOCATION", "214")

# IdStatus (del diccionario de la API)
STATUS = {
    "no_confirmado": 1210,
    "confirmado": 1211,
    "hora_cancelada": 1212,
    "confirmado_email": 1213,
    "cancelado_email": 1214,
    "atendido": 1215,
    "llamada_no_contestada": 2008,
    "recado": 2009,
    "recibido": 49926,
}
STATUS_LABEL = {v: k for k, v in STATUS.items()}


def _post_json(path: str, payload: dict) -> dict:
    """POST JSON a Dentidesk. La respuesta trae los acentos como escapes JSON \\uXXXX
    (ASCII), así que r.json() los decodifica bien ('Odontología' correcto)."""
    r = httpx.post(f"{_BASE}/{path}", json=payload, timeout=_TIMEOUT)
    try:
        body = r.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Dentidesk {path}: respuesta no-JSON ({r.status_code}): {r.text[:200]}")
    if r.status_code != 200:
        msg = body.get("description") or body.get("message") or "error desconocido"
        raise RuntimeError(f"Dentidesk {path} -> {r.status_code}: {msg}")
    return body


def login() -> str:
    """Devuelve un token JWT NUEVO (un solo uso). Lee creds de .env."""
    email = os.getenv("DENTIDESK_USER")
    password = os.getenv("DENTIDESK_PASS")
    if not email or not password:
        raise RuntimeError("Faltan DENTIDESK_USER / DENTIDESK_PASS en el entorno")
    body = _post_json("users/authentication.php", {"email": email, "password": password})
    token = body.get("token")
    if not token:
        raise RuntimeError(f"Login Dentidesk sin token: {body}")
    return token


# ---------------------------------------------------------------------------
# LECTURA
# ---------------------------------------------------------------------------

def get_agenda_day(date_iso: str, location: str | None = None) -> list[dict]:
    """Citas de un día en una sucursal. date_iso = 'YYYY-MM-DD'. Hace su propio login
    (token single-use). Params case-sensitive: Token, IdLocation, Date."""
    loc = location or DEFAULT_LOCATION
    token = login()
    body = _post_json("agenda/getAgendaDay.php",
                      {"Token": token, "IdLocation": str(loc), "Date": date_iso})
    return body.get("data", [])


def get_agenda_status(id_agenda: str, location: str | None = None) -> dict:
    """Estado actual de una cita. LECTURA. Probado 2026-06-27: requiere IdLocation además de
    IdAgenda. Devuelve {IdAgenda, IdStatus, Status}."""
    loc = location or DEFAULT_LOCATION
    token = login()
    body = _post_json("agenda/getAgendaStatus.php",
                      {"Token": token, "IdAgenda": str(id_agenda), "IdLocation": str(loc)})
    data = body.get("data", [])
    return data[0] if data else {}


def find_by_cedula(cedula: str, date_iso: str, location: str | None = None) -> dict | None:
    """Busca una cita por cédula (PatientDocument) en la agenda de un día. Para 'Capa 1'
    (¿paciente nuevo o recurrente?). Devuelve la cita o None."""
    target = _norm_doc(cedula)
    for cita in get_agenda_day(date_iso, location):
        if _norm_doc(cita.get("PatientDocument", "")) == target:
            return cita
    return None


def find_by_phone(phone: str, date_iso: str, location: str | None = None) -> dict | None:
    """Busca una cita por teléfono (Phone/Phone2) en la agenda de un día. Para emparejar
    el número de WhatsApp entrante con una cita existente. Devuelve la cita o None."""
    target = _norm_phone(phone)
    for cita in get_agenda_day(date_iso, location):
        for field in ("Phone", "Phone2"):
            if _norm_phone(cita.get(field, "")) == target:
                return cita
    return None


def _norm_doc(doc: str) -> str:
    return "".join(c for c in str(doc) if c.isdigit())


def _norm_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits  # comparar por últimos 10 dígitos


# ---------------------------------------------------------------------------
# ESCRITURA — CANDADO. No se ejecuta en desarrollo.
# ---------------------------------------------------------------------------

def _require_writes_enabled():
    if os.getenv("DENTIDESK_ALLOW_WRITES", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "Escritura a Dentidesk BLOQUEADA. Activa DENTIDESK_ALLOW_WRITES=1 solo en producción "
            "y bajo autorización explícita. (Regla del cliente: nada de escrituras en el CRM en dev.)"
        )


def update_status(id_agenda: str, id_status: int, location: str | None = None) -> dict:
    """ESCRITURA: cambia el IdStatus de una cita existente (p.ej. confirmar = STATUS['confirmado']).
    BLOQUEADA salvo DENTIDESK_ALLOW_WRITES. NO mueve fecha/hora (la API no lo soporta).
    Incluye IdLocation (getAgendaDay/getAgendaStatus lo exigen; updateAgenda probablemente también
    — NO probado: regla del cliente, sin escrituras al CRM real en dev)."""
    _require_writes_enabled()
    loc = location or DEFAULT_LOCATION
    token = login()
    return _post_json("agenda/updateAgenda.php",
                      {"Token": token, "IdAgenda": str(id_agenda),
                       "IdStatus": int(id_status), "IdLocation": str(loc)})


def confirm_appointment(id_agenda: str, location: str | None = None) -> dict:
    """Atajo: marca una cita como Confirmada (1211). ESCRITURA — bajo el mismo candado."""
    return update_status(id_agenda, STATUS["confirmado"], location)
