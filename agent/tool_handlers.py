import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from integrations import chatwoot, db, airtable
from utils.audio import transcribe_audio as _transcribe

db.init_db()


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


# DEMO: Cal.com retirado. Las citas se registran temporalmente en Airtable
# (register_appointment) mientras Dentidesk no esté conectado. Se retira al
# integrar Dentidesk.
def handle_tool(tool_name: str, tool_input: dict) -> str:
    handlers = {
        "get_patient": _get_patient,
        "save_patient": _save_patient,
        "register_appointment": _register_appointment,
        "escalate_to_human": _escalate_to_human,
        "transcribe_audio": _transcribe_audio,
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


def _register_appointment(
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
    ok, msg = _within_clinic_hours(fecha_iso, time)
    if not ok:
        # Backstop: no se registra fuera de horario. El agente debe pedir hora válida.
        return {"success": False, "error": "fuera_de_horario", "message": msg}
    label = _SPECIALTY_LABELS.get(specialty, specialty)
    res = airtable.register_appointment(
        patient_name=patient_name,
        patient_phone=patient_phone,
        specialty=label,
        day=day,
        time=time,
        cedula=cedula,
        estado=estado,
        procedimiento=procedimiento,
        fecha_iso=fecha_iso,
        is_reschedule=is_reschedule,
    )
    return {"success": True, **res}


def _escalate_to_human(reason: str, conversation_id: int) -> dict:
    chatwoot.add_label(conversation_id, "bot-off")
    return {"success": True, "escalated": True}


def _transcribe_audio(audio_url: str) -> dict:
    return {"transcription": _transcribe(audio_url)}


_SPECIALTY_LABELS = {
    "general": "Odontología General",
    "ortodoncia": "Ortodoncia",
    "endodoncia": "Endodoncia",
    "cirugia": "Cirugía e Implantología",
    "protesis": "Prótesis Dental",
    "odontopediatria": "Odontopediatría",
}


def _send_confirmation_email(
    patient_name: str,
    patient_phone: str,
    specialty: str,
    start_time: str,
    booking_uid: str,
    is_reschedule: bool = False,
    old_start_time: str = "",
) -> dict:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    email_from = os.getenv("EMAIL_FROM", smtp_user)
    email_to = os.getenv("EMAIL_CLINIC", "")

    if not smtp_user or not smtp_pass or not email_to:
        return {"success": False, "reason": "SMTP not configured"}

    def _fmt_dt(iso: str) -> tuple[str, str]:
        try:
            d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            days_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
            months_es = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
            return (f"{days_es[d.weekday()]} {d.day} de {months_es[d.month]} de {d.year}",
                    d.strftime("%I:%M %p"))
        except Exception:
            return iso, ""

    date_str, time_str = _fmt_dt(start_time)
    old_date_str, old_time_str = _fmt_dt(old_start_time) if old_start_time else ("", "")

    specialty_label = _SPECIALTY_LABELS.get(specialty, specialty.capitalize())
    action = "Reagenda de Cita" if is_reschedule else "Confirmación de Cita"
    reschedule_row = ""
    if is_reschedule and old_date_str:
        reschedule_row = f"""
      <tr style="border-bottom:1px solid #ddd;background:#fff3cd;">
        <td style="padding:10px;color:#666;">Cita anterior</td>
        <td style="padding:10px;">{old_date_str} — {old_time_str}</td>
      </tr>"""

    subject = f"Odontotec — {action}: {patient_name}"
    body = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;">
  <div style="background:#1a6db5;padding:20px;border-radius:8px 8px 0 0;">
    <h2 style="color:white;margin:0;">Odontotec — Odontología Especializada</h2>
    <p style="color:#cce4ff;margin:4px 0 0;">Arroyo Hondo, Santo Domingo, RD</p>
  </div>
  <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px;">
    <h3 style="color:#1a6db5;">{"Cita Reagendada" if is_reschedule else "Cita Confirmada"}</h3>
    <p>Estimado/a <strong>{patient_name}</strong>,</p>
    <p>{"Su cita ha sido reagendada exitosamente." if is_reschedule else "Su cita ha sido confirmada exitosamente."} A continuacion los detalles:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">{reschedule_row}
      <tr style="border-bottom:1px solid #ddd;">
        <td style="padding:10px;color:#666;">Especialidad</td>
        <td style="padding:10px;font-weight:bold;">{specialty_label}</td>
      </tr>
      <tr style="border-bottom:1px solid #ddd;">
        <td style="padding:10px;color:#666;">{"Nueva fecha" if is_reschedule else "Fecha"}</td>
        <td style="padding:10px;font-weight:bold;">{date_str}</td>
      </tr>
      <tr style="border-bottom:1px solid #ddd;">
        <td style="padding:10px;color:#666;">Hora</td>
        <td style="padding:10px;font-weight:bold;">{time_str}</td>
      </tr>
      <tr>
        <td style="padding:10px;color:#666;">Direccion</td>
        <td style="padding:10px;font-weight:bold;">Arroyo Hondo, Santo Domingo</td>
      </tr>
    </table>
    <p style="background:#e8f4fd;padding:12px;border-radius:6px;border-left:4px solid #1a6db5;">
      Por favor llegue <strong>5 minutos antes</strong> de su cita.
    </p>
    <p>Para reagendar o consultas, contactenos por WhatsApp: <strong>+1 809-977-9329</strong></p>
    <hr style="margin:20px 0;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;">Ref: {booking_uid} | Tel paciente: {patient_phone}</p>
  </div>
</body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(email_from, [email_to], msg.as_string())
    except Exception as e:
        # El correo es best-effort: la cita ya está reservada en Cal.com.
        # NUNCA propagar — un fallo de correo no debe romper el turno ni escalar al paciente.
        return {"success": False, "reason": str(e)}

    return {"success": True, "sent_to": email_to}
