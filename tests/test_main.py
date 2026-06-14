import os
from unittest.mock import patch, MagicMock

for k, v in {
    "ANTHROPIC_API_KEY": "test",
    "CHATWOOT_URL": "https://test.com",
    "CHATWOOT_API_TOKEN": "test",
    "CHATWOOT_ACCOUNT_ID": "1",
    "BOT_OFF_LABEL": "bot-off",
    "CALCOM_URL": "https://test.com",
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

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
