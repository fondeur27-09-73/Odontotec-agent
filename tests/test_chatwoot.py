import respx
import httpx
import os

os.environ.setdefault("CHATWOOT_URL", "https://chatwoot.test.com")
os.environ.setdefault("CHATWOOT_API_TOKEN", "test-token")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
os.environ.setdefault("BOT_OFF_LABEL", "bot-off")

from integrations.chatwoot import send_message, get_labels, add_label, is_bot_off

@respx.mock
def test_send_message():
    respx.post(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 1}))
    assert send_message(42, "Hola")["id"] == 1

@respx.mock
def test_get_labels():
    respx.get(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": ["bot-off"]}))
    assert "bot-off" in get_labels(42)

@respx.mock
def test_is_bot_off_true():
    respx.get(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": ["bot-off"]}))
    assert is_bot_off(42) is True

@respx.mock
def test_is_bot_off_false():
    respx.get(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": []}))
    assert is_bot_off(42) is False

@respx.mock
def test_add_label_when_not_present():
    respx.get(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": []}))
    post_route = respx.post(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": ["bot-off"]}))
    add_label(42, "bot-off")
    assert post_route.called

@respx.mock
def test_add_label_skips_if_present():
    respx.get(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={"payload": ["bot-off"]}))
    post_route = respx.post(
        "https://chatwoot.test.com/api/v1/accounts/1/conversations/42/labels"
    ).mock(return_value=httpx.Response(200, json={}))
    add_label(42, "bot-off")
    assert not post_route.called
