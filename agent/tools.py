TOOLS = [
    {
        "name": "get_patient",
        "description": "Busca información del paciente (nombre, cédula) en base de datos por teléfono.",
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
        "name": "buscar_cita_dentidesk",
        "description": "LECTURA: busca la cita del paciente en la agenda real de Dentidesk para un día, por cédula o teléfono. Usar antes de reagendar_cita_dentidesk para obtener IdAgenda, fecha, paciente y doctor.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_iso": {"type": "string", "description": "Fecha de la cita actual, YYYY-MM-DD"},
                "cedula": {"type": "string"},
                "telefono": {"type": "string"},
                "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
            },
            "required": ["fecha_iso"]
        }
    },
    {
        "name": "agendar_cita_dentidesk",
        "description": "ESCRITURA: crea una cita NUEVA en Dentidesk. Llamar UNA SOLA VEZ, después de que el paciente confirme todos los datos (PASO 5/6 del flujo de nueva cita).",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_name": {"type": "string"},
                "patient_phone": {"type": "string"},
                "specialty": {"type": "string", "description": "general|ortodoncia|endodoncia|cirugia|protesis|odontopediatria"},
                "day": {"type": "string", "description": "Día de la cita en texto, ej: 'jueves 9 de julio'"},
                "time": {"type": "string", "description": "Hora de la cita, ej: '3:00 PM'"},
                "cedula": {"type": "string"},
                "procedimiento": {"type": "string", "description": "El tratamiento en palabras, ej: 'Limpieza dental'"},
                "fecha_iso": {"type": "string", "description": "Fecha de la cita, YYYY-MM-DD"},
                "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
            },
            "required": ["patient_name", "patient_phone", "specialty", "day", "time", "fecha_iso"]
        }
    },
    {
        "name": "reagendar_cita_dentidesk",
        "description": "ESCRITURA: mueve una cita existente a otra fecha/hora en Dentidesk. Llamar UNA SOLA VEZ, después de buscar_cita_dentidesk y de que el paciente confirme los nuevos datos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id_agenda": {"type": "string", "description": "IdAgenda devuelto por buscar_cita_dentidesk"},
                "fecha_actual_iso": {"type": "string", "description": "Fecha actual de la cita (campo 'fecha' de buscar_cita_dentidesk), YYYY-MM-DD"},
                "patient_name": {"type": "string", "description": "Nombre EXACTO del paciente tal cual lo devolvió buscar_cita_dentidesk (campo 'paciente')"},
                "fecha_iso": {"type": "string", "description": "Nueva fecha de la cita, YYYY-MM-DD"},
                "time": {"type": "string", "description": "Nueva hora de la cita, ej: '9:30 AM'"},
                "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"},
                "doctor": {"type": "string", "description": "Campo 'doctor' devuelto por buscar_cita_dentidesk; incluirlo siempre que esté disponible"}
            },
            "required": ["id_agenda", "fecha_actual_iso", "patient_name", "fecha_iso", "time"]
        }
    },
    {
        "name": "confirmar_cita_dentidesk",
        "description": "ESCRITURA: marca una cita existente como Confirmada en Dentidesk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id_agenda": {"type": "string", "description": "IdAgenda de la cita a confirmar"},
                "sucursal": {"type": "string", "description": "arroyo_hondo|naco|haina (default arroyo_hondo)"}
            },
            "required": ["id_agenda"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Transfiere la conversación a un compañero humano. Usar SOLO si: el paciente pide explícitamente hablar con una persona, está molesto, o agendar/reagendar falló con un error distinto de fuera_de_horario/hora_invalida. Nunca por falta de información.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "recado|consulta_compleja|queja|otro"},
                "conversation_id": {"type": "integer"}
            },
            "required": ["reason", "conversation_id"]
        }
    }
]