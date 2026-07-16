"""Watchdog: edge-trigger de alertas + guardrail de fecha pasada."""
import pytest

from scheduler import watchdog
from agent import metrics
from agent.tool_handlers import _fecha_no_pasada


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    watchdog._last_ok.clear()
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
