import os
import logging
from openai import OpenAI
from agent.prompts import SYSTEM_PROMPT
from agent.tool_handlers import handle_tool

logger = logging.getLogger("odontotec.agent")

MAX_ITERATIONS = 10
_client = None

# OpenAI tool schemas (same logic as Anthropic but different format)
# MODO PRUEBA: las herramientas de reserva (Cal.com) y correo fueron retiradas a
# propósito. El agente NO registra citas en ningún sistema externo hasta que
# Dentidesk esté conectado. Solo persiste nombre/cédula localmente (SQLite) para
# no volver a preguntarlos, transcribe audio y puede escalar.
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_patient",
            "description": "Busca nombre y cédula del paciente en base de datos local por teléfono.",
            "parameters": {
                "type": "object",
                "properties": {"phone": {"type": "string"}},
                "required": ["phone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_patient",
            "description": "Guarda o actualiza nombre y cédula del paciente en base de datos local.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "name": {"type": "string"},
                    "cedula": {"type": "string", "description": "Número de cédula del paciente"}
                },
                "required": ["phone", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "register_appointment",
            "description": "Registra la cita confirmada del paciente. Usar UNA SOLA VEZ en PASO 6, después de que el paciente confirme sus datos en PASO 5.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Nombre completo del paciente"},
                    "patient_phone": {"type": "string", "description": "Teléfono del paciente"},
                    "cedula": {"type": "string", "description": "Cédula del paciente"},
                    "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria"},
                    "day": {"type": "string", "description": "Día de la cita en texto, ej: sábado 27 de junio"},
                    "time": {"type": "string", "description": "Hora de la cita, ej: 10:00 AM"}
                },
                "required": ["patient_name", "patient_phone", "specialty", "day", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_audio",
            "description": "Transcribe nota de voz a texto. Usar cuando el paciente envíe audio.",
            "parameters": {
                "type": "object",
                "properties": {"audio_url": {"type": "string"}},
                "required": ["audio_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Transfiere la conversación a un humano. Usar solo si el paciente lo pide explícitamente o está molesto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "recado|consulta_compleja|queja|otro"},
                    "conversation_id": {"type": "integer"}
                },
                "required": ["reason", "conversation_id"]
            }
        }
    }
]


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        if openrouter_key:
            _client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
        elif openai_key:
            _client = OpenAI(api_key=openai_key)
        else:
            raise RuntimeError("OPENROUTER_API_KEY or OPENAI_API_KEY not set")
    return _client


def run_agent(history: list[dict], conversation_id: int, patient_phone: str = "") -> str:
    import json
    system = (
        SYSTEM_PROMPT
        .replace("{conversation_id}", str(conversation_id))
        .replace("{patient_phone}", patient_phone or "desconocido")
    )
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    if "=" in model:
        raise RuntimeError(
            f"OPENAI_MODEL env var malformed (contains '='): {model!r} — "
            f"editaron la variable pegando 'OPENAI_MODEL=valor' en vez de solo 'valor'"
        )
    logger.info(f"run_agent using model={model!r}")

    for _ in range(MAX_ITERATIONS):
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            timeout=60
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = handle_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
        else:
            return msg.content or ""

    return "Con gusto. Permítame un momento para ayudarle con su solicitud."
