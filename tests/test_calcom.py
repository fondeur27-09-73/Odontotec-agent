import pytest
import respx
import httpx
import os

os.environ.setdefault("CALCOM_URL", "https://calcom.test.com")
os.environ.setdefault("CALCOM_API_KEY", "test-key")
os.environ.setdefault("CALCOM_EVENTS", '{"general":1,"ortodoncia":2}')
os.environ.setdefault("TIMEZONE", "America/Santo_Domingo")

from integrations.calcom import (
    check_availability, book_appointment, reschedule_appointment,
    get_patient_bookings, get_upcoming_bookings
)


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


def test_unknown_specialty_raises():
    with pytest.raises(ValueError, match="Unknown specialty"):
        check_availability("desconocida", "2026-06-15", "2026-06-16")


@respx.mock
def test_book_appointment_conflict_raises():
    respx.post("https://calcom.test.com/api/v2/bookings").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "message": "slot_not_available"
        })
    )
    with pytest.raises(RuntimeError, match="slot_not_available"):
        book_appointment("+18491234567", "Juan", "general", "2026-06-15T08:30:00.000Z")


@respx.mock
def test_get_upcoming_bookings():
    respx.get("https://calcom.test.com/api/v2/bookings").mock(
        return_value=httpx.Response(200, json={
            "data": {"bookings": [{"uid": "b1", "startTime": "2026-06-15T09:00:00.000Z"}]}
        })
    )
    result = get_upcoming_bookings("2026-06-15")
    assert len(result) == 1
    assert result[0]["uid"] == "b1"
