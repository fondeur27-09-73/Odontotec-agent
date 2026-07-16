import os
import logging
from datetime import datetime
from openai import OpenAI
from agent.prompts import SYSTEM_PROMPT
from agent.tool_handlers import handle_tool
from agent import metrics

logger = logging.getLogger("odontotec.agent")

MAX_ITERATIONS = 10
_client = None

# DECISIÓN DEL CLIENTE 2026-07-09: escalate_to_human (que pone bot-off y silencia a Carla) SOLO se
# ejecuta si el paciente pide EXPLÍCITAMENTE hablar con una persona real. Un fallo técnico, un
# found=false o un mensaje ambiguo (ej. "Wasapp") NO habilitan escalar — Carla debe INSISTIR en
# cerrar la cita (reintentar agendar/reagendar), nunca poner bot-off por un tropiezo. Ver guardrail
# en run_agent.
_HUMAN_REQUEST_HINTS = (
    "una persona", "con alguien", "un humano", "una humana", "persona real", "ser humano",
    "hablar con alguien", "operador", "operadora", "recepcionista", "un agente humano",
    "gerente", "encargad", "quiero hablar con", "necesito hablar con", "comunicarme con un",
    "atienda una persona", "que me llame", "hablar con la clinica", "hablar con un doctor",
)


