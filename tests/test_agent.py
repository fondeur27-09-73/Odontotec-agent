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


def test_escalate_prematuro_se_bloquea_y_reintenta_agenda():
    # Bug 2026-07-08: ante reagendar el modelo llamaba escalate_to_human de una, sin tocar la
    # agenda. El guardrail lo bloquea (sin ejecutar handle_tool) y lo empuja a intentar
    # buscar_cita_dentidesk; recién entonces se ejecuta una tool real.
    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool",
               return_value=json.dumps({"found": True, "IdAgenda": "999"})) as mock_tool:
        mock_fn.return_value.chat.completions.create.side_effect = [
            _tool_response("escalate_to_human", {"reason": "otro", "conversation_id": 1}),
            _tool_response("buscar_cita_dentidesk", {"fecha_iso": "2026-07-11"}),
            _text_response("Le confirmo su cita reprogramada."),
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "cambia mi cita"}], 1)
        # escalate_to_human NUNCA se ejecutó; la única tool ejecutada fue la de agenda.
        assert mock_tool.call_count == 1
        assert mock_tool.call_args.args[0] == "buscar_cita_dentidesk"
        assert "confirmo" in result.lower()


def test_escalate_bloqueo_es_persistente_no_solo_una_vez():
    # Si el modelo insiste (escala DOS veces seguidas sin fallo de escritura), el guardrail lo
    # bloquea las dos: nunca ejecuta escalate_to_human. found=false de una lectura NO habilita.
    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool",
               return_value=json.dumps({"found": False})) as mock_tool:
        mock_fn.return_value.chat.completions.create.side_effect = [
            _tool_response("escalate_to_human", {"reason": "otro", "conversation_id": 1}),
            _tool_response("buscar_cita_dentidesk", {"fecha_iso": "2026-07-21"}),
            _tool_response("escalate_to_human", {"reason": "consulta_compleja", "conversation_id": 1}),
            _text_response("¿Me indica la fecha de su cita actual, por favor?"),
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "quiero mover mi cita"}], 1)
        # Solo se ejecutó la lectura; ninguno de los dos escalados pasó.
        assert mock_tool.call_count == 1
        assert mock_tool.call_args.args[0] == "buscar_cita_dentidesk"
        assert "fecha" in result.lower()


def test_escalate_permitido_si_paciente_pide_humano_explicito():
    # Excepción legítima: el paciente pide hablar con una persona → escalate SÍ se ejecuta de una.
    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool",
               return_value=json.dumps({"success": True, "escalated": True})) as mock_tool:
        mock_fn.return_value.chat.completions.create.side_effect = [
            _tool_response("escalate_to_human", {"reason": "otro", "conversation_id": 1}),
            _text_response("Con gusto, la comunico con una compañera."),
        ]
        from agent.claude import run_agent
        result = run_agent(
            [{"role": "user", "content": "no quiero un bot, quiero hablar con una persona"}], 1)
        assert mock_tool.call_count == 1
        assert mock_tool.call_args.args[0] == "escalate_to_human"


def test_escalate_permitido_tras_intento_real_de_agenda():
    # Si YA hubo un intento de agenda (aquí falló), el escalado legítimo (GUION F) SÍ se ejecuta.
    with patch("agent.claude._get_client") as mock_fn, \
         patch("agent.claude.handle_tool") as mock_tool:
        mock_tool.side_effect = [
            json.dumps({"success": False, "error": "guardar_cita_fallo"}),
            json.dumps({"success": True, "escalated": True}),
        ]
        mock_fn.return_value.chat.completions.create.side_effect = [
            _tool_response("reagendar_cita_dentidesk",
                           {"id_agenda": "1", "fecha_actual_iso": "2026-07-10",
                            "patient_name": "Ulises", "fecha_iso": "2026-07-11", "time": "11:30"}),
            _tool_response("escalate_to_human", {"reason": "otro", "conversation_id": 1}),
            _text_response("Una compañera le confirmará su cita en unos minutos."),
        ]
        from agent.claude import run_agent
        result = run_agent([{"role": "user", "content": "cambia mi cita"}], 1)
        # Ambas tools se ejecutaron: el intento real y, tras el fallo, el escalado.
        assert mock_tool.call_count == 2
        assert mock_tool.call_args_list[1].args[0] == "escalate_to_human"
        assert "compañera" in result
