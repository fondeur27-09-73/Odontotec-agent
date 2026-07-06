"""Tests de Fase 1 (auditoría 2026-07-05): normalización de hora a 24h y mapa especialidad→doctor."""
import json
from unittest.mock import patch

import pytest

from agent.tool_handlers import _to_24h, _resolve_doctor, handle_tool
from integrations.dentidesk_playwright import _split_time_24h


# --- _to_24h: lo que mande el modelo -> 'HH:MM' 24h ---

@pytest.mark.parametrize("raw,expected", [
    ("10:00 AM", "10:00"),
    ("3:00 PM", "15:00"),
    ("2:30 pm", "14:30"),
    ("8:30 a.m.", "08:30"),
    ("12:00 PM", "12:00"),   # mediodía
    ("12:30 AM", "00:30"),   # medianoche
    ("14:00", "14:00"),      # ya en 24h
    ("9 am", "09:00"),
    ("5 pm", "17:00"),
])
def test_to_24h_normaliza(raw, expected):
    assert _to_24h(raw) == expected


@pytest.mark.parametrize("raw", ["", "mediodía", "25:00", "10:99"])
def test_to_24h_rechaza_basura(raw):
    assert _to_24h(raw) is None


# --- _split_time_24h: Playwright solo acepta 24h ya normalizado ---

def test_split_time_24h_ok():
    assert _split_time_24h("15:00") == ("15", "00")
    assert _split_time_24h("8:30") == ("08", "30")


@pytest.mark.parametrize("raw", ["3:00 PM", "10:00 AM", "3pm", "27:00", ""])
def test_split_time_24h_rechaza_no_24h(raw):
    with pytest.raises(ValueError):
        _split_time_24h(raw)


# --- _resolve_doctor: especialidad -> needle de doctor ---

def test_resolve_doctor_defaults():
    assert _resolve_doctor("ortodoncia") == "Cabrera"
    assert _resolve_doctor("CIRUGIA ") == "Angel Lee"


def test_resolve_doctor_general_es_personal_fijo():
    # Regla cliente 2026-07-05: general = personal fijo, "" = no seleccionar doctor.
    assert _resolve_doctor("general") == ""


def test_resolve_doctor_especialidad_desconocida():
    assert _resolve_doctor("dermatologia") is None


def test_resolve_doctor_override_env(monkeypatch):
    monkeypatch.setenv("DENTIDESK_DOCTOR_MAP", '{"ortodoncia": "Casado"}')
    assert _resolve_doctor("ortodoncia") == "Casado"
    assert _resolve_doctor("endodoncia") == "Cedano"  # defaults sobreviven al override parcial
    assert _resolve_doctor("general") == ""           # regla de personal fijo intacta


def test_resolve_doctor_env_malformado_no_rompe(monkeypatch):
    monkeypatch.setenv("DENTIDESK_DOCTOR_MAP", "{esto no es json")
    assert _resolve_doctor("protesis") == "Adriana Abreu"


# --- handle_tool: errores estructurados ANTES de tocar Playwright ---

@pytest.fixture(autouse=True)
def _sin_agenda_previa(monkeypatch):
    """Aísla el chequeo de idempotencia: por defecto la agenda real no tiene cita previa
    (y nunca se pega a la API de Dentidesk desde los tests)."""
    monkeypatch.setattr("agent.tool_handlers.dentidesk.find_by_cedula", lambda *a, **k: None)
    monkeypatch.setattr("agent.tool_handlers.dentidesk.find_by_phone", lambda *a, **k: None)


_BASE_ARGS = {
    "patient_name": "Juan Pérez", "patient_phone": "+18091234567",
    "specialty": "ortodoncia", "day": "lunes 6 de julio",
    "fecha_iso": "2026-07-06",
}


def test_agendar_hora_invalida_no_llama_playwright():
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "cuando pueda"}))
    assert result["success"] is False
    assert result["error"] == "hora_invalida"
    mock_pw.assert_not_called()


def test_agendar_general_sin_doctor_llega_a_playwright():
    # general = personal fijo: se agenda SIN seleccionar doctor (doctor_label="").
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"success": True, "IdAgenda": "1000"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "specialty": "general", "time": "10:00 AM"}))
    assert result["success"] is True
    assert mock_pw.call_args.kwargs["doctor_label"] == ""


def test_agendar_especialidad_desconocida_no_llama_playwright():
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "specialty": "dermatologia",
                                         "time": "10:00 AM"}))
    assert result["success"] is False
    assert result["error"] == "doctor_no_mapeado"
    mock_pw.assert_not_called()


def test_agendar_pasa_hora_24h_y_doctor_a_playwright():
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"success": True, "IdAgenda": "999"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "3:00 PM"}))
    assert result["success"] is True
    kwargs = mock_pw.call_args.kwargs
    assert kwargs["time"] == "15:00"
    assert kwargs["doctor_label"] == "Cabrera"


def test_reagendar_pasa_hora_24h():
    with patch("agent.tool_handlers.dentidesk_playwright.move_appointment",
               return_value={"success": True, "IdAgenda": "999"}) as mock_pw:
        result = json.loads(handle_tool("reagendar_cita_dentidesk", {
            "id_agenda": "999", "fecha_actual_iso": "2026-07-06",
            "patient_name": "Juan Pérez", "fecha_iso": "2026-07-07", "time": "4:30 PM",
        }))
    assert result["success"] is True
    assert mock_pw.call_args.kwargs["nueva_hora"] == "16:30"


def test_agendar_idempotente_si_ya_hay_cita_ese_dia(monkeypatch):
    # Doble llamada del modelo (o dos mensajes casi simultáneos): si la agenda real ya tiene
    # cita del paciente ese día, NO se abre Playwright ni se crea duplicado.
    monkeypatch.setattr(
        "agent.tool_handlers.dentidesk.find_by_phone",
        lambda *a, **k: {"IdAgenda": "555", "PatientName": "Juan Pérez",
                         "Date": "2026-07-06", "time": "10:00"},
    )
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "10:00 AM"}))
    assert result["success"] is True
    assert result["ya_existia"] is True
    assert result["IdAgenda"] == "555"
    mock_pw.assert_not_called()


def test_agendar_sigue_si_chequeo_idempotencia_falla(monkeypatch):
    # La consulta a la agenda es best-effort: si la API falla, el agendado continúa.
    def _boom(*a, **k):
        raise RuntimeError("API caída")
    monkeypatch.setattr("agent.tool_handlers.dentidesk.find_by_phone", _boom)
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"success": True, "IdAgenda": "777"}):
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "10:00 AM"}))
    assert result["success"] is True
    assert result["IdAgenda"] == "777"


def test_agendar_fuera_de_horario_gana_antes_que_hora_invalida():
    # Domingo: bloquea por horario aunque la hora venga bien formada.
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "fecha_iso": "2026-07-05",
                                         "time": "10:00 AM"}))  # 2026-07-05 = domingo
    assert result["success"] is False
    assert result["error"] == "fuera_de_horario"
    mock_pw.assert_not_called()
