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
    assert response.json() == {"status": "ok"}
