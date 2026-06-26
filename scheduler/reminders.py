import os
from datetime import date, timedelta
from integrations import calcom


def _phone_from_email(email: str) -> str:
    return "+" + email.split("@")[0]


def send_nightly_reminders():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    try:
        bookings = calcom.get_upcoming_bookings(tomorrow)
    except Exception as e:
        print(f"[reminders] fetch error: {e}")
        return

    for booking in bookings:
        uid = booking.get("uid")
        attendees = booking.get("attendees", [])
        if not attendees:
            continue

        email = attendees[0].get("email", "")
        if not email.endswith("@odontotec.bot"):
            continue

        phone = _phone_from_email(email)
        name = attendees[0].get("name", "paciente")
        specialty = booking.get("eventType", {}).get("title", "su consulta")
        start = booking.get("startTime", "")
        display_date = start[:10] if len(start) >= 10 else tomorrow
        display_time = start[11:16] if len(start) >= 16 else ""

        msg = (
            f"Buen día/Buenas tardes {name}, soy Carla de Odonto-Tec, le contacto para confirmar "
            f"su cita para el día {display_date} a las {display_time}. Por aquí le esperamos."
        )
        print(f"[reminders] {phone}: {msg}")


def start_scheduler():
    import pytz
    from apscheduler.schedulers.background import BackgroundScheduler

    tz = pytz.timezone(os.getenv("TIMEZONE", "America/Santo_Domingo"))
    hour = int(os.getenv("REMINDER_HOUR", "20"))

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(send_nightly_reminders, "cron", hour=hour, minute=0)
    scheduler.start()
    print(f"[scheduler] reminders scheduled at {hour}:00 {tz}")
    return scheduler
