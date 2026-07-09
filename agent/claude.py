import os
import logging
from datetime import datetime
from openai import OpenAI
from agent.prompts import SYSTEM_PROMPT
from agent.tool_handlers import handle_tool

logger = logging.getLogger("odontotec.agent")

MAX_ITERATIONS = 10
_client = None

# Tools de ESCRITURA a la agenda real (Dentidesk). Solo un FALLO de una de estas (o del paciente
# pidiendo humano / queja) habilita escalate_to_human — ver guardrail en run_agent. La lectura
# (buscar_cita_dentidesk) NO habilita escalar: un found=false significa "pregunta la fecha al
# paciente / reintenta", no "transfiere a un humano".
_WRITE_TOOLS = {
    "agendar_cita_dentidesk",
    "reagendar_cita_dentidesk",
    "confirmar_cita_dentidesk",
}

# Frases con las que el paciente pide EXPLÍCITAMENTE un humano (única vía, junto a queja / fallo
# real, para que escalate_to_human se ejecute sin que antes haya un intento de agenda que falló).
_HUMAN_REQUEST_HINTS = (
    "una persona", "con alguien", "un humano", "una humana", "persona real", "ser humano",
    "hablar con alguien", "operador", "operadora", "recepcionista", "un agente humano",
    "gerente", "encargad", "quiero hablar con", "necesito hablar con", "comunicarme con un",
)


def _norm_es(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", text or "").encode("ascii", "ignore").decode().lower()


def _wants_human(text: str) -> bool:
    t = _norm_es(text)
    return any(h in t for h in _HUMAN_REQUEST_HINTS)


def _tool_reason(tc) -> str:
    import json
    try:
        return (json.loads(tc.function.arguments) or {}).get("reason", "")
    except Exception:
        return ""


def _is_write_failure(name: str, result: str) -> bool:
    """True si una tool de ESCRITURA devolvió un error real (success=false / 'error' / basura).
    Eso —y solo eso— es el disparador legítimo de GUION F + escalado."""
    import json
    if name not in _WRITE_TOOLS:
        return False
    try:
        parsed = json.loads(result)
    except Exception:
        return True  # respuesta inparseable de una escritura = tratarla como fallo
    return isinstance(parsed, dict) and (parsed.get("success") is False or "error" in parsed)

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
            "name": "agendar_cita_dentidesk",
            "description": "Crea una cita NUEVA en Dentidesk. Usar UNA SOLA VEZ en PASO 6, después de que el paciente confirme sus datos en PASO 5.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Nombre completo del paciente"},
                    "patient_phone": {"type": "string", "description": "Teléfono del paciente"},
                    "cedula": {"type": "string", "description": "Cédula del paciente"},
                    "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria"},
                    "procedimiento": {"type": "string", "description": "Tratamiento concreto que pidió el paciente, en palabras: ej 'Limpieza dental', 'Extracción de muela', 'Tratamiento de canal', 'Brackets'"},
                    "day": {"type": "string", "description": "Día de la cita en texto, ej: sábado 27 de junio"},
                    "time": {"type": "string", "description": "Hora de la cita, ej: 10:00 AM"},
                    "fecha_iso": {"type": "string", "description": "Fecha de la cita en formato ISO YYYY-MM-DD, calculada a partir de la fecha de hoy indicada en el prompt. Ej: 2026-06-29"},
                    "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
                },
                "required": ["patient_name", "patient_phone", "specialty", "procedimiento", "day", "time", "fecha_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reagendar_cita_dentidesk",
            "description": "Mueve (reagenda) una cita EXISTENTE de Dentidesk a otra fecha/hora. Requiere el IdAgenda, la fecha ACTUAL, el nombre del paciente y el doctor, todos obtenidos con buscar_cita_dentidesk. Usar UNA SOLA VEZ tras confirmar los nuevos datos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_agenda": {"type": "string", "description": "IdAgenda de la cita a mover (de buscar_cita_dentidesk)"},
                    "fecha_actual_iso": {"type": "string", "description": "Fecha ACTUAL de la cita antes de moverla, YYYY-MM-DD (de buscar_cita_dentidesk, campo 'fecha')"},
                    "patient_name": {"type": "string", "description": "Nombre completo del paciente (de buscar_cita_dentidesk), para ubicar la tarjeta en la agenda"},
                    "doctor": {"type": "string", "description": "Doctor asignado a la cita (de buscar_cita_dentidesk, campo 'doctor'). Necesario para que la agenda muestre la tarjeta correcta; enviar siempre que buscar_cita_dentidesk lo haya devuelto."},
                    "fecha_iso": {"type": "string", "description": "Nueva fecha en formato YYYY-MM-DD"},
                    "time": {"type": "string", "description": "Nueva hora, ej: 10:00 AM"},
                    "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina"}
                },
                "required": ["id_agenda", "fecha_actual_iso", "patient_name", "fecha_iso", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_cita_dentidesk",
            "description": "LECTURA de la agenda real de Dentidesk. Busca si el paciente ya tiene una cita en un día concreto, por cédula o teléfono. Úsalo para saber si es paciente recurrente o ver los datos de su cita.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_iso": {"type": "string", "description": "Fecha a consultar en formato YYYY-MM-DD"},
                    "cedula": {"type": "string", "description": "Cédula del paciente (opcional)"},
                    "telefono": {"type": "string", "description": "Teléfono del paciente (opcional)"},
                    "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
                },
                "required": ["fecha_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_cita_proxima_dentidesk",
            "description": "LECTURA: busca la PRÓXIMA cita del paciente en la agenda real de Dentidesk SIN necesitar la fecha exacta (escanea desde hoy hacia adelante por cédula o teléfono). Úsalo para reagendar cuando NO sabes el día de la cita actual del paciente, para no tener que preguntárselo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cedula": {"type": "string", "description": "Cédula del paciente (opcional)"},
                    "telefono": {"type": "string", "description": "Teléfono del paciente (opcional)"},
                    "dias": {"type": "integer", "description": "Cuántos días hacia adelante escanear desde hoy (default 30, máx 60)"},
                    "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirmar_cita_dentidesk",
            "description": "ESCRITURA: marca una cita existente de Dentidesk como Confirmada. Requiere el IdAgenda obtenido con buscar_cita_dentidesk. Solo en producción autorizada.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_agenda": {"type": "string", "description": "IdAgenda de la cita a confirmar"},
                    "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina"}
                },
                "required": ["id_agenda"]
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


