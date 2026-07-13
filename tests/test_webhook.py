import os
from unittest.mock import patch

for k, v in {
    "CHATWOOT_URL": "https://chatwoot.test.com",
    "CHATWOOT_API_TOKEN": "token",
    "CHATWOOT_ACCOUNT_ID": "1",
    "BOT_OFF_LABEL": "bot-off",
    "MAX_HISTORY": "20",
    "OPENAI_API_KEY": "test",
    "TIMEZONE": "America/Santo_Domingo",
    "DB_PATH": "./.pytest_cache/test_odontotec.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient
from main import app
client = TestClient(app)

PAYLOAD = {
    "event": "message_created",
    "data": {
        "content": "Hola",
        "message_type": 0,
        "attachments": [],
        "conversation": {
            "id": 42,
            "labels": [],
            "meta": {"sender": {"phone_number": "+18491234567"}}
        }
    }
}


def test_ignores_outgoing():
    p = {**PAYLOAD, "data": {**PAYLOAD["data"], "message_type": 1}}
    assert client.post("/webhook", json=p).json()["status"] == "ignored"


def test_ignores_non_message_event():
    assert client.post("/webhook", json={"event": "other"}).json()["status"] == "ignored"


def test_ignores_bot_off_label():
    payload = {**PAYLOAD, "data": {**PAYLOAD["data"], "conversation": {
        **PAYLOAD["data"]["conversation"], "labels": ["bot-off"]
    }}}
    assert client.post("/webhook", json=payload).json()["status"] == "ok"


def test_bot_off_en_chatwoot_no_corre_agente():
    # Bug auditoría 2026-07-05 #4: tras escalate_to_human (label bot-off), Carla NO debe
    # contestar encima del humano. El chequeo vive en _process_message (consulta labels reales
    # en Chatwoot, no el payload del webhook).
    with patch("integrations.chatwoot.get_labels", return_value=["bot-off"]), \
         patch("agent.claude.run_agent") as mock_agent, \
         patch("integrations.chatwoot.send_message") as mock_send:
        resp = client.post("/webhook", json=PAYLOAD)
        assert resp.json()["status"] == "ok"
        mock_agent.assert_not_called()
        mock_send.assert_not_called()


def test_webhook_secret_rechaza_sin_token(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3creto")
    assert client.post("/webhook", json=PAYLOAD).status_code == 401


def test_webhook_secret_acepta_token_correcto(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3creto")
    with patch("integrations.chatwoot.get_labels", return_value=[]), \
         patch("agent.claude.run_agent", return_value="Hola."), \
         patch("integrations.chatwoot.send_message"):
        resp = client.post("/webhook?token=s3creto", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_processes_message():
    with patch("integrations.chatwoot.get_labels", return_value=[]), \
         patch("main._build_history", return_value=[]), \
         patch("agent.claude.run_agent", return_value="Hola, soy Carla."), \
         patch("integrations.chatwoot.send_message") as mock_send:
        resp = client.post("/webhook", json=PAYLOAD)
        assert resp.json()["status"] == "ok"
        mock_send.assert_called_once_with(42, "Hola, soy Carla.")


def test_fallo_del_llm_le_avisa_al_paciente_en_vez_de_dejarlo_hablando_solo():
    # El 402 de OpenRouter (sin saldo) moría en un logger.error mientras el webhook ya había
    # contestado 200: Carla quedaba muda y por fuera todo se veía sano (2026-07-13). Cualquier
    # fallo de run_agent debe responderle algo al paciente.
    with patch("integrations.chatwoot.get_labels", return_value=[]), \
         patch("integrations.chatwoot.get_conv_messages", return_value=[]), \
         patch("agent.claude.run_agent", side_effect=RuntimeError("402 sin saldo")), \
         patch("integrations.chatwoot.send_message") as mock_send:
        resp = client.post("/webhook", json=PAYLOAD)
        assert resp.json()["status"] == "ok"
        mock_send.assert_called_once()
        conv_id, texto = mock_send.call_args.args
        assert conv_id == 42
        assert "momento" in texto.lower()
