import os
from openai import OpenAI
from agent.prompts import SYSTEM_PROMPT
from agent.tool_handlers import handle_tool

MAX_ITERATIONS = 10
_client = None

# OpenAI tool schemas (same logic as Anthropic but different format)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Verifica slots disponibles para una especialidad dental en un rango de fechas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis"},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"}
                },
                "required": ["specialty", "date_from", "date_to"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Reserva una cita para el paciente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_phone": {"type": "string"},
                    "patient_name": {"type": "string"},
                    "specialty": {"type": "string"},
                    "start_time": {"type": "string", "description": "ISO 8601: 2026-06-15T08:30:00.000Z"}
                },
                "required": ["patient_phone", "patient_name", "specialty", "start_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Reagenda una cita existente. NUNCA cancela, siempre mueve hacia adelante.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_uid": {"type": "string"},
                    "new_start_time": {"type": "string", "description": "ISO 8601"}
                },
                "required": ["booking_uid", "new_start_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient_appointments",
            "description": "Obtiene citas activas del paciente.",
            "parameters": {
                "type": "object",
                "properties": {"patient_phone": {"type": "string"}},
                "required": ["patient_phone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient",
            "description": "Busca información del paciente en base de datos.",
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
            "description": "Guarda o actualiza nombre del paciente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "name": {"type": "string"}
                },
                "required": ["phone", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Transfiere la conversación a Carla o Helen. Usar cuando no puedas resolver.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "recado|consulta_compleja|queja|otro"},
                    "conversation_id": {"type": "integer"}
                },
                "required": ["reason", "conversation_id"]
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
    }
]


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=key)
    return _client


def run_agent(history: list[dict], conversation_id: int) -> str:
    import json
    messages = [{"role": "system", "content": SYSTEM_PROMPT.replace("{conversation_id}", str(conversation_id))}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    for _ in range(MAX_ITERATIONS):
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto"
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

    return "Disculpe, hubo un inconveniente. Un momento, le comunicamos con un agente."
