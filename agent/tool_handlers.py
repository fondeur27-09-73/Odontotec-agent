import json
from integrations import calcom, supabase_client, chatwoot
from utils.audio import transcribe_audio as _transcribe


def handle_tool(tool_name: str, tool_input: dict) -> str:
    handlers = {
        "check_availability": _check_availability,
        "book_appointment": _book_appointment,
        "reschedule_appointment": _reschedule_appointment,
        "get_patient_appointments": _get_patient_appointments,
        "get_patient": _get_patient,
        "save_patient": _save_patient,
        "escalate_to_human": _escalate_to_human,
        "transcribe_audio": _transcribe_audio,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        return json.dumps(handler(**tool_input), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _check_availability(specialty: str, date_from: str, date_to: str) -> dict:
    return {"slots": calcom.check_availability(specialty, date_from, date_to)}


def _book_appointment(patient_phone: str, patient_name: str, specialty: str, start_time: str) -> dict:
    supabase_client.ensure_patient(patient_phone, patient_name)
    booking = calcom.book_appointment(patient_phone, patient_name, specialty, start_time)
    supabase_client.save_reminder(
        cal_uid=booking["uid"],
        phone=patient_phone,
        doctor=booking.get("hosts", [{}])[0].get("name", "") if booking.get("hosts") else "",
        specialty=specialty,
        appt_at=booking["startTime"]
    )
    return {"success": True, "booking_uid": booking["uid"], "start_time": booking["startTime"]}


def _reschedule_appointment(booking_uid: str, new_start_time: str) -> dict:
    booking = calcom.reschedule_appointment(booking_uid, new_start_time)
    supabase_client.update_reminder_status(booking_uid, "rescheduled")
    return {"success": True, "new_start_time": booking["startTime"]}


def _get_patient_appointments(patient_phone: str) -> dict:
    return {"bookings": calcom.get_patient_bookings(patient_phone)}


def _get_patient(phone: str) -> dict:
    return supabase_client.get_patient(phone) or {"found": False}


def _save_patient(phone: str, name: str) -> dict:
    return {"success": True, "patient": supabase_client.ensure_patient(phone, name)}


def _escalate_to_human(reason: str, conversation_id: int) -> dict:
    chatwoot.add_label(conversation_id, "bot-off")
    return {"success": True, "escalated": True}


def _transcribe_audio(audio_url: str) -> dict:
    return {"transcription": _transcribe(audio_url)}
