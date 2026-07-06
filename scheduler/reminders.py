"""
Recordatorios de confirmación 48h/24h → citas confirmadas.

Job diario (APScheduler, @ REMINDER_HOUR en TIMEZONE): recorre las sucursales, mira las citas de
mañana (24h) y pasado mañana (48h), y a las NO confirmadas les manda la plantilla de WhatsApp
(Cloud API vía Chatwoot). Idempotente: cada tramo (48h/24h) se manda una sola vez por cita
(control en SQLite). La respuesta del botón la maneja el interceptor en main.py.

Lectura de agenda = API Dentidesk (rápida, sin navegador). El envío no toca Playwright.
"""
import os
import logging
from datetime import datetime, timedelta

from integrations import dentidesk, db, chatwoot

logger = logging.getLogger("odontotec.reminders")

REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "20"))
TIMEZONE = os.getenv("TIMEZONE", "America/Santo_Domingo")
WA_COUNTRY_CODE = os.getenv("WA_COUNTRY_CODE", "1")  # Rep. Dominicana

# Estados que YA cuentan como confirmada -> no recordar.
_CONFIRMADOS = {dentidesk.STATUS["confirmado"], dentidesk.STATUS["confirmado_email"],
                dentidesk.STATUS["atendido"], dentidesk.STATUS["hora_cancelada"],
                dentidesk.STATUS["cancelado_email"]}

# tramo -> días de anticipación
_TRAMOS = {"48h": 2, "24h": 1}


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    except Exception:
        return None


def _to_e164(phone: str) -> str:
    """Normaliza el teléfono de Dentidesk a E.164 para Chatwoot ('+1809...'). Si ya trae '+', se
    respeta; si son 10 dígitos locales, se antepone el código de país."""
    p = str(phone).strip()
    if p.startswith("+"):
        return p
    digits = "".join(c for c in p if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:
        return f"+{WA_COUNTRY_CODE}{digits}"
    return f"+{digits}"


def _is_confirmada(cita: dict) -> bool:
    """¿La cita ya no necesita recordatorio? IdStatus es la vía fiable; el texto es fallback.
    OJO: 'No confirmado' CONTIENE 'confirm' -> se usa startswith, no substring, para no
    tratar por error una cita pendiente como confirmada."""
    try:
        return int(cita.get("IdStatus")) in _CONFIRMADOS
    except (TypeError, ValueError):
        status = str(cita.get("Status", "")).strip().lower()
        return status.startswith("confirm") or "cancel" in status or "atend" in status


def _fecha_humana(date_iso: str) -> str:
    """'2026-06-29' -> '29/06/2026' (variable {{2}} de la plantilla)."""
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return date_iso


def _procesar_tramo(tramo: str, dias: int, hoy: datetime) -> int:
    fecha_obj = hoy + timedelta(days=dias)
    fecha_iso = fecha_obj.strftime("%Y-%m-%d")
    enviados = 0
    for loc in dentidesk.LOCATIONS.values():
        try:
            citas = dentidesk.get_agenda_day(fecha_iso, loc)
        except Exception as e:
            logger.error(f"reminders: get_agenda_day({fecha_iso}, {loc}) falló: {e}")
            continue
        for cita in citas:
            id_agenda = cita.get("IdAgenda")
            if not id_agenda:
                continue
            if _is_confirmada(cita):
                continue
            if db.ya_enviado(id_agenda, tramo):
                continue
            phone = _to_e164(cita.get("Phone") or cita.get("Phone2") or "")
            if not phone:
                logger.warning(f"reminders: cita {id_agenda} sin teléfono, se salta")
                continue
            params = {
                "1": cita.get("PatientName", ""),
                "2": _fecha_humana(cita.get("Date", fecha_iso)),
                "3": cita.get("Reason", "su cita"),
                "4": cita.get("time", ""),
                "5": cita.get("LocationName", ""),
            }
            try:
                conv_id = chatwoot.send_template(phone, params, name=cita.get("PatientName", ""))
                db.marcar_recordatorio(id_agenda, tramo, phone, conv_id, fecha_iso, str(loc))
                enviados += 1
                logger.info(f"reminders: enviado {tramo} cita={id_agenda} phone={phone}")
            except Exception as e:
                # No marcar enviado -> se reintenta en el próximo ciclo.
                logger.error(f"reminders: send_template cita={id_agenda} falló: {e}")
    return enviados


def run_once() -> dict:
    """Corre un ciclo completo (48h + 24h) ya. Útil para probar sin esperar al cron."""
    tz = _tz()
    hoy = datetime.now(tz) if tz else datetime.now()
    total = {}
    for tramo, dias in _TRAMOS.items():
        total[tramo] = _procesar_tramo(tramo, dias, hoy)
    logger.info(f"reminders: ciclo completo -> {total}")
    return total


def start_scheduler():
    """Registra el job diario. Se llama desde el lifespan de main.py."""
    from apscheduler.schedulers.background import BackgroundScheduler
    tz = _tz()
    scheduler = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
    scheduler.add_job(run_once, "cron", hour=REMINDER_HOUR, minute=0, id="recordatorios",
                      replace_existing=True)
    scheduler.start()
    logger.info(f"reminders: scheduler activo, corre diario a las {REMINDER_HOUR}:00 {TIMEZONE}")
    return scheduler