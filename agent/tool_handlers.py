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


def _fecha_no_pasada(fecha_iso: str) -> tuple[bool, str]:
    """Bloquea agendar/reagendar hacia una fecha que ya pasó. Dentidesk lo acepta sin chistar y
    deshacerlo es a mano. El modelo se equivoca de año o arrastra la fecha vieja al reagendar.
    Lenient igual que _within_clinic_hours: si no parsea, deja pasar."""
    if not fecha_iso:
        return True, ""
    try:
        from zoneinfo import ZoneInfo
        hoy = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "America/Santo_Domingo"))).date()
    except Exception:
        hoy = datetime.now().date()
    try:
        fecha = datetime.strptime(fecha_iso[:10], "%Y-%m-%d").date()
    except Exception:
        return True, ""
    if fecha < hoy:
        return False, (f"La fecha {fecha_iso} ya pasó (hoy es {hoy:%Y-%m-%d}). "
                       f"Pídale al paciente una fecha futura.")
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


def _cedula_dominicana_ok(cedula: str) -> tuple[bool, str]:
    """Cédula dominicana = EXACTAMENTE 11 dígitos. Guardrail duro ANTES de escribir a Dentidesk.

    Dentidesk NO valida longitud (probado en vivo 2026-07-22: guardó un paciente con cédula '123',
    3 dígitos). Sin este candado, una cédula corta o vacía crea una ficha basura imposible de
    deduplicar, y peor: envenena el autocompletar de #rut, que luego trunca cédulas buenas a ese
    prefijo (bug '0310'). El límite vive aquí, no en Dentidesk, que acepta cualquier cosa.
    Devuelve (ok, mensaje_para_el_paciente)."""
    digits = "".join(c for c in cedula if c.isdigit())
    if len(digits) == 11:
        return True, ""
    return False, ("Para registrar la cita necesito la cédula completa del paciente: son 11 dígitos. "
                   "¿Me la puede enviar de nuevo, completa?")


def _escribir_o_contar(fn, **kwargs):
    """Ejecuta una ESCRITURA en Dentidesk contando el fallo antes de propagarlo.

    INCIDENTE 2026-08-21: `crear_cita` devolvió 502 durante HORAS (pacientes reales sin poder
    agendar) y el watchdog nunca avisó, porque solo miraba `/session` — que respondía 200 tan feliz.
    El fallo de escritura es el síntoma MÁS CARO que hay aquí: el paciente pide cita y no la
    consigue. Ahora queda contado y el watchdog alerta al primer fallo (ver scheduler/watchdog.py).
    La excepción sigue subiendo igual: esto solo mira, no cambia el comportamiento."""
    try:
        return fn(**kwargs)
    except Exception:
        from agent import metrics
        metrics.increment("cita_write_failed")
        raise


