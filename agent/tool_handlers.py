import json
import os
import re
from datetime import datetime
from integrations import chatwoot, db, dentidesk, dentidesk_playwright


# Horario de la clínica (minutos desde medianoche). Backstop de código: aunque el
# modelo intente, NUNCA se registra una cita fuera de horario.
#   L-V (0-4): 8:30am - 5:30pm | Sáb (5): 8:00am - 12:00pm | Dom (6): cerrado
_HOURS = {0: (510, 1050), 1: (510, 1050), 2: (510, 1050), 3: (510, 1050),
          4: (510, 1050), 5: (480, 720)}


def _parse_minutes(time_str: str) -> int | None:
    """Convierte '1:30 PM', '8:30 a.m.', '9 am', '14:00' a minutos desde medianoche."""
    if not time_str:
        return None
    s = time_str.strip().lower().replace(".", "").replace(" ", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm|a|p)?m?$", s)
    if not m:
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ap = m.group(3)
    if ap in ("pm", "p") and hh != 12:
        hh += 12
    if ap in ("am", "a") and hh == 12:
        hh = 0
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def _to_24h(time_str: str) -> str | None:
    """Normaliza cualquier hora que mande el modelo ('10:00 AM', '3 pm', '14:00') a 'HH:MM' 24h.
    Playwright selecciona #horac/#minutos por valor numérico: pasarle '3:00 PM' crudo selecciona
    las 03:00 de la madrugada (o revienta con mm='00 PM'). Devuelve None si no se puede parsear."""
    mins = _parse_minutes(time_str)
    if mins is None:
        return None
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _within_clinic_hours(fecha_iso: str, time_str: str) -> tuple[bool, str]:
    """Valida fecha+hora contra el horario. Si no se puede parsear, permite (lenient)
    para no bloquear casos legítimos; solo bloquea lo que es claramente fuera de horario."""
    if not fecha_iso:
        return True, ""
    try:
        wd = datetime.strptime(fecha_iso[:10], "%Y-%m-%d").weekday()
    except Exception:
        return True, ""
    if wd == 6:
        return False, "Los domingos la clínica está cerrada."
    rng = _HOURS.get(wd)
    mins = _parse_minutes(time_str)
    if rng is None or mins is None:
        return True, ""
    lo, hi = rng
    if mins < lo or mins > hi:
        if wd == 5:
            return False, "Los sábados atendemos de 8:00 a.m. a 12:00 p.m."
        return False, "El horario es de lunes a viernes de 8:30 a.m. a 5:30 p.m."
    return True, ""


