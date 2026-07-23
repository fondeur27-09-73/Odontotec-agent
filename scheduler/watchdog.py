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

# Conversaciones por las que ya se avisó que están estancadas. Edge-trigger: una alerta por
# conversación; al dejar de estar estancada se saca del set y se re-arma por si recae.
_alerted_stalled: set[int] = set()


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


def _age_secs(raw) -> float | None:
    """Segundos transcurridos desde `raw` (epoch int de Chatwoot, o ISO 8601), o None si no parsea."""
    import time
    from datetime import datetime
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = float(raw)
        else:  # "2026-07-22T05:17:00.000Z" o con offset
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        return time.time() - ts
    except (ValueError, TypeError, OverflowError):
        return None


def _patient_waiting_secs(cid: int) -> float | None:
    """Segundos que el paciente lleva esperando en la conversación `cid`: antigüedad de SU último
    mensaje, pero solo si Carla no contestó después. None si el último mensaje no es del paciente
    (la pelota no está de nuestro lado) o no se puede leer el timestamp."""
    from integrations import chatwoot
    msgs = chatwoot.get_conv_messages(cid)
    # message_type: 0=paciente, 1=Carla/humano, 2=actividad. Solo la conversación real (0/1).
    convo = [m for m in msgs if m.get("message_type") in (0, 1)]
    if not convo:
        return None
    last = convo[-1]  # ponytail: asume orden cronológico ascendente (default de Chatwoot); si viniera al revés, sería convo[0]
    if last.get("message_type") != 0:
        return None  # Carla ya respondió
    return _age_secs(last.get("created_at"))


def _check_stalled_conversations(report: dict) -> None:
    """Avisa cuando un paciente lleva > umbral esperando respuesta en una conversación ABIERTA —
    sea cual sea la causa (daemon caído, saldo LLM en 0, Chatwoot, VPS, cualquier cosa). Vigila el
    SÍNTOMA (paciente esperando), no la causa, así que cubre fallos que ninguna otra sonda anticipa.
    Edge-triggered vía _alerted_stalled: UNA alerta por conversación, no una cada 5 min."""
    from integrations import chatwoot
    umbral = int(os.getenv("WATCHDOG_STALLED_MIN", "5")) * 60
    try:
        convs = chatwoot.get_open_conversations()
    except Exception as e:
        report["stalled"] = {"error": f"{type(e).__name__}: {e}"}
        return
    estancadas: dict[int, float] = {}
    for c in convs:
        cid = c.get("id")
        if cid is None:
            continue
        try:
            espera = _patient_waiting_secs(cid)
        except Exception as e:
            logger.warning(f"watchdog stalled: no pude leer la conv {cid}: {e}")
            continue
        if espera is None or espera < umbral:
            continue
        # Candidato estancado: confirmar que no está ya escalada a un humano (alguien la tiene).
        try:
            if chatwoot.is_bot_off(cid):
                continue
        except Exception:
            pass  # si no se puede saber, mejor avisar de más que dejar a un paciente colgado
        estancadas[cid] = espera
    for cid, espera in estancadas.items():
        if cid not in _alerted_stalled:
            _alerted_stalled.add(cid)
            msg = (f"[CARLA-WATCHDOG] 🕐 el paciente de la conversación {cid} lleva "
                   f"{espera / 60:.0f} min esperando respuesta en WhatsApp — revisar en Chatwoot")
            logger.warning(msg)
            alerts.send_dev_alert(msg)
    for cid in list(_alerted_stalled):
        if cid not in estancadas:
            _alerted_stalled.discard(cid)  # se resolvió -> re-armar por si vuelve a estancarse
    report["stalled"] = {"count": len(estancadas), "ids": list(estancadas)}


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
        _check_stalled_conversations(report)
    except Exception as e:
        report["stalled"] = {"error": str(e)}
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