def _today_str() -> str:
    """Fecha de hoy en la zona horaria de la clínica, ej: 'jueves 26 de junio de 2026 (2026-06-26)'."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "America/Santo_Domingo")))
    except Exception:
        now = datetime.now()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{dias[now.weekday()]} {now.day} de {meses[now.month]} de {now.year} ({now:%Y-%m-%d})"


def run_agent(history: list[dict], conversation_id: int, patient_phone: str = "") -> str:
    import json
    system = (
        SYSTEM_PROMPT
        .replace("{conversation_id}", str(conversation_id))
        .replace("{patient_phone}", patient_phone or "desconocido")
        .replace("{today}", _today_str())
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

    # GUARDRAIL (bug 2026-07-08): con gpt-5.5, ante reagendar/agendar —incluso solo cambiando la
    # FECHA, o cambiando el tratamiento— el modelo llamaba escalate_to_human como escape, sin
    # intentar siquiera buscar_cita_dentidesk/agendar/reagendar (o sin pedir al paciente la fecha de
    # su cita actual). Reforzar el prompt NO lo corrigió (2 intentos). Aquí se bloquea el escalado
    # PERSISTENTEMENTE (no solo una vez): escalate_to_human solo se ejecuta si ya hubo un FALLO real
    # de una escritura (GUION F legítimo), el paciente pidió humano explícitamente, o marcó queja.
    # Mientras no, cada intento de escalar se responde con una corrección y el modelo debe seguir el
    # flujo (buscar / agendar / o preguntar la fecha). Si insiste 10 iteraciones, cae al mensaje
    # neutro de abajo (nunca transfiere ni confirma en falso). La lectura found=false NO habilita.
    last_user = next((m.get("content") or "" for m in reversed(history)
                      if m.get("role") == "user"), "")
    patient_wants_human = _wants_human(last_user)
    write_failed = False

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
                name = tc.function.name
                # Escalado prematuro: NO se ejecuta escalate_to_human salvo señal legítima. Se
                # responde el tool_call con una corrección (todo tool_call DEBE recibir un tool-result
                # o la API rechaza el siguiente request), y el modelo reintenta el flujo correcto.
                escalado_legitimo = (
                    write_failed or patient_wants_human or _tool_reason(tc) == "queja"
                )
                if name == "escalate_to_human" and not escalado_legitimo:
                    logger.info(
                        f"run_agent conv={conversation_id}: escalate_to_human BLOQUEADO "
                        f"(escalado prematuro, sin fallo de escritura ni pedido de humano)"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "error": "escalado_no_permitido",
                            "message": (
                                "NO escales. El paciente quiere agendar/reagendar/cambiar una cita, "
                                "y eso NO se transfiere a un humano. Sigue el flujo: para reagendar, "
                                "llama buscar_cita_dentidesk con la fecha de la cita ACTUAL para "
                                "obtener el IdAgenda; si no sabes esa fecha, PREGÚNTASELA al paciente "
                                "con cortesía (no escales por no tenerla). Para una cita nueva, llama "
                                "agendar_cita_dentidesk. Un cambio de tratamiento es una reagenda "
                                "normal. Solo puedes escalar si el paciente pide explícitamente "
                                "hablar con una persona, o si una escritura ya devolvió un error real."
                            ),
                        }, ensure_ascii=False),
                    })
                    continue
                # args malformados del modelo no deben tumbar el turno entero (el paciente se
                # quedaba sin respuesta): se devuelven como error de tool y el modelo corrige.
                try:
                    args = json.loads(tc.function.arguments)
                    result = handle_tool(name, args)
                except (json.JSONDecodeError, TypeError) as e:
                    result = json.dumps({"error": f"argumentos inválidos: {e}"})
                result = str(result)
                if _is_write_failure(name, result):
                    write_failed = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result
                })
        else:
            return msg.content or ""

    return "Con gusto. Permítame un momento para ayudarle con su solicitud."