# Agenda real = Dentidesk. Lectura por API (buscar/confirmar). Crear y mover citas se hace por
# Playwright sobre la UI web (la API no lo permite). Toda ESCRITURA está bajo el candado
# DENTIDESK_ALLOW_WRITES y se ejercita solo en el campo de simulación autorizado.
def handle_tool(tool_name: str, tool_input: dict) -> str:
    handlers = {
        "get_patient": _get_patient,
        "save_patient": _save_patient,
        "buscar_cita_dentidesk": _buscar_cita_dentidesk,
        "buscar_cita_proxima_dentidesk": _buscar_cita_proxima_dentidesk,
        "agendar_cita_dentidesk": _agendar_cita_dentidesk,
        "reagendar_cita_dentidesk": _reagendar_cita_dentidesk,
        "confirmar_cita_dentidesk": _confirmar_cita_dentidesk,
        "escalate_to_human": _escalate_to_human,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        return json.dumps(handler(**tool_input), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _get_patient(phone: str) -> dict:
    patient = db.get_patient(phone)
    if patient:
        return {"found": True, "phone": phone, **patient}
    return {"found": False}


def _save_patient(phone: str, name: str, cedula: str = "") -> dict:
    saved = db.save_patient(phone, name=name, cedula=cedula)
    return {"success": True, "patient": {"phone": phone, **saved}}


def _agendar_cita_dentidesk(
    patient_name: str,
    patient_phone: str,
    specialty: str,
    day: str,
    time: str,
    cedula: str = "",
    procedimiento: str = "",
    fecha_iso: str = "",
    sucursal: str = "arroyo_hondo",
) -> dict:
    """ESCRITURA (UI Playwright): crea una cita NUEVA en Dentidesk. Backstop de horario antes de
    tocar nada. Bajo candado DENTIDESK_ALLOW_WRITES (no opera fuera del campo de simulación)."""
    ok, msg = _within_clinic_hours(fecha_iso, time)
    if not ok:
        return {"success": False, "error": "fuera_de_horario", "message": msg}
    time24 = _to_24h(time)
    if time24 is None:
        return {"success": False, "error": "hora_invalida",
                "message": f"No entendí la hora '{time}'. Pida la hora de nuevo, ej: 10:00 AM."}
    doctor = _resolve_doctor(specialty)
    if doctor is None:
        return {"success": False, "error": "doctor_no_mapeado",
                "message": f"No hay doctor configurado para la especialidad '{specialty}'."}
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    # Idempotencia (best-effort): si el paciente YA tiene cita ese día en la agenda real, NO crear
    # otra. Cubre el doble-clic del modelo (llamar la tool dos veces pese al "UNA SOLA VEZ") y dos
    # mensajes procesados casi a la vez. Si la consulta falla (red/API), se sigue con el agendado:
    # peor un duplicado raro que bloquear citas legítimas.
    try:
        existente = None
        if cedula:
            existente = dentidesk.find_by_cedula(cedula, fecha_iso, loc)
        if existente is None and patient_phone:
            existente = dentidesk.find_by_phone(patient_phone, fecha_iso, loc)
        if existente:
            return {"success": True, "ya_existia": True,
                    "IdAgenda": existente.get("IdAgenda"),
                    "paciente": existente.get("PatientName"),
                    "fecha": existente.get("Date"), "hora": existente.get("time"),
                    "message": "El paciente ya tiene una cita registrada ese día; no se creó otra."}
    except Exception:
        pass
    res = dentidesk_playwright.create_appointment(
        cedula=cedula, patient_name=patient_name, phone=patient_phone,
        doctor_label=doctor, fecha_iso=fecha_iso, time=time24,
        procedimiento=procedimiento, sucursal=loc,
    )
    return {"success": True, **(res if isinstance(res, dict) else {"result": res})}


def _reagendar_cita_dentidesk(
    id_agenda: str,
    fecha_actual_iso: str,
    patient_name: str,
    fecha_iso: str,
    time: str,
    sucursal: str = "arroyo_hondo",
    doctor: str = "",
    specialty: str = "",
    procedimiento: str = "",
) -> dict:
    """ESCRITURA (UI Playwright): mueve una cita existente a otra fecha/hora (la API no puede).
    Backstop de horario. Bajo candado DENTIDESK_ALLOW_WRITES. fecha_actual_iso/patient_name/doctor
    son necesarios para que Playwright ubique la tarjeta de la cita en la grilla de la agenda
    (vienen de una llamada previa a buscar_cita_dentidesk — doctor es su campo "doctor"; sin él,
    la agenda puede quedar filtrada por otro doctor y la tarjeta no aparece, ver
    dentidesk-playwright-bugs-2026-07-07).

    CAMBIO DE TRATAMIENTO (opcional): si el paciente cambia de tratamiento al reagendar, pasar
    specialty (la nueva especialidad del sistema) y/o procedimiento (el tratamiento en palabras).
    specialty se resuelve al doctor nuevo (igual que en agendar) y se cambia en el modal junto con
    el motivo — el modal de editar cita es el mismo que el de crear."""
    ok, msg = _within_clinic_hours(fecha_iso, time)
    if not ok:
        return {"success": False, "error": "fuera_de_horario", "message": msg}
    time24 = _to_24h(time)
    if time24 is None:
        return {"success": False, "error": "hora_invalida",
                "message": f"No entendí la hora '{time}'. Pida la hora de nuevo, ej: 10:00 AM."}
    # Cambio de tratamiento: resolver la nueva especialidad al doctor nuevo (misma lógica que agendar).
    # "" (general = personal fijo) es válido y significa NO tocar el doctor; None = especialidad mala.
    nuevo_doctor_label = ""
    if specialty:
        nuevo_doctor_label = _resolve_doctor(specialty)
        if nuevo_doctor_label is None:
            return {"success": False, "error": "doctor_no_mapeado",
                    "message": f"No hay doctor configurado para la especialidad '{specialty}'."}
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    res = dentidesk_playwright.move_appointment(
        id_agenda=id_agenda, fecha_actual_iso=fecha_actual_iso, patient_name=patient_name,
        nueva_fecha_iso=fecha_iso, nueva_hora=time24, sucursal=loc, doctor_label=doctor,
        nuevo_doctor_label=nuevo_doctor_label, nuevo_procedimiento=procedimiento,
    )
    return {"success": True, **(res if isinstance(res, dict) else {"result": res})}


_LOCATION_ALIAS = {
    "arroyo_hondo": "214", "arroyo hondo": "214", "214": "214",
    "naco": "215", "215": "215",
    "haina": "216", "216": "216",
}


# Especialidad (valor del tool) -> doctor al que se agenda en Dentidesk. El <select #dentista_cita>
# lista NOMBRES de doctor, no especialidades: pasarle "general" no matchea nada y la cita no se
# guarda. Los valores son "needles" (apellido distintivo) que dentidesk_playwright matchea de forma
# flexible contra el texto real de la opción. Defaults = primer doctor de cada especialidad según la
# lista del cliente (agent/prompts.py). Sobreescribible completo por env DENTIDESK_DOCTOR_MAP (JSON
# {"especialidad": "needle"}), p.ej. para fijar el personal de odontología general, que el cliente
# maneja como personal fijo sin especialista.
_DOCTOR_DEFAULTS = {
    "ortodoncia": "Cabrera",        # Dra. Altemi Cabrera Sime
    "cirugia": "Angel Lee",         # Dr. Angel Lee
    "endodoncia": "Cedano",         # Dra. Aimer Cedano
    "protesis": "Adriana Abreu",    # Dra. Adriana Abreu
    "odontopediatria": "Bastidas",  # Dra. Daniela Bastidas
    # "general" = "" a propósito: regla del cliente (2026-07-05) — odontología general la atiende
    # personal FIJO y la cita NO se registra por nombre de doctor. "" = no tocar #dentista_cita
    # (queda el valor que Dentidesk ponga por defecto en el modal).
    "general": "",
}


def _resolve_doctor(specialty: str) -> str | None:
    """Needle de doctor para una especialidad. "" = válido, significa NO seleccionar doctor
    (personal fijo, caso "general"). None = especialidad sin mapeo (error del caller)."""
    doctor_map = dict(_DOCTOR_DEFAULTS)
    override = os.getenv("DENTIDESK_DOCTOR_MAP", "")
    if override:
        try:
            doctor_map.update(json.loads(override))
        except json.JSONDecodeError:
            pass  # env malformado no debe tumbar el agendado; quedan los defaults
    return doctor_map.get(str(specialty).strip().lower())


def _cita_to_dict(cita: dict) -> dict:
    """Normaliza una cita cruda de la API de Dentidesk al shape que ve el modelo."""
    return {
        "found": True,
        "IdAgenda": cita.get("IdAgenda"),
        "paciente": cita.get("PatientName"),
        "fecha": cita.get("Date"),
        "hora": cita.get("time"),
        "procedimiento": cita.get("Reason"),
        "doctor": cita.get("ProfessionalName"),
        "especialidad": (cita.get("ProfessionalSpeciality") or [None])[0],
        "estado": cita.get("Status"),
        "sucursal": cita.get("LocationName"),
    }


def _buscar_cita_dentidesk(
    fecha_iso: str,
    cedula: str = "",
    telefono: str = "",
    sucursal: str = "arroyo_hondo",
) -> dict:
    """LECTURA: busca la cita del paciente en la agenda real de Dentidesk para un día.
    Empareja por cédula o por teléfono. Devuelve la cita (datos del propio paciente) o no encontrada."""
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    cita = None
    if cedula:
        cita = dentidesk.find_by_cedula(cedula, fecha_iso, loc)
    if cita is None and telefono:
        cita = dentidesk.find_by_phone(telefono, fecha_iso, loc)
    if not cita:
        return {"found": False, "fecha": fecha_iso}
    return _cita_to_dict(cita)


def _buscar_cita_proxima_dentidesk(
    cedula: str = "",
    telefono: str = "",
    nombre: str = "",
    dias: int = 10,
    sucursal: str = "arroyo_hondo",
) -> dict:
    """LECTURA: busca la PRÓXIMA cita del paciente SIN conocer la fecha exacta, escaneando la agenda
    real desde hoy hacia adelante. Prueba en orden: teléfono, cédula, y por último NOMBRE (nombre y
    apellido). El nombre es el fallback para citas de TERCEROS (reservadas para otra persona), donde
    el tel/cédula del remitente de WhatsApp no coincide con los datos guardados en la cita — es como
    el humano la ubica en la UI. Devuelve la cita más temprana encontrada, o no encontrada."""
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    # ponytail: prefer phone over cedula (phone more reliable, cedula format varies in Dentidesk storage)
    cita = dentidesk.find_upcoming(phone=telefono, days=dias, location=loc) if telefono else None
    if not cita and cedula:
        cita = dentidesk.find_upcoming(cedula=cedula, days=dias, location=loc)
    if not cita and nombre:
        cita = dentidesk.find_upcoming(nombre=nombre, days=dias, location=loc)
    if not cita:
        return {"found": False}
    return _cita_to_dict(cita)


def _confirmar_cita_dentidesk(id_agenda: str, sucursal: str = "arroyo_hondo") -> dict:
    """ESCRITURA: marca una cita existente como Confirmada en Dentidesk. Protegida por el candado
    DENTIDESK_ALLOW_WRITES del módulo (sin ese env lanza error). No se ejecuta en desarrollo."""
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    res = dentidesk.confirm_appointment(id_agenda, loc)
    return {"success": True, **(res if isinstance(res, dict) else {"result": res})}


def _escalate_to_human(reason: str, conversation_id: int) -> dict:
    chatwoot.add_label(conversation_id, "bot-off")
    return {"success": True, "escalated": True}