def _norm_es(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", text or "").encode("ascii", "ignore").decode().lower()


def _wants_human(text: str) -> bool:
    t = _norm_es(text)
    return any(h in t for h in _HUMAN_REQUEST_HINTS)

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
            "description": "Mueve (reagenda) una cita EXISTENTE de Dentidesk a otra fecha/hora, y opcionalmente cambia el tratamiento. Requiere el IdAgenda, la fecha ACTUAL, el nombre del paciente y el doctor actual, todos obtenidos con buscar_cita_dentidesk/buscar_cita_proxima_dentidesk. Usar UNA SOLA VEZ tras confirmar los nuevos datos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_agenda": {"type": "string", "description": "IdAgenda de la cita a mover (de buscar_cita_dentidesk)"},
                    "fecha_actual_iso": {"type": "string", "description": "Fecha ACTUAL de la cita antes de moverla, YYYY-MM-DD (de buscar_cita_dentidesk, campo 'fecha')"},
                    "patient_name": {"type": "string", "description": "Nombre completo del paciente (de buscar_cita_dentidesk), para ubicar la tarjeta en la agenda"},
                    "doctor": {"type": "string", "description": "Doctor ACTUAL de la cita (de buscar_cita_dentidesk, campo 'doctor'). Necesario para que la agenda muestre la tarjeta correcta; enviar siempre que buscar_cita_dentidesk lo haya devuelto."},
                    "fecha_iso": {"type": "string", "description": "Nueva fecha en formato YYYY-MM-DD"},
                    "time": {"type": "string", "description": "Nueva hora, ej: 10:00 AM"},
                    "specialty": {"type": "string", "description": "SOLO si el paciente cambia de tratamiento: la NUEVA especialidad del sistema (general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria). Cambia el doctor de la cita al de esa especialidad. Omitir si no cambia el tratamiento."},
                    "procedimiento": {"type": "string", "description": "SOLO si el paciente cambia de tratamiento: el NUEVO tratamiento en palabras (ej 'Limpieza dental'). Omitir si no cambia el tratamiento."},
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
            "description": "LECTURA de la agenda real de Dentidesk. Busca si el paciente ya tiene una cita en un día concreto, por cédula, teléfono o nombre. Úsalo para saber si es paciente recurrente o ver los datos de su cita. IMPORTANTE: si la cita es para OTRA persona (un familiar), el teléfono/cédula del que escribe NO va a coincidir — pasa SIEMPRE el 'nombre' completo del paciente (nombre y apellido) para poder encontrarla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_iso": {"type": "string", "description": "Fecha a consultar en formato YYYY-MM-DD"},
                    "cedula": {"type": "string", "description": "Cédula del paciente (opcional)"},
                    "telefono": {"type": "string", "description": "Teléfono del paciente (opcional)"},
                    "nombre": {"type": "string", "description": "Nombre y apellido del paciente (opcional). Imprescindible para citas de terceros/familiares. Requiere al menos nombre + apellido."},
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
            "description": "LECTURA: busca la PRÓXIMA cita del paciente en la agenda real de Dentidesk SIN necesitar la fecha exacta (escanea desde hoy hacia adelante por teléfono, cédula o nombre). Úsalo para reagendar cuando NO sabes el día de la cita actual del paciente. IMPORTANTE: si la cita es para OTRA persona (un familiar), el teléfono/cédula del que escribe NO va a coincidir — en ese caso pide y pasa el 'nombre' completo del paciente (nombre y apellido) para encontrarla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cedula": {"type": "string", "description": "Cédula del paciente (opcional)"},
                    "telefono": {"type": "string", "description": "Teléfono del paciente (opcional)"},
                    "nombre": {"type": "string", "description": "Nombre y apellido del paciente (opcional). Fallback para citas de terceros/familiares. Requiere al menos nombre + apellido."},
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
            "description": "Transfiere la conversación a un humano (pone la conversación en modo humano y Carla deja de responder). Usar ÚNICAMENTE si el paciente pide EXPLÍCITAMENTE hablar con una persona real. NUNCA por fallos técnicos, errores al registrar, falta de datos, ni mensajes ambiguos: en esos casos NO se escala, se insiste en cerrar la cita.",
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

    # GUARDRAIL (bugs 2026-07-08/09): gpt-5.5 llamaba escalate_to_human como escape ante
    # reagendar/agendar, o ante un fallo técnico (ej. 502 del daemon), o ante un mensaje ambiguo
    # (ej. "Wasapp") — poniendo bot-off y silenciando a Carla sin registrar la cita. Reforzar el
    # prompt no bastó. DECISIÓN DEL CLIENTE 2026-07-09: escalate_to_human SOLO se ejecuta si el
    # paciente pide EXPLÍCITAMENTE hablar con una persona real. En cualquier otro caso (fallo
    # técnico, found=false, mensaje raro) NO se escala: se bloquea PERSISTENTEMENTE y se devuelve una
    # corrección para que Carla INSISTA en cerrar la cita (reintentar). Si insiste 10 iteraciones,
    # cae al mensaje neutro de abajo — que NO es bot-off ni confirmación en falso.
    last_user = next((m.get("content") or "" for m in reversed(history)
                      if m.get("role") == "user"), "")
    patient_wants_human = _wants_human(last_user)

    for _ in range(MAX_ITERATIONS):
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            timeout=60,
            max_tokens=8192
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                name = tc.function.name
                # Escalado permitido SOLO si el paciente pidió humano explícitamente. Si no, no se
                # ejecuta: se responde el tool_call con una corrección (todo tool_call DEBE recibir un
                # tool-result o la API rechaza el siguiente request) y el modelo insiste en la cita.
                if name == "escalate_to_human" and not patient_wants_human:
                    logger.info(
                        f"run_agent conv={conversation_id}: escalate_to_human BLOQUEADO "
                        f"(el paciente no pidió un humano explícitamente — Carla debe insistir)"
                    )
                    metrics.increment("escalate_blocked")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "error": "escalado_no_permitido",
                            "message": (
                                "NO escales y NO pongas bot-off. El paciente NO pidió hablar con una "
                                "persona. Ante cualquier tropiezo (un error al registrar, un mensaje "
                                "confuso, o no saber un dato) NO se transfiere a un humano: INSISTE en "
                                "cerrar la cita. Si agendar/reagendar falló, discúlpate en una línea y "
                                "vuelve a intentarlo (reconfirma los datos y llama la tool otra vez). "
                                "Si falta un dato, pídelo. Si el mensaje del paciente no requiere "
                                "acción (ej. 'ok', 'gracias', 'whatsapp'), responde con cortesía y "
                                "cierra. Solo se escala si el paciente dice EXPLÍCITAMENTE que quiere "
                                "hablar con una persona real."
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
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
        else:
            text = (msg.content or "").strip()
            # Un modelo de razonamiento (gpt-5.5) puede agotar max_tokens PENSANDO y devolver
            # content vacío con finish_reason='length': sin excepción y sin error. Devolver "" ahí
            # dejaba al paciente sin respuesta y sin rastro en logs — el mismo silencio del 402.
            # Que reviente: _process_message lo loguea y le avisa al paciente.
            if not text:
                raise RuntimeError(
                    f"el modelo {model!r} no devolvió texto "
                    f"(finish_reason={response.choices[0].finish_reason!r}) — "
                    f"si es 'length', max_tokens se agotó razonando"
                )
            return text

    # Llegar aquí = MAX_ITERATIONS sin que el modelo cerrara nada. El paciente recibe una frase
    # neutra y por fuera parece atendido; el contador es lo único que lo delata.
    metrics.increment("iterations_exhausted")
    return "Con gusto. Permítame un momento para ayudarle con su solicitud."
