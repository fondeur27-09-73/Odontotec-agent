import os

for k, v in {
    "CHATWOOT_URL": "https://test.com",
    "CHATWOOT_API_TOKEN": "test",
    "CHATWOOT_ACCOUNT_ID": "1",
    "BOT_OFF_LABEL": "bot-off",
    "OPENAI_API_KEY": "test",
    "TIMEZONE": "America/Santo_Domingo",
}.items():
    os.environ.setdefault(k, v)


def test_health():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_button_action_reconoce_botones_cortos():
    from main import _button_action
    assert _button_action("Confirmar") == "CONFIRMAR"
    assert _button_action("✅ Confirmar") == "CONFIRMAR"
    assert _button_action("Cancelar cita") == "CANCELAR"
    assert _button_action("Reagendar") == "REAGENDAR"
    assert _button_action("Reprogramar mi cita") == "REAGENDAR"


def test_button_action_no_secuestra_frases_conversacionales():
    from main import _button_action
    # Frase larga durante el flujo del agente: debe ir al LLM, no al handler de botón.
    assert _button_action("sí, quiero confirmar mi cita para el martes") is None
    # Substring incrustado / typo: no es palabra completa, no cuenta (bug 2026-07-08).
    assert _button_action("si, conrfirmado") is None
    assert _button_action("") is None
