import json
from unittest.mock import patch


def test_check_availability_returns_slots():
    with patch("integrations.calcom.check_availability",
               return_value={"2026-06-15": [{"time": "2026-06-15T08:30:00.000Z"}]}):
        from agent.tool_handlers import handle_tool
        result = json.loads(handle_tool("check_availability", {
            "specialty": "general", "date_from": "2026-06-15", "date_to": "2026-06-16"
        }))
        assert "2026-06-15" in result["slots"]


def test_escalate_adds_bot_off_label():
    with patch("integrations.chatwoot.add_label") as mock_label, \
         patch("integrations.chatwoot.get_labels", return_value=[]):
        from agent.tool_handlers import handle_tool
        result = json.loads(handle_tool("escalate_to_human", {
            "reason": "recado", "conversation_id": 42
        }))
        mock_label.assert_called_once_with(42, "bot-off")
        assert result["success"] is True


def test_unknown_tool_returns_error():
    from agent.tool_handlers import handle_tool
    result = json.loads(handle_tool("nonexistent", {}))
    assert "error" in result


def test_save_patient_calls_ensure():
    with patch("integrations.supabase_client.ensure_patient",
               return_value={"phone": "+18491234567", "name": "Juan"}) as mock_ensure:
        from agent.tool_handlers import handle_tool
        result = json.loads(handle_tool("save_patient", {"phone": "+18491234567", "name": "Juan"}))
        mock_ensure.assert_called_once_with("+18491234567", "Juan")
        assert result["success"] is True
