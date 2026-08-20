"""
Alertas del watchdog al OPERADOR (no al paciente).

Dos canales, los dos best-effort — una alerta que revienta no puede tumbar el ciclo del watchdog:

1. Email (WATCHDOG_ALERT_EMAIL + SMTP). Funciona HOY: smtplib de la stdlib, credenciales SMTP
   reusadas de las que ya usa Chatwoot (confirmadas 2026-07-03).
2. WhatsApp (WATCHDOG_ALERT_TO_NUMBER + WATCHDOG_ALERT_INBOX_ID). BLOQUEADO hasta que exista el
   inbox de Chatwoot dedicado y Meta apruebe la plantilla `alerta_operativa`. Mientras tanto se
   salta solo.

DESACOPLE (a propósito): el watchdog NUNCA usa CHATWOOT_INBOX_ID (el número de Carla con
pacientes). Cuando llegue la Fase 2 y el cliente entregue su número oficial de Meta, se cambia
CHATWOOT_INBOX_ID y este archivo no se toca.

Si no hay ningún canal configurado: log CRITICAL. Peor es cero.
"""

import os
import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("odontotec.alerts")


def _env(name: str, default: str = "") -> str:
    # Se lee en cada llamada, no al importar: EasyPanel inyecta env vars al arrancar el
    # contenedor y así configurar el inbox nuevo no obliga a rebuild, solo a restart.
    return os.getenv(name, default).strip()


def _destinatarios() -> list[str]:
    """WATCHDOG_ALERT_EMAIL admite VARIOS destinos separados por coma o punto y coma
    ("dev@x.com, clinica@y.com"): la misma alerta le llega al técnico y a la clínica. Un solo
    correo es un punto ciego — si esa persona no lo mira, nadie se entera."""
    crudo = _env("WATCHDOG_ALERT_EMAIL").replace(";", ",")
    return [d.strip() for d in crudo.split(",") if d.strip()]


def _send_email(subject: str, message: str) -> bool:
    """Usa las SMTP_* genéricas (un solo servidor de correo para la app, no uno por feature).
    Solo WATCHDOG_ALERT_EMAIL es propio: es el destino, y va al operador, no a un paciente."""
    to = _destinatarios()
    host = _env("SMTP_HOST")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    if not (to and host and user and password):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _env("EMAIL_FROM") or user
    msg["To"] = ", ".join(to)
    msg.set_content(message)
    port = int(_env("SMTP_PORT", "587"))
    # 465 = SSL directo; 587/25 = STARTTLS. Elegir mal cuelga la conexión hasta el timeout.
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
    logger.info(f"send_dev_alert: email enviado a {', '.join(to)}")
    return True


def _send_whatsapp(message: str) -> bool:
    to = _env("WATCHDOG_ALERT_TO_NUMBER")
    inbox = _env("WATCHDOG_ALERT_INBOX_ID")
    if not (to and inbox):
        return False
    from integrations import chatwoot
    chatwoot.send_template(
        to,
        {"1": message},
        name="watchdog",
        template_name=_env("WATCHDOG_ALERT_TEMPLATE_NAME", "alerta_operativa"),
        inbox_id=int(inbox),
    )
    logger.info(f"send_dev_alert: WhatsApp enviado a {to}")
    return True


def send_dev_alert(message: str) -> None:
    """Avisa al operador por todos los canales configurados. Nunca lanza excepción."""
    # El asunto lleva el qué, no solo la etiqueta: la alerta se lee en el teléfono y tiene que
    # entenderse sin abrirla.
    asunto = message.splitlines()[0][:120] if message.strip() else "[CARLA-WATCHDOG] alerta"
    sent = False
    for canal, fn in (("email", lambda: _send_email(asunto, message)),
                      ("whatsapp", lambda: _send_whatsapp(message))):
        try:
            sent = fn() or sent
        except Exception as e:
            logger.critical(f"send_dev_alert: canal {canal} FALLÓ ({e}). Mensaje: {message}")
    if not sent:
        logger.critical(f"send_dev_alert: sin canal configurado. No enviado. Mensaje: {message}")
