import os
import json
from unittest.mock import patch, MagicMock

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")


def _text_response(text: str):
    """Respuesta estilo OpenAI chat.completions sin tool calls."""
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = text
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def _tool_response(name: str, arguments: dict):
    """Respuesta estilo OpenAI chat.completions con un tool call."""
    tc = MagicMock()
    tc.id = "t1"
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    msg = MagicMock()
    msg.tool_calls = [tc]
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def test_run_agent_returns_text():
    with patch("agent.claude._get_client") as mock_fn:
        mock_fn.return_value.chat.completions.create.return_value = \
            _text_response("Hola, soy Carla.")
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "Hola"}], 42)
        assert "Carla" in result


def test_run_agent_executes_tool():
    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool",
               return_value=json.dumps({"name": "Juan"})) as mock_tool:
        mock_fn.return_value.chat.completions.create.side_effect = [
            _tool_response("get_patient", {"phone": "+18491234567"}),
            _text_response("Hola Juan."),
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "Hola"}], 42)
        mock_tool.assert_called_once_with("get_patient", {"phone": "+18491234567"})
        assert "Juan" in result


def test_run_agent_argumentos_malformados_no_tumban_el_turno():
    # El modelo puede mandar JSON roto en function.arguments: se devuelve como error de
    # tool y la conversación sigue, en vez de excepción y paciente sin respuesta.
    tc = MagicMock()
    tc.id = "t1"
    tc.function.name = "get_patient"
    tc.function.arguments = "{esto no es json"
    msg = MagicMock()
    msg.tool_calls = [tc]
    broken = MagicMock()
    broken.choices = [MagicMock(message=msg)]

    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool") as mock_tool:
        mock_fn.return_value.chat.completions.create.side_effect = [
            broken, _text_response("¿Me repite por favor?"),
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "Hola"}], 42)
        mock_tool.assert_not_called()
        assert "repite" in result


def test_save_patient_tool_schema_includes_cedula():
    from agent.claude import OPENAI_TOOLS
    save_patient_tool = next(t for t in OPENAI_TOOLS if t["function"]["name"] == "save_patient")
    assert "cedula" in save_patient_tool["function"]["parameters"]["properties"]
