import os
import sys
import importlib
from unittest.mock import patch

for k, v in {
    "CALCOM_URL": "https://calcom.test.com",
    "CALCOM_API_KEY": "test",
    "CALCOM_EVENTS": '{"general":1}',
    "TIMEZONE": "America/Santo_Domingo",
    "CHATWOOT_URL": "https://chatwoot.test.com",
    "CHATWOOT_API_TOKEN": "t",
    "CHATWOOT_ACCOUNT_ID": "1",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY": "test",
}.items():
    os.environ.setdefault(k, v)


def _get_send_nightly_reminders():
    """Import (or reload) the reminders module to get the current implementation."""
    if "scheduler.reminders" in sys.modules:
        mod = importlib.reload(sys.modules["scheduler.reminders"])
    else:
        import scheduler.reminders as mod
    return mod.send_nightly_reminders


def test_send_nightly_reminders_processes_booking():
    bookings = [{
        "uid": "b1",
        "attendees": [{"name": "Juan", "email": "18491234567@odontotec.bot"}],
        "startTime": "2026-06-15T09:00:00.000Z",
        "eventType": {"title": "Odontología General"}
    }]
    with patch("integrations.calcom.get_upcoming_bookings", return_value=bookings), \
         patch("integrations.supabase_client.ensure_patient") as mock_ensure, \
         patch("integrations.supabase_client.save_reminder") as mock_save, \
         patch("integrations.supabase_client.update_reminder_status") as mock_update:
        send_nightly_reminders = _get_send_nightly_reminders()
        send_nightly_reminders()
        mock_ensure.assert_called_once_with("+18491234567")
        mock_save.assert_called_once()
        mock_update.assert_called_once_with("b1", "reminder_sent")


def test_send_nightly_reminders_skips_non_odontotec_email():
    bookings = [{
        "uid": "b2",
        "attendees": [{"name": "External", "email": "someone@gmail.com"}],
        "startTime": "2026-06-15T09:00:00.000Z",
        "eventType": {"title": "General"}
    }]
    with patch("integrations.calcom.get_upcoming_bookings", return_value=bookings), \
         patch("integrations.supabase_client.save_reminder") as mock_save:
        send_nightly_reminders = _get_send_nightly_reminders()
        send_nightly_reminders()
        mock_save.assert_not_called()


def test_send_nightly_reminders_handles_calcom_error():
    with patch("integrations.calcom.get_upcoming_bookings",
               side_effect=Exception("connection refused")):
        send_nightly_reminders = _get_send_nightly_reminders()
        # Should not raise — errors are caught and logged
        send_nightly_reminders()
