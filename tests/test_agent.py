import os
import json
from unittest.mock import patch, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


def _text_response(text: str):
    m = MagicMock()
    m.stop_reason = "end_turn"
    b = MagicMock()
    b.text = text
    m.content = [b]
    return m


def test_run_agent_returns_text():
    with patch("agent.claude._get_client") as mock_fn:
        mock_fn.return_value.messages.create.return_value = _text_response("Hola, soy Carla.")
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "Hola"}], 42)
        assert "Carla" in result


def test_run_agent_executes_tool():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "t1"
    tool_block.name = "get_patient"
    tool_block.input = {"phone": "+18491234567"}

    tool_resp = MagicMock()
    tool_resp.stop_reason = "tool_use"
    tool_resp.content = [tool_block]

    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool",
               return_value=json.dumps({"name": "Juan"})) as mock_tool:
        mock_fn.return_value.messages.create.side_effect = [
            tool_resp, _text_response("Hola Juan.")
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "Hola"}], 42)
        mock_tool.assert_called_once_with("get_patient", {"phone": "+18491234567"})
        assert "Juan" in result
