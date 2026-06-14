import os
import json
import httpx

TIMEZONE = os.getenv("TIMEZONE", "America/Santo_Domingo")


def _headers() -> dict:
    key = os.getenv("CALCOM_API_KEY")
    if not key:
        raise RuntimeError("CALCOM_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "cal-api-version": "2024-09-04"
    }


def _base_url() -> str:
    url = os.getenv("CALCOM_URL")
    if not url:
        raise RuntimeError("CALCOM_URL not set")
    return url


def _event_id(specialty: str) -> int:
    events = json.loads(os.getenv("CALCOM_EVENTS", "{}"))
    eid = events.get(specialty.lower())
    if eid is None:
        raise ValueError(f"Unknown specialty: {specialty}. Valid: {list(events.keys())}")
    return eid


def _check_response(body: dict, context: str) -> dict:
    if body.get("status") != "success":
        msg = body.get("message") or body.get("error") or "unknown error"
        raise RuntimeError(f"Cal.com error in {context}: {msg}")
    return body["data"]


def check_availability(specialty: str, date_from: str, date_to: str) -> dict:
    r = httpx.get(
        f"{_base_url()}/api/v2/slots/available",
        headers=_headers(),
        params={
            "eventTypeId": _event_id(specialty),
            "startTime": f"{date_from}T00:00:00",
            "endTime": f"{date_to}T23:59:59",
            "timeZone": TIMEZONE
        },
        timeout=10
    )
    r.raise_for_status()
    return _check_response(r.json(), "check_availability").get("slots", {})


def book_appointment(patient_phone: str, patient_name: str, specialty: str, start_time: str) -> dict:
    r = httpx.post(
        f"{_base_url()}/api/v2/bookings",
        headers=_headers(),
        json={
            "eventTypeId": _event_id(specialty),
            "start": start_time,
            "attendee": {
                "name": patient_name,
                "email": f"{patient_phone.replace('+', '')}@odontotec.bot",
                "timeZone": TIMEZONE,
                "language": "es"
            }
        },
        timeout=10
    )
    r.raise_for_status()
    return _check_response(r.json(), "book_appointment")


def reschedule_appointment(booking_uid: str, new_start_time: str) -> dict:
    r = httpx.patch(
        f"{_base_url()}/api/v2/bookings/{booking_uid}/reschedule",
        headers=_headers(),
        json={"start": new_start_time},
        timeout=10
    )
    r.raise_for_status()
    return _check_response(r.json(), "reschedule_appointment")


def get_patient_bookings(patient_phone: str) -> list[dict]:
    email = f"{patient_phone.replace('+', '')}@odontotec.bot"
    r = httpx.get(
        f"{_base_url()}/api/v2/bookings",
        headers=_headers(),
        params={"attendeeEmail": email},
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("bookings", [])


def get_upcoming_bookings(date_str: str) -> list[dict]:
    r = httpx.get(
        f"{_base_url()}/api/v2/bookings",
        headers=_headers(),
        params={
            "afterStart": f"{date_str}T00:00:00",
            "beforeEnd": f"{date_str}T23:59:59",
            "status": "upcoming"
        },
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("bookings", [])
