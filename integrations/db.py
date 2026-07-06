import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/odontotec.db")


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                phone TEXT PRIMARY KEY,
                name TEXT,
                cedula TEXT
            )
        """)
        # Recordatorios de confirmación (48h/24h). Una fila por cita (IdAgenda). Guarda qué
        # tramos ya se enviaron (idempotencia: no reenviar el mismo tramo) y el teléfono +
        # conversation_id para mapear la respuesta del botón (CONFIRMAR/CANCELAR/REAGENDAR) de
        # vuelta a la cita sin re-consultar la agenda de Dentidesk.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recordatorios (
                id_agenda TEXT PRIMARY KEY,
                phone TEXT,
                conversation_id INTEGER,
                fecha_cita TEXT,
                location TEXT,
                enviado_48h INTEGER DEFAULT 0,
                enviado_24h INTEGER DEFAULT 0,
                respuesta TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recordatorios_phone ON recordatorios(phone)")


def get_patient(phone: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT name, cedula FROM patients WHERE phone = ?", (phone,)
        ).fetchone()
    if not row:
        return None
    patient = {}
    if row[0]:
        patient["name"] = row[0]
    if row[1]:
        patient["cedula"] = row[1]
    return patient


def save_patient(phone: str, name: str = "", cedula: str = "") -> dict:
    existing = get_patient(phone) or {}
    if name:
        existing["name"] = name
    if cedula:
        existing["cedula"] = cedula
    with _conn() as conn:
        conn.execute(
            "INSERT INTO patients (phone, name, cedula) VALUES (?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET name = excluded.name, cedula = excluded.cedula",
            (phone, existing.get("name", ""), existing.get("cedula", "")),
        )
    return existing


# ---------------------------------------------------------------------------
# Recordatorios de confirmación (48h/24h)
# ---------------------------------------------------------------------------

def _norm_phone(phone: str) -> str:
    """Últimos 10 dígitos — misma regla que integrations/dentidesk._norm_phone, para que el
    teléfono guardado al enviar el recordatorio matchee el que llega en el webhook al tocar el botón."""
    digits = "".join(c for c in str(phone) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


_TRAMO_COL = {"48h": "enviado_48h", "24h": "enviado_24h"}


def ya_enviado(id_agenda: str, tramo: str) -> bool:
    """¿Ya se mandó este tramo (48h/24h) para esta cita? Idempotencia del scheduler."""
    col = _TRAMO_COL[tramo]
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {col} FROM recordatorios WHERE id_agenda = ?", (str(id_agenda),)
        ).fetchone()
    return bool(row and row[0])


def marcar_recordatorio(id_agenda: str, tramo: str, phone: str, conversation_id: int | None,
                        fecha_cita: str, location: str) -> None:
    """Registra que el tramo (48h/24h) se envió para esta cita y guarda phone/conv_id/location
    para mapear la respuesta del botón. Upsert por id_agenda."""
    col = _TRAMO_COL[tramo]
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO recordatorios (id_agenda, phone, conversation_id, fecha_cita, location, "
            f"{col}, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?) "
            f"ON CONFLICT(id_agenda) DO UPDATE SET {col} = 1, phone = excluded.phone, "
            "conversation_id = COALESCE(excluded.conversation_id, recordatorios.conversation_id), "
            "fecha_cita = excluded.fecha_cita, location = excluded.location, updated_at = excluded.updated_at",
            (str(id_agenda), _norm_phone(phone), conversation_id, fecha_cita, str(location), now),
        )


def buscar_cita_por_phone(phone: str) -> dict | None:
    """Mapea el teléfono entrante (respuesta del botón) a la cita recordada más reciente.
    Evita re-consultar la agenda de Dentidesk. Devuelve dict o None."""
    target = _norm_phone(phone)
    with _conn() as conn:
        row = conn.execute(
            "SELECT id_agenda, phone, conversation_id, fecha_cita, location FROM recordatorios "
            "WHERE phone = ? ORDER BY updated_at DESC LIMIT 1", (target,)
        ).fetchone()
    if not row:
        return None
    return {"id_agenda": row[0], "phone": row[1], "conversation_id": row[2],
            "fecha_cita": row[3], "location": row[4]}


def guardar_respuesta(id_agenda: str, respuesta: str) -> None:
    """Guarda la respuesta del paciente (CONFIRMAR/CANCELAR/REAGENDAR) para auditoría."""
    with _conn() as conn:
        conn.execute(
            "UPDATE recordatorios SET respuesta = ?, updated_at = ? WHERE id_agenda = ?",
            (respuesta, _now(), str(id_agenda)),
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
