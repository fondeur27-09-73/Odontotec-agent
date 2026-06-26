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
) -> dict:
    """Crea una fila de cita en Airtable. Devuelve el record id."""
    key, base, table = _cfg()
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
    return {"record_id": r.json().get("id")}