def _norm_nombre(s: str) -> str:
    """Nombre en minúsculas, sin tildes, sin dobles espacios — para comparar, no para mostrar."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _datos_del_paciente_real(phone: str, patient_name: str, cedula: str,
                             cita_para_tercero: bool) -> tuple[bool, str]:
    """Guardrail de CITA DE TERCERO (bug 2026-08-19, caso Karen Ferreras).

    Cuando quien escribe pide cita para un primo/hijo/hermano, el modelo tiende a reusar el nombre
    del contacto de WhatsApp (o el que get_patient devolvió) y agenda a nombre de QUIEN LLAMA, no
    del paciente que va a asistir. La cita queda en la ficha equivocada sin ningún error visible.
    Si la cita se declaró para un tercero pero el nombre o la cédula son los del titular del
    teléfono, se corta ANTES de escribir. Devuelve (ok, mensaje_para_el_modelo)."""
    if not cita_para_tercero:
        return True, ""
    titular = db.get_patient(phone) or {}
    ced_titular = "".join(c for c in str(titular.get("cedula", "")) if c.isdigit())
    ced_cita = "".join(c for c in str(cedula) if c.isdigit())
    choca_nombre = _norm_nombre(titular.get("name")) and         _norm_nombre(titular.get("name")) == _norm_nombre(patient_name)
    choca_cedula = bool(ced_titular) and ced_titular == ced_cita
    if choca_nombre or choca_cedula:
        return False, ("La cita es para otra persona, pero el nombre/cédula enviados son los de "
                       "quien escribe. Pida el nombre completo y la cédula del PACIENTE que va a "
                       "asistir (no los de quien escribe) y vuelva a registrar la cita.")
    return True, ""


def _elegir_doctor(doctor: str, specialty: str):
    """Doctor con el que se agenda. Carla ELIGE del listado de Dentidesk (parámetro `doctor`);
    si no eligió, cae al doctor por defecto de la especialidad. Devuelve el needle (str) o un dict
    de error listo para retornar. Un nombre que no está en el listado se rechaza ANTES de tocar
    Dentidesk: mejor pedirle que lo corrija que agendar con quien no era."""
    if doctor:
        real = _doctor_de_la_lista(doctor)
        if real is None:
            return {"success": False, "error": "doctor_desconocido",
                    "message": (f"'{doctor}' no está en el listado de profesionales de Dentidesk. "
                                f"Elija uno de la lista de doctores por especialidad.")}
        return real
    por_defecto = _resolve_doctor(specialty)
    if por_defecto is None:
        return {"success": False, "error": "doctor_no_mapeado",
                "message": f"No hay doctor configurado para la especialidad '{specialty}'."}
    return por_defecto


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
    cita_para_tercero: bool = False,
    doctor: str = "",
) -> dict:
    """ESCRITURA (UI Playwright): crea una cita NUEVA en Dentidesk. Backstop de fecha/horario antes
    de tocar nada. Bajo candado DENTIDESK_ALLOW_WRITES (no opera fuera del campo de simulación)."""
    ok, msg = _cedula_dominicana_ok(cedula)
    if not ok:
        return {"success": False, "error": "cedula_invalida", "message": msg}
    ok, msg = _datos_del_paciente_real(patient_phone, patient_name, cedula, cita_para_tercero)
    if not ok:
        return {"success": False, "error": "datos_del_titular", "message": msg}
    ok, msg = _fecha_no_pasada(fecha_iso)
    if not ok:
        return {"success": False, "error": "fecha_pasada", "message": msg}
    ok, msg = _within_clinic_hours(fecha_iso, time)
    if not ok:
        return {"success": False, "error": "fuera_de_horario", "message": msg}
    time24 = _to_24h(time)
    if time24 is None:
        return {"success": False, "error": "hora_invalida",
                "message": f"No entendí la hora '{time}'. Pida la hora de nuevo, ej: 10:00 AM."}
    doctor = _elegir_doctor(doctor, specialty)
    if isinstance(doctor, dict):
        return doctor
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    # Idempotencia (best-effort): si el paciente YA tiene cita ese día en la agenda real, NO crear
    # otra. Cubre el doble-clic del modelo (llamar la tool dos veces pese al "UNA SOLA VEZ") y dos
    # mensajes procesados casi a la vez. Si la consulta falla (red/API), se sigue con el agendado:
    # peor un duplicado raro que bloquear citas legítimas.
    try:
        # El TELÉFONO no identifica a nadie: una familia deja el mismo número y las citas de los
        # parientes se crean CON ESE número. Buscando solo por teléfono, la 2ª persona del mismo
        # número recibía "ya tiene cita ese día" y se quedaba SIN AGENDAR (caso Sra. Díaz,
        # 2026-08-24). find_in_day usa el mismo criterio que el resto (_cita_matches): con nombre
        # de >=2 palabras el teléfono NO decide. En cita de tercero el teléfono ni se manda.
        existente = dentidesk.find_in_day(
            fecha_iso, cedula=cedula, nombre=patient_name,
            phone="" if cita_para_tercero else patient_phone, location=loc,
        )
        # Solo es DUPLICADO si es a la MISMA HORA. Un paciente puede tener dos citas el mismo día
        # (la agenda real está llena de casos) y la Sra. Díaz (2026-08-24) se quedó sin la suya de
        # la tarde porque ya tenía una a las 08:30 del año pasado. Lo que este chequeo debe frenar
        # es el doble-clic del modelo, que repite la MISMA hora.
        if existente and str(existente.get("time") or "")[:5] == time24[:5]:
            return {"success": True, "ya_existia": True,
                    "IdAgenda": existente.get("IdAgenda"),
                    "paciente": existente.get("PatientName"),
                    "fecha": existente.get("Date"), "hora": existente.get("time"),
                    "message": "El paciente ya tiene esa MISMA cita registrada; no se creó otra. "
                               "Ya está agendada: confírmesela, no la registre de nuevo."}
    except Exception:
        pass
    res = _escribir_o_contar(
        dentidesk_playwright.create_appointment,
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
    nuevo_doctor: str = "",
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
    ok, msg = _fecha_no_pasada(fecha_iso)
    if not ok:
        return {"success": False, "error": "fecha_pasada", "message": msg}
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
    if nuevo_doctor or specialty:
        nuevo_doctor_label = _elegir_doctor(nuevo_doctor, specialty)
        if isinstance(nuevo_doctor_label, dict):
            return nuevo_doctor_label
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    res = _escribir_o_contar(
        dentidesk_playwright.move_appointment,
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
# Regla del cliente (2026-08-20): ortodoncia y odontología general NO se agendan con un
# especialista de nombre propio. Dentidesk tiene fichas dedicadas para eso — nombres REALES vistos
# en el listado de Profesionales del CRM (captura 2026-08-20):
#   "Dr. Ortodoncia Ortodoncia" | "Dr. General General" | "Dr. Periodoncia Especialistas"
# Los needles llevan las dos palabras a propósito: son únicos en la lista y no pueden colisionar
# con ningún doctor de nombre propio. El default viejo de ortodoncia ("Cabrera") caía en la Dra.
# Altemi Cabrera Sime, que NO es la ficha de ortodoncia — de ahí la cita mal agendada del 19-ago.
_DOCTOR_DEFAULTS = {
    "ortodoncia": "ortodoncia ortodoncia",      # ficha "Dr. Ortodoncia Ortodoncia"
    "general": "general general",               # ficha "Dr. General General"
    "periodoncia": "periodoncia especialistas",  # ficha "Dr. Periodoncia Especialistas"
    "cirugia": "Angel Lee",         # Dr. Angel Lee
    "endodoncia": "Cedano",         # Dra. Aimer Cedano
    "protesis": "Adriana Abreu",    # Dra. Adriana Abreu
    "odontopediatria": "Bastidas",  # Dra. Daniela Bastidas
}


# Listado REAL de Profesionales de Dentidesk (sidebar de la agenda, captura 2026-08-20). Carla
# elige de aquí; esto es el candado que impide que invente un doctor que no existe: un nombre fuera
# de esta lista no llega nunca a Playwright.
_DOCTORES_DENTIDESK = [
    "Adriana Abreu", "Aimer Cedano", "Altemi Cabrera Sime", "Angel Lee", "Anibel Chalas",
    "Daniela Bastidas", "Disiris Santana", "Edra Vargas", "General General", "Jeffray Lora",
    "Julia Montilla", "Marcelle Morales", "Mirleinis Casado", "Monica Vargas",
    "Ortodoncia Ortodoncia", "Periodoncia Especialistas", "Roner Capellan",
]


def _doctor_de_la_lista(doctor: str) -> str | None:
    """Empareja lo que el modelo eligió contra el listado real de Dentidesk. Tolera 'Dr./Dra.',
    tildes y mayúsculas ("Dra. Altemi Cabrera Sime" -> "Altemi Cabrera Sime"). Devuelve el nombre
    canónico, o None si no está en la lista (el caller lo rechaza sin tocar Dentidesk)."""
    n = _norm_nombre(doctor)
    for pref in ("dra. ", "dr. ", "dra ", "dr "):
        if n.startswith(pref):
            n = n[len(pref):]
            break
    if not n:
        return None
    for real in _DOCTORES_DENTIDESK:
        if _norm_nombre(real) == n:
            return real
    coincidencias = [r for r in _DOCTORES_DENTIDESK if n in _norm_nombre(r)]
    return coincidencias[0] if len(coincidencias) == 1 else None


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


def _datos_de_busqueda_del_tercero(telefono: str, cedula: str, nombre: str) -> tuple[str, str]:
    """Al buscar la cita de un TERCERO, descarta el teléfono/cédula que sean de QUIEN ESCRIBE.

    Bug hermano del de agendar (2026-08-19): si quien escribe pide mover la cita de su primo y la
    búsqueda va con SU teléfono, Dentidesk devuelve LA CITA DE ÉL — y Carla termina moviendo la cita
    equivocada, sin ningún error visible. El dato que sirve es el `nombre` del paciente de la cita.
    Solo se descarta lo que se puede PROBAR que es del titular del teléfono (está en la base local
    con otro nombre): si el teléfono resulta ser de verdad el del tercero, se conserva."""
    if not nombre:
        return telefono, cedula   # sin nombre no hay con qué comparar; se deja como venga
    titular = db.get_patient(telefono) if telefono else None
    if not titular:
        return telefono, cedula
    if _norm_nombre(titular.get("name")) == _norm_nombre(nombre):
        return telefono, cedula   # el teléfono ES del paciente de la cita
    ced_titular = "".join(c for c in str(titular.get("cedula", "")) if c.isdigit())
    ced_dada = "".join(c for c in str(cedula) if c.isdigit())
    return "", ("" if ced_titular and ced_titular == ced_dada else cedula)


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
    nombre: str = "",
    sucursal: str = "arroyo_hondo",
    cita_para_tercero: bool = False,
) -> dict:
    """LECTURA: busca la cita del paciente en la agenda real de Dentidesk para un día.
    Empareja por cédula, teléfono o NOMBRE (fallback para citas de terceros, donde el tel/cédula
    del remitente no coincide). Devuelve la cita o no encontrada."""
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    if cita_para_tercero:
        telefono, cedula = _datos_de_busqueda_del_tercero(telefono, cedula, nombre)
    cita = dentidesk.find_in_day(fecha_iso, cedula=cedula, phone=telefono,
                                 nombre=nombre, location=loc)
    if not cita:
        return {"found": False, "fecha": fecha_iso}
    return _cita_to_dict(cita)


def _buscar_cita_proxima_dentidesk(
    cedula: str = "",
    telefono: str = "",
    nombre: str = "",
    dias: int = 10,
    sucursal: str = "arroyo_hondo",
    cita_para_tercero: bool = False,
) -> dict:
    """LECTURA: busca la PRÓXIMA cita del paciente SIN conocer la fecha exacta, escaneando la agenda
    real desde hoy hacia adelante. Prueba en orden: teléfono, cédula, y por último NOMBRE (nombre y
    apellido). El nombre es el fallback para citas de TERCEROS (reservadas para otra persona), donde
    el tel/cédula del remitente de WhatsApp no coincide con los datos guardados en la cita — es como
    el humano la ubica en la UI. Devuelve la cita más temprana encontrada, o no encontrada."""
    loc = _LOCATION_ALIAS.get(str(sucursal).lower(), "214")
    # Un solo escaneo con los 3 datos: _cita_matches hace OR (tel | cédula | nombre). Escanear una
    # vez por dato costaba 3x logins y 3x tiempo para el mismo resultado.
    if cita_para_tercero:
        telefono, cedula = _datos_de_busqueda_del_tercero(telefono, cedula, nombre)
    cita = dentidesk.find_upcoming(cedula=cedula, phone=telefono, nombre=nombre,
                                   days=dias, location=loc)
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
    # El label bot-off es PASIVO: sin esto nadie se entera de que el paciente pidió un humano y queda
    # esperando. Avisar al operador. send_dev_alert es SMTP SÍNCRONO (timeout 15s) y esto corre dentro
    # de run_agent -> inline colgaría la respuesta al paciente hasta 15s. Por eso va en un hilo daemon
    # aparte (fire-and-forget). send_dev_alert es best-effort, nunca lanza.
    import threading
    from integrations import alerts
    msg = (f"[CARLA-ESCALADA] 🙋 la conversación {conversation_id} pidió un humano — "
           f"atender en Chatwoot. Motivo: {reason}")
    threading.Thread(target=alerts.send_dev_alert, args=(msg,), daemon=True).start()
    return {"success": True, "escalated": True}

