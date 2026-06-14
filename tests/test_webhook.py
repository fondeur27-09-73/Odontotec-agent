import os
from unittest.mock import patch, MagicMock

for k, v in {
    "CHATWOOT_URL": "https://chatwoot.test.com",
    "CHATWOOT_API_TOKEN": "token",
    "CHATWOOT_ACCOUNT_ID": "1",
    "BOT_OFF_LABEL": "bot-off",
    "ANTHROPIC_API_KEY": "test",
    "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
    "MAX_HISTORY": "20",
    "CALCOM_URL": "https://calcom.test.com",
    "CALCOM_API_KEY": "test",
    "CALCOM_EVENTS": '{"general":1}',
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY": "test",
    "OPENAI_API_KEY": "test",
    "TIMEZONE": "America/Santo_Domingo",
}.items():
    os.environ.setdefault(k, v)

with patch("scheduler.reminders.start_scheduler", return_value=MagicMock()):
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


def test_respects_bot_off_from_payload():
    payload = {**PAYLOAD, "data": {**PAYLOAD["data"], "conversation": {
        **PAYLOAD["data"]["conversation"], "labels": ["bot-off"]
    }}}
    assert client.post("/webhook", json=payload).json()["status"] == "bot_off"


def test_respects_bot_off_from_api():
    with patch("integrations.chatwoot.get_labels", return_value=["bot-off"]):
        assert client.post("/webhook", json=PAYLOAD).json()["status"] == "bot_off"


def test_processes_message():
    with patch("integrations.chatwoot.get_labels", return_value=[]), \
         patch("integrations.supabase_client.ensure_patient"), \
         patch("integrations.supabase_client.save_message"), \
         patch("integrations.supabase_client.get_messages", return_value=[
             {"role": "user", "content": "Hola", "msg_type": "text"}
         ]), \
         patch("agent.claude.run_agent", return_value="Hola, soy Carla."), \
         patch("integrations.chatwoot.send_message") as mock_send:
        resp = client.post("/webhook", json=PAYLOAD)
        assert resp.json()["status"] == "ok"
        mock_send.assert_called_once_with(42, "Hola, soy Carla.")
