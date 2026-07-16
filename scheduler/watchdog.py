"""
Vigilancia de producción: nadie se enteraba de que algo se había roto hasta que un paciente se
quejaba. La noche del 2026-07-15 Chrome se murió dentro del daemon y el sistema pasó un día entero
con la agenda muerta, sin una sola línea en el log (ver CLAUDE.md).

Job cada WATCHDOG_INTERVAL_MIN (default 5). Comprueba:
  1. Daemon Dentidesk logueado (GET /session del daemon — si Chrome está muerto, esto falla).
  2. LLM alcanzable (models.list(): valida auth + red sin gastar tokens de completion; habría
     cazado el 402 de OpenRouter que dejó a Carla muda dos días).
  3. Contadores de anomalía del agente (escaladas bloqueadas, iteraciones agotadas, fallos).

Alerta EDGE-TRIGGERED: solo en la transición sano->enfermo y enfermo->sano. Si sigue caído, se
calla — 5 min de intervalo serían 288 mensajes al día y el operador silenciaría el número.
"""

import os
import logging

from integrations import alerts

logger = logging.getLogger("odontotec.watchdog")

# None = todavía sin chequear. El primer chequeo que salga mal SÍ alerta (arrancar con el daemon
# ya muerto es justo el caso del 2026-07-15: sin esto, el arranque se comería el aviso).
_last_ok: dict[str, bool | None] = {}


def _interval_min() -> int:
    return int(os.getenv("WATCHDOG_INTERVAL_MIN", "5"))


def _check_daemon_session() -> tuple[bool, str]:
    """GET {DENTIDESK_DAEMON_URL}/session. Devuelve (ok, detalle)."""
    import httpx
    url = os.getenv("DENTIDESK_DAEMON_URL", "").rstrip("/")
    if not url:
        return True, "sin DENTIDESK_DAEMON_URL (modo local, no se vigila)"
    try:
        secret = os.getenv("DENTIDESK_DAEMON_SECRET", "")
        r = httpx.get(f"{url}/session",
                      headers={"X-Daemon-Secret": secret} if secret else {},
                      timeout=15)
        r.raise_for_status()
        logueado = r.json().get("logueado")
        if logueado is True:
            return True, "logueado"
        return False, "el navegador del daemon NO está logueado — entrar por VNC"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_llm_reachable() -> tuple[bool, str]:
    try:
        from agent.claude import _get_client
        _get_client().models.list()
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _maybe_alert(key: str, ok: bool, detail: str) -> None:
    was_ok = _last_ok.get(key)
    _last_ok[key] = ok
    if not ok and was_ok is not False:
        msg = f"[CARLA-WATCHDOG] ⚠️ {key} caído: {detail}"
        logger.critical(msg)
        alerts.send_dev_alert(msg)
    elif ok and was_ok is False:
        msg = f"[CARLA-WATCHDOG] ✅ {key} recuperado"
        logger.info(msg)
        alerts.send_dev_alert(msg)


def _check_metrics(report: dict) -> None:
    """Contadores del agente contra su umbral. No es edge-triggered: snapshot_and_reset ya deja
    el contador en 0, así que solo vuelve a avisar si el problema se repite en el próximo ciclo."""
    from agent import metrics
    snap = metrics.snapshot_and_reset()
    umbrales = {
        "escalate_blocked": (int(os.getenv("WATCHDOG_ESCALATE_BLOCKED_THRESHOLD", "5")),
                             "escaladas bloqueadas (Carla insistiendo en vez de escalar)"),
        "iterations_exhausted": (int(os.getenv("WATCHDOG_ITERATIONS_EXHAUSTED_THRESHOLD", "2")),
                                 "conversaciones agotaron las iteraciones del agente"),
        "agent_failed": (int(os.getenv("WATCHDOG_AGENT_FAILED_THRESHOLD", "3")),
                         "mensajes fallaron sin respuesta real al paciente"),
    }
    for key, (umbral, desc) in umbrales.items():
        n = snap.get(key, 0)
        if n >= umbral:
            msg = f"[CARLA-WATCHDOG] ⚠️ {n} {desc} en los últimos {_interval_min()} min (umbral: {umbral})"
            logger.warning(msg)
            alerts.send_dev_alert(msg)
    report["metrics"] = snap


def check_once() -> dict:
    """Un ciclo de chequeos. Devuelve el reporte (también sirve para probar a mano)."""
    report = {}
    for key, check in (("daemon", _check_daemon_session), ("llm", _check_llm_reachable)):
        try:
            ok, detail = check()
        except Exception as e:  # un chequeo roto no puede matar el job del scheduler
            ok, detail = False, f"chequeo reventó: {e}"
        _maybe_alert(key, ok, detail)
        report[key] = {"ok": ok, "detail": detail}
    try:
        _check_metrics(report)
    except Exception as e:
        report["metrics"] = {"error": str(e)}
    logger.info(f"watchdog check: {report}")
    return report


def start_watchdog(scheduler) -> None:
    """Registra el job. Se llama desde el lifespan de main.py con el scheduler ya existente."""
    minutos = _interval_min()
    scheduler.add_job(check_once, "interval", minutes=minutos, id="watchdog",
                      replace_existing=True)
    logger.info(f"watchdog: activo, chequea cada {minutos} min")
