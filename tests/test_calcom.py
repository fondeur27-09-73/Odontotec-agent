import respx
import httpx
import os
import json

os.environ.setdefault("CALCOM_URL", "https://calcom.test.com")
os.environ.setdefault("CALCOM_API_KEY", "test-key")
os.environ.setdefault("CALCOM_EVENTS", '{"general":1,"ortodoncia":2}')
os.environ.setdefault("TIMEZONE", "America/Santo_Domingo")

from integrations.calcom import check_availability, book_appointment, reschedule_appointment, get_patient_bookings

@respx.mock
def test_check_availability():
    respx.get("https://calcom.test.com/api/v2/slots/available").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "data": {"slots": {"2026-06-15": [{"time": "2026-06-15T08:30:00.000Z"}]}}
        })
    )
    result = check_availability("general", "2026-06-15", "2026-06-16")
    assert "2026-06-15" in result

@respx.mock
def test_book_appointment():
    respx.post("https://calcom.test.com/api/v2/bookings").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "data": {"uid": "abc123", "startTime": "2026-06-15T08:30:00.000Z"}
        })
    )
    result = book_appointment("+18491234567", "Juan", "general", "2026-06-15T08:30:00.000Z")
    assert result["uid"] == "abc123"

@respx.mock
def test_reschedule_appointment():
    respx.patch("https://calcom.test.com/api/v2/bookings/abc123/reschedule").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "data": {"uid": "abc123", "startTime": "2026-06-16T10:00:00.000Z"}
        })
    )
    result = reschedule_appointment("abc123", "2026-06-16T10:00:00.000Z")
    assert result["uid"] == "abc123"

@respx.mock
def test_unknown_specialty_raises():
    with __import__("pytest").raises(ValueError, match="Unknown specialty"):
        check_availability("desconocida", "2026-06-15", "2026-06-16")
