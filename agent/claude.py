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
                    "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria"},
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
            "description": "Guarda o actualiza nombre y cédula del paciente.",
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
    },
    {
        "type": "function",
        "function": {
            "name": "send_confirmation_email",
            "description": "Envía email de confirmación de cita al paciente. Usar SIEMPRE después de book_appointment o reschedule_appointment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string"},
                    "patient_phone": {"type": "string"},
                    "specialty": {"type": "string"},
                    "start_time": {"type": "string", "description": "ISO 8601"},
                    "booking_uid": {"type": "string"},
                    "is_reschedule": {"type": "boolean", "description": "True si es reagenda, False si es cita nueva"},
                    "old_start_time": {"type": "string", "description": "ISO 8601 — fecha/hora anterior (solo para reagendas)"}
                },
                "required": ["patient_name", "patient_phone", "specialty", "start_time", "booking_uid"]
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


def run_agent(history: list[dict], conversation_id: int) -> str:
    import json
    messages = [{"role": "system", "content": SYSTEM_PROMPT.replace("{conversation_id}", str(conversation_id))}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    booked = None       # cache del primer book_appointment exitoso (anti-duplicado)
    force_final = False  # tras enviar correo, forzar mensaje de cierre (anti-bucle)

    for _ in range(MAX_ITERATIONS):
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="none" if force_final else "auto",
            timeout=60
        )

        msg = response.choices[0].message

        if msg.tool_calls and not force_final:
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                name = tc.function.name
                if name == "book_appointment" and booked is not None:
                    # Idempotente: ya se reservó con éxito, no reservar de nuevo.
                    result = booked
                else:
                    result = handle_tool(name, args)
                    if name == "book_appointment":
                        try:
                            if json.loads(result).get("success"):
                                booked = result
                        except Exception:
                            pass
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
                # Tras el correo, el único paso restante es el cierre: forzar texto.
                if name == "send_confirmation_email":
                    force_final = True
        else:
            return msg.content or ""

    return "Su solicitud quedó registrada. En breve le confirmamos los detalles de su cita."
