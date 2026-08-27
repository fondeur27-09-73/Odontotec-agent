"""Watchdog: edge-trigger de alertas + guardrail de fecha pasada."""
import pytest

from scheduler import watchdog
from agent import metrics
from agent.tool_handlers import _fecha_no_pasada


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    watchdog._last_ok.clear()
    watchdog._last_alert_ts.clear()
    metrics.snapshot_and_reset()
    enviadas = []
    monkeypatch.setattr(watchdog.alerts, "send_dev_alert", enviadas.append)
    return enviadas


def test_primer_chequeo_caido_alerta(_reset):
    """Arrancar con el daemon YA muerto debe avisar. Si el primer chequeo se tragara la alerta
    (por no tener con qué comparar), un reinicio con Chrome muerto pasaría en silencio — que es
    justo lo que pasó el 2026-07-15."""
    watchdog._maybe_alert("daemon", False, "muerto")
    assert len(_reset) == 1
    assert "daemon caído" in _reset[0]


def test_primer_chequeo_sano_no_alerta(_reset):
    watchdog._maybe_alert("daemon", True, "logueado")
    assert _reset == []


def test_solo_alerta_en_la_transicion(_reset):
    """Caído 3 ciclos seguidos = UNA alerta. Cada 5 min serían 288 al día y el operador
    silenciaría el número."""
    for _ in range(3):
        watchdog._maybe_alert("llm", False, "402 sin saldo")
    assert len(_reset) == 1


def test_recuperado_avisa_una_vez(_reset):
    watchdog._maybe_alert("llm", False, "402")
    watchdog._maybe_alert("llm", True, "ok")
    watchdog._maybe_alert("llm", True, "ok")
    assert len(_reset) == 2
    assert "recuperado" in _reset[1]


def test_recordatorio_si_sigue_caido(_reset, monkeypatch):
    """El daemon deslogueado el 2026-08-25 mandó UN correo y se calló 2 días. Mientras siga caído
    hay que recordarlo cada WATCHDOG_REALERT_HOURS: sin humano en el VNC no se arregla solo."""
    monkeypatch.setenv("WATCHDOG_REALERT_HOURS", "6")
    t = [1_000_000.0]
    monkeypatch.setattr(watchdog.time, "time", lambda: t[0], raising=False)
    watchdog._maybe_alert("daemon", False, "no logueado")
    t[0] += 3600 * 5                      # 5h despues: aun no toca
    watchdog._maybe_alert("daemon", False, "no logueado")
    assert len(_reset) == 1
    t[0] += 3600 * 2                      # 7h desde el primer aviso: recordatorio
    watchdog._maybe_alert("daemon", False, "no logueado")
    assert len(_reset) == 2
    assert "daemon caído" in _reset[1]


def test_daemon_sin_url_no_alerta(monkeypatch):
    """En local no hay DENTIDESK_DAEMON_URL: no debe reportarse como caído."""
    monkeypatch.delenv("DENTIDESK_DAEMON_URL", raising=False)
    ok, detail = watchdog._check_daemon_session()
    assert ok is True
    assert "modo local" in detail


def test_daemon_no_logueado_es_caida(monkeypatch):
    monkeypatch.setenv("DENTIDESK_DAEMON_URL", "http://daemon:8100")

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"logueado": False}

    monkeypatch.setattr(watchdog, "_check_daemon_session",
                        watchdog._check_daemon_session)  # sin envolver
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
    ok, detail = watchdog._check_daemon_session()
    assert ok is False
    assert "VNC" in detail


def test_daemon_inalcanzable_es_caida(monkeypatch):
    """Chrome muerto -> el daemon revienta o no contesta. No puede pasar por sano."""
    monkeypatch.setenv("DENTIDESK_DAEMON_URL", "http://daemon:8100")
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _boom)
    ok, detail = watchdog._check_daemon_session()
    assert ok is False
    assert "ConnectError" in detail


