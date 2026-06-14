import os
from supabase import create_client, Client

_client: Client = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )
    return _client

def ensure_patient(phone: str, name: str = None) -> dict:
    data = {"phone": phone}
    if name:
        data["name"] = name
    result = get_client().table("patients").upsert(data).execute()
    return result.data[0]

def get_patient(phone: str) -> dict | None:
    result = get_client().table("patients").select("*").eq("phone", phone).execute()
    return result.data[0] if result.data else None

def save_message(phone: str, role: str, content: str, msg_type: str = "text"):
    ensure_patient(phone)
    get_client().table("messages").insert({
        "patient_phone": phone,
        "role": role,
        "content": content,
        "msg_type": msg_type
    }).execute()

def get_messages(phone: str, limit: int = 20) -> list[dict]:
    result = (get_client().table("messages")
        .select("role,content,msg_type,created_at")
        .eq("patient_phone", phone)
        .order("created_at", desc=True)
        .limit(limit)
        .execute())
    return list(reversed(result.data))

def get_conversation(phone: str) -> dict | None:
    result = (get_client().table("conversations")
        .select("*").eq("patient_phone", phone).execute())
    return result.data[0] if result.data else None

def set_conversation_status(phone: str, status: str):
    get_client().table("conversations").upsert({
        "patient_phone": phone, "status": status
    }).execute()

def increment_recado(phone: str) -> int:
    conv = get_conversation(phone)
    count = (conv["recado_count"] if conv else 0) + 1
    get_client().table("conversations").upsert({
        "patient_phone": phone, "status": "recado", "recado_count": count
    }).execute()
    return count

def save_reminder(cal_uid: str, phone: str, doctor: str, specialty: str, appt_at: str):
    ensure_patient(phone)
    get_client().table("appointment_reminders").upsert({
        "cal_booking_uid": cal_uid,
        "patient_phone": phone,
        "doctor_name": doctor,
        "specialty": specialty,
        "appointment_at": appt_at,
        "status": "pending"
    }).execute()

def get_pending_reminders(date_str: str) -> list[dict]:
    from datetime import date, timedelta
    next_day = (date.fromisoformat(date_str) + timedelta(days=1)).isoformat()
    result = (get_client().table("appointment_reminders")
        .select("*").eq("status", "pending")
        .gte("appointment_at", f"{date_str}T00:00:00")
        .lt("appointment_at", f"{next_day}T00:00:00")
        .execute())
    return result.data

def update_reminder_status(uid: str, status: str):
    get_client().table("appointment_reminders").update(
        {"status": status}
    ).eq("cal_booking_uid", uid).execute()
