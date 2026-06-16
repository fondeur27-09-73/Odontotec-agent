TOOLS = [
    {
        "name": "check_availability",
        "description": "Verifica slots disponibles para una especialidad dental en un rango de fechas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"}
            },
            "required": ["specialty", "date_from", "date_to"]
        }
    },
    {
        "name": "book_appointment",
        "description": "Reserva una cita para el paciente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_phone": {"type": "string"},
                "patient_name": {"type": "string"},
                "specialty": {"type": "string"},
                "start_time": {"type": "string", "description": "ISO 8601: 2026-06-15T08:30:00.000Z"}
            },
            "required": ["patient_phone", "patient_name", "specialty", "start_time"]
        }
    },
    {
        "name": "reschedule_appointment",
        "description": "Reagenda una cita existente. NUNCA cancela, siempre mueve hacia adelante.",
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_uid": {"type": "string"},
                "new_start_time": {"type": "string", "description": "ISO 8601"}
            },
            "required": ["booking_uid", "new_start_time"]
        }
    },
    {
        "name": "get_patient_appointments",
        "description": "Obtiene citas activas del paciente.",
        "input_schema": {
            "type": "object",
            "properties": {"patient_phone": {"type": "string"}},
            "required": ["patient_phone"]
        }
    },
    {
        "name": "get_patient",
        "description": "Busca información del paciente en base de datos.",
        "input_schema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "required": ["phone"]
        }
    },
    {
        "name": "save_patient",
        "description": "Guarda o actualiza nombre y cédula del paciente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "name": {"type": "string"},
                "cedula": {"type": "string", "description": "Número de cédula del paciente"}
            },
            "required": ["phone", "name"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Transfiere la conversación a Carla o Helen. Usar cuando no puedas resolver.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "recado|consulta_compleja|queja|otro"},
                "conversation_id": {"type": "integer"}
            },
            "required": ["reason", "conversation_id"]
        }
    },
    {
        "name": "transcribe_audio",
        "description": "Transcribe nota de voz a texto. Usar cuando el paciente envíe audio.",
        "input_schema": {
            "type": "object",
            "properties": {"audio_url": {"type": "string"}},
            "required": ["audio_url"]
        }
    },
    {
        "name": "send_confirmation_email",
        "description": "Envía email de confirmación de cita al paciente. Usar SIEMPRE después de book_appointment o reschedule_appointment.",
        "input_schema": {
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
]
