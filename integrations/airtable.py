import os
from datetime import datetime
import httpx

# DEMO: registro temporal de citas en Airtable mientras Dentidesk no esté conectado.
# Se retira cuando Dentidesk entre (igual que Cal.com). Solo escritura de citas.

_API = "https://api.airtable.com/v0"


def _cfg() -> tuple[str, str, str]:
    key = os.getenv("AIRTABLE_API_KEY")
    base = os.getenv("AIRTABLE_BASE_ID")
    table = os.getenv("AIRTABLE_TABLE", "Citas")
    if not key or not base:
        raise RuntimeError("AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
    return key, base, table


def _mark_substituted_for_phone(key: str, base: str, table: str, phone: str) -> int:
    """Al reagendar: la cita activa anterior NO se borra, se marca como 'Sustituida'
    (queda el historial). La nueva cita se registra aparte como 'Reagendada'."""
    if not phone:
        return 0
    safe = phone.replace("'", "")
    # Citas activas = no canceladas, no atendidas, no ya sustituidas.
    formula = (
        f"AND({{Telefono}}='{safe}',"
        f"NOT(OR({{Estado}}='Cancelada',{{Estado}}='Atendida',{{Estado}}='Sustituida')))"
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = httpx.get(
        f"{_API}/{base}/{table}",
        headers={"Authorization": f"Bearer {key}"},
        params={"filterByFormula": formula},
        timeout=15,
    )
    r.raise_for_status()
    ids = [rec["id"] for rec in r.json().get("records", [])]
    marked = 0
    for rid in ids:
        u = httpx.patch(
            f"{_API}/{base}/{table}/{rid}",
            headers=headers,
            json={"fields": {"Estado": "Sustituida"}, "typecast": True},
            timeout=15,
        )
        if u.status_code == 200:
            marked += 1
    return marked


def register_appointment(
    patient_name: str,
    patient_phone: str,
    specialty: str,
    day: str,
    time: str,
    cedula: str = "",
    estado: str = "Confirmada",
    procedimiento: str = "",
    fecha_iso: str = "",
    is_reschedule: bool = False,
) -> dict:
    """Crea una fila de cita en Airtable. Devuelve el record id.
    Si is_reschedule=True, primero borra la cita activa anterior del paciente
    (por teléfono) para que no queden duplicados."""
    key, base, table = _cfg()
    substituted = 0
    if is_reschedule:
        try:
            substituted = _mark_substituted_for_phone(key, base, table, patient_phone)
        except Exception:
            substituted = 0  # best-effort: si falla, igual registramos la nueva
        if estado == "Confirmada":
            estado = "Reagendada"  # la nueva cita reagendada
    fields = {
        "Paciente": patient_name,
        "Cedula": cedula,
        "Telefono": patient_phone,
        "Especialidad": specialty,
        "Procedimiento": procedimiento,
        "Dia": day,
        "Hora": time,
        "Estado": estado,
        "Creado": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    if fecha_iso:
        fields["Fecha"] = fecha_iso
    r = httpx.post(
        f"{_API}/{base}/{table}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"fields": fields, "typecast": True},
        timeout=15,
    )
    r.raise_for_status()
    return {"record_id": r.json().get("id"), "substituted_previous": substituted}