def test_chequeo_que_revienta_no_mata_el_job(monkeypatch, _reset):
    monkeypatch.setattr(watchdog, "_check_llm_reachable",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.delenv("DENTIDESK_DAEMON_URL", raising=False)
    report = watchdog.check_once()
    assert report["llm"]["ok"] is False


def test_metrics_alerta_al_pasar_umbral(monkeypatch, _reset):
    monkeypatch.setenv("WATCHDOG_ESCALATE_BLOCKED_THRESHOLD", "2")
    for _ in range(2):
        metrics.increment("escalate_blocked")
    report = {}
    watchdog._check_metrics(report)
    assert len(_reset) == 1
    assert report["metrics"]["escalate_blocked"] == 2
    # snapshot_and_reset dejó el contador en 0 -> el próximo ciclo calla
    _reset.clear()
    watchdog._check_metrics({})
    assert _reset == []


# --- guardrail de fecha pasada ---

def test_fecha_pasada_se_bloquea():
    ok, msg = _fecha_no_pasada("2020-01-01")
    assert ok is False
    assert "ya pasó" in msg


def test_fecha_futura_pasa():
    assert _fecha_no_pasada("2999-01-01")[0] is True


def test_hoy_pasa():
    from datetime import datetime
    assert _fecha_no_pasada(datetime.now().strftime("%Y-%m-%d"))[0] is True


@pytest.mark.parametrize("valor", ["", "mañana", "31/07/2026", None])
def test_fecha_ilegible_es_lenient(valor):
    """No parsea -> deja pasar. _within_clinic_hours ya funciona así; bloquear aquí mataría
    citas legítimas por un formato raro."""
    assert _fecha_no_pasada(valor)[0] is True


def test_alerta_va_a_varios_destinatarios(monkeypatch):
    """WATCHDOG_ALERT_EMAIL con varios correos -> la alerta le llega a todos (técnico + clínica).
    Cubre las tres alertas por igual: daemon caído, LLM caído y conversación estancada, porque
    todas salen por send_dev_alert."""
    from integrations import alerts
    monkeypatch.setenv("WATCHDOG_ALERT_EMAIL", "fondeur28@gmail.com, contactoodontotec@gmail.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setenv("SMTP_PORT", "587")
    enviados = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): enviados.append(msg["To"])

    # OJO: send_dev_alert está stubbeada por el fixture autouse _reset; se prueba _send_email.
    monkeypatch.setattr(alerts.smtplib, "SMTP", _FakeSMTP)
    assert alerts._send_email("[CARLA-WATCHDOG] prueba", "cuerpo") is True
    assert enviados == ["fondeur28@gmail.com, contactoodontotec@gmail.com"]


def test_alerta_sin_destinatarios_no_envia(monkeypatch):
    from integrations import alerts
    monkeypatch.setenv("WATCHDOG_ALERT_EMAIL", "  ,  ")
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    assert alerts._send_email("asunto", "cuerpo") is False


def test_fallo_al_registrar_cita_queda_contado(monkeypatch):
    """El síntoma más caro (paciente sin cita) tiene que llegar al watchdog. El 2026-08-21 falló
    durante horas sin una sola alerta porque nadie lo contaba."""
    from agent import metrics
    from agent.tool_handlers import handle_tool
    from unittest.mock import patch
    metrics.snapshot_and_reset()
    with patch("agent.tool_handlers.dentidesk.find_in_day", return_value=None), \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               side_effect=RuntimeError("502 del daemon")):
        handle_tool("agendar_cita_dentidesk", {
            "patient_name": "Juan Pérez", "patient_phone": "+18091234567",
            "specialty": "general", "day": "lunes", "time": "10:00 AM",
            "fecha_iso": "2099-01-05", "cedula": "03102778092", "doctor": "Dr. General General"})
    assert metrics.snapshot_and_reset()["cita_write_failed"] == 1


def test_watchdog_alerta_al_primer_fallo_de_cita(_reset, monkeypatch):
    from agent import metrics
    metrics.increment("cita_write_failed")
    watchdog._check_metrics({})
    assert len(_reset) == 1
    assert "CITAS NO SE PUDIERON REGISTRAR" in _reset[0]
