from unittest.mock import MagicMock, patch

def _mock_chain(return_data):
    mock_result = MagicMock()
    mock_result.data = return_data
    m = MagicMock()
    m.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_result
    m.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_result
    return m

def test_get_patient_returns_none_when_not_found():
    with patch("integrations.supabase_client.get_client", return_value=_mock_chain([])):
        from integrations.supabase_client import get_patient
        assert get_patient("+18491234567") is None

def test_get_patient_returns_patient_when_found():
    with patch("integrations.supabase_client.get_client",
               return_value=_mock_chain([{"phone": "+18491234567", "name": "Juan"}])):
        from integrations.supabase_client import get_patient
        result = get_patient("+18491234567")
        assert result["name"] == "Juan"

def test_get_messages_returns_chronological_order():
    # Supabase returns rows newest-first (DESC order); get_messages reverses to oldest-first
    rows = [
        {"role": "assistant", "content": "Hola", "created_at": "2026-06-14T10:01:00"},
        {"role": "user", "content": "Buenos dias", "created_at": "2026-06-14T10:00:00"},
    ]
    with patch("integrations.supabase_client.get_client", return_value=_mock_chain(rows)):
        from integrations.supabase_client import get_messages
        result = get_messages("+18491234567")
        # After reversing DESC results: oldest (user 10:00) is index 0, newest (assistant 10:01) is index 1
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
