"""Tests de Fase 1 (auditoría 2026-07-05): normalización de hora a 24h y mapa especialidad→doctor."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from agent.tool_handlers import _to_24h, _resolve_doctor, handle_tool, _cedula_dominicana_ok
from integrations.dentidesk_playwright import _split_time_24h, _rut_field
from scripts.dentidesk_daemon import _wait_until_dead, _keep_session_warm, _clear_singleton_locks


# --- _wait_until_dead: el daemon debe salir cuando Chrome se muere, no dormir para siempre ---

def test_wait_until_dead_vuelve_cuando_muere_el_navegador():
    page = MagicMock()
    page.is_closed.side_effect = [False, False, True]  # vive 2 vueltas, luego muere
    _wait_until_dead(page, poll=0)
    assert page.is_closed.call_count == 3


def test_wait_until_dead_no_bloquea_si_ya_esta_muerto():
    page = MagicMock()
    page.is_closed.return_value = True
    _wait_until_dead(page, poll=0)
    assert page.is_closed.call_count == 1


# --- _keep_session_warm: heartbeat en pestana propia + re-guarda cookie (fix caido-todo-el-dia) ---

def test_keep_session_warm_heartbeat_en_pestana_propia_y_resave():
    ctx = MagicMock()
    hb = MagicMock()
    ctx.new_page.return_value = hb
    page = MagicMock()
    page.is_closed.side_effect = [False, True]  # una vuelta con heartbeat, luego muere
    with patch("scripts.dentidesk_daemon._save_cookies") as save:
        _keep_session_warm(ctx, page, poll=0, every=0)
    ctx.new_page.assert_called_once()   # pestana dedicada, NUNCA pages[0] (no pisa una cita)
    assert hb.goto.called               # navego Dentidesk = actividad (sesion caliente)
    assert save.called                  # re-guardo la cookie fresca para el proximo reinicio


# --- _clear_singleton_locks: lock rancio del perfil persistente hacia crash-loop de Chrome ---

def test_clear_singleton_locks_borra_los_rancios_y_tolera_ausentes(tmp_path):
    (tmp_path / "SingletonLock").write_text("host-26")   # el que causaba exitCode=21
    # SingletonSocket y SingletonCookie NO existen -> no debe reventar por ellos
    removed = _clear_singleton_locks(str(tmp_path))
    assert removed == ["SingletonLock"]
    assert not (tmp_path / "SingletonLock").exists()
    # idempotente: segunda pasada sin locks no borra nada ni falla
    assert _clear_singleton_locks(str(tmp_path)) == []


# --- _rut_field: cédula dominicana con guion rompe el campo RUT chileno de Dentidesk ---

@pytest.mark.parametrize("cedula,expected", [
    ("0310277809-2", "03102778092"),   # el caso real que no guardaba (borde rojo)
    ("031-0277809-2", "03102778092"),  # formato dominicano completo
    ("031 0277809 2", "03102778092"),  # con espacios
    ("03102778092", "03102778092"),    # ya limpia -> igual
    ("", "1-9"),                        # sin cédula -> placeholder de Dentidesk
    ("  ", "1-9"),                      # solo espacios -> placeholder
])
def test_rut_field(cedula, expected):
    assert _rut_field(cedula) == expected


# --- _cedula_dominicana_ok: guardrail de 11 dígitos ANTES de escribir a Dentidesk ---
# (Dentidesk no valida longitud; una cédula corta crea ficha basura y envenena el autocompletar.)

@pytest.mark.parametrize("cedula,ok", [
    ("03102778092", True),     # 11 dígitos limpios
    ("031-0277809-2", True),   # 11 dígitos con formato dominicano
    ("031 0277809 2", True),   # con espacios
    ("123", False),            # el caso real que Dentidesk sí guardaba (3 dígitos)
    ("0310", False),           # el prefijo truncado que envenenó el autocompletar
    ("0310277809", False),     # 10 dígitos: uno de menos
    ("031027780921", False),   # 12 dígitos: uno de más
    ("", False),               # vacía
    ("1-9", False),            # el placeholder de Dentidesk NO pasa el guardrail
])
def test_cedula_dominicana_ok(cedula, ok):
    passed, msg = _cedula_dominicana_ok(cedula)
    assert passed is ok
    assert (msg == "") is ok  # rechazo trae mensaje para el paciente; aceptación no


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
    assert _resolve_doctor("ortodoncia") == "ortodoncia ortodoncia"
    assert _resolve_doctor("CIRUGIA ") == "Angel Lee"


def test_resolve_doctor_general_ficha_generica():
    # Regla cliente 2026-08-20: general va a la ficha genérica "DOCTOR GENERAL".
    assert _resolve_doctor("general") == "general general"


def test_resolve_doctor_especialidad_desconocida():
    assert _resolve_doctor("dermatologia") is None


def test_resolve_doctor_override_env(monkeypatch):
    monkeypatch.setenv("DENTIDESK_DOCTOR_MAP", '{"ortodoncia": "Casado"}')
    assert _resolve_doctor("ortodoncia") == "Casado"
    assert _resolve_doctor("endodoncia") == "Cedano"  # defaults sobreviven al override parcial
    assert _resolve_doctor("general") == "general general"    # ficha genérica intacta


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


def _proximo(weekday: int) -> str:
    """Fecha FUTURA más cercana con ese día de la semana (0=lunes … 6=domingo).

    Estos tests traían fechas fijas de julio 2026. Al pasar esas fechas, el guardrail
    _fecha_no_pasada empezó a rechazarlas — correctamente — y 10 tests reventaron sin que nada
    del código estuviera mal. Fechas relativas a hoy: no se vuelven a pudrir."""
    hoy = datetime.now().date()
    delta = (weekday - hoy.weekday()) % 7 or 7  # 'or 7' -> nunca hoy, siempre futuro
    return (hoy + timedelta(days=delta)).strftime("%Y-%m-%d")


_LUNES, _MARTES, _DOMINGO = _proximo(0), _proximo(1), _proximo(6)

_BASE_ARGS = {
    "patient_name": "Juan Pérez", "patient_phone": "+18091234567",
    "specialty": "ortodoncia", "day": "el próximo lunes",
    "fecha_iso": _LUNES, "cedula": "03102778092",  # 11 dígitos: pasa el guardrail de cédula
}


def test_agendar_cedula_corta_no_llama_playwright():
    """El guardrail de 11 dígitos corta ANTES de tocar Playwright: cédula incompleta -> no escribe."""
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "cedula": "123", "time": "10:00 AM"}))
    assert result["success"] is False
    assert result["error"] == "cedula_invalida"
    mock_pw.assert_not_called()


def test_agendar_hora_invalida_no_llama_playwright():
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "cuando pueda"}))
    assert result["success"] is False
    assert result["error"] == "hora_invalida"
    mock_pw.assert_not_called()


class _SyncThread:
    """Thread que corre el target al start(), para poder assertar el fire-and-forget en el test."""
    def __init__(self, target=None, args=(), daemon=None):
        self._t, self._a = target, args
    def start(self):
        self._t(*self._a)


def test_escalate_agrega_label_bot_off_y_avisa_al_operador():
    # El label bot-off es pasivo: sin el aviso, el paciente pide humano y nadie se entera.
    with patch("integrations.chatwoot.add_label") as mock_label, \
         patch("integrations.chatwoot.get_labels", return_value=[]), \
         patch("integrations.alerts.send_dev_alert") as mock_alert, \
         patch("threading.Thread", _SyncThread):
        result = json.loads(handle_tool("escalate_to_human",
                                        {"reason": "recado", "conversation_id": 42}))
    mock_label.assert_called_once_with(42, "bot-off")
    mock_alert.assert_called_once()                      # se avisó al operador
    assert "42" in mock_alert.call_args.args[0]          # con el id de la conversación
    assert "recado" in mock_alert.call_args.args[0]      # y el motivo
    assert result["success"] is True


def test_tool_desconocida_devuelve_error():
    result = json.loads(handle_tool("nonexistent", {}))
    assert "error" in result


def test_agendar_general_va_a_la_ficha_generica():
    # general -> ficha genérica "DOCTOR GENERAL", no un especialista con nombre propio.
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"success": True, "IdAgenda": "1000"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "specialty": "general", "time": "10:00 AM"}))
    assert result["success"] is True
    assert mock_pw.call_args.kwargs["doctor_label"] == "general general"


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
    assert kwargs["doctor_label"] == "ortodoncia ortodoncia"


def test_reagendar_pasa_hora_24h():
    with patch("agent.tool_handlers.dentidesk_playwright.move_appointment",
               return_value={"success": True, "IdAgenda": "999"}) as mock_pw:
        result = json.loads(handle_tool("reagendar_cita_dentidesk", {
            "id_agenda": "999", "fecha_actual_iso": _LUNES,
            "patient_name": "Juan Pérez", "fecha_iso": _MARTES, "time": "4:30 PM",
        }))
    assert result["success"] is True
    assert mock_pw.call_args.kwargs["nueva_hora"] == "16:30"
    # Sin cambio de tratamiento: no se toca doctor ni motivo.
    assert mock_pw.call_args.kwargs["nuevo_doctor_label"] == ""
    assert mock_pw.call_args.kwargs["nuevo_procedimiento"] == ""


def test_reagendar_con_cambio_de_tratamiento_resuelve_doctor_nuevo():
    # El paciente cambia de endodoncia a limpieza (general): se resuelve el doctor nuevo por
    # especialidad y se pasa el nuevo motivo, además de mover fecha/hora.
    with patch("agent.tool_handlers.dentidesk_playwright.move_appointment",
               return_value={"success": True, "IdAgenda": "999"}) as mock_pw:
        result = json.loads(handle_tool("reagendar_cita_dentidesk", {
            "id_agenda": "999", "fecha_actual_iso": _LUNES, "patient_name": "Juan Pérez",
            "doctor": "Cedano", "fecha_iso": _MARTES, "time": "10:00 AM",
            "specialty": "ortodoncia", "procedimiento": "Brackets",
        }))
    assert result["success"] is True
    kwargs = mock_pw.call_args.kwargs
    assert kwargs["doctor_label"] == "Cedano"            # doctor ACTUAL (para ubicar la tarjeta)
    assert kwargs["nuevo_doctor_label"] == "ortodoncia ortodoncia"  # doctor NUEVO (ficha de ortodoncia)
    assert kwargs["nuevo_procedimiento"] == "Brackets"


def test_reagendar_especialidad_desconocida_no_llama_playwright():
    with patch("agent.tool_handlers.dentidesk_playwright.move_appointment") as mock_pw:
        result = json.loads(handle_tool("reagendar_cita_dentidesk", {
            "id_agenda": "999", "fecha_actual_iso": _LUNES, "patient_name": "Juan Pérez",
            "fecha_iso": _MARTES, "time": "10:00 AM", "specialty": "dermatologia",
        }))
    assert result["success"] is False
    assert result["error"] == "doctor_no_mapeado"
    mock_pw.assert_not_called()


def test_agendar_idempotente_si_ya_hay_cita_ese_dia(monkeypatch):
    # Doble llamada del modelo (o dos mensajes casi simultáneos): si la agenda real ya tiene
    # cita del paciente ese día, NO se abre Playwright ni se crea duplicado.
    monkeypatch.setattr(
        "agent.tool_handlers.dentidesk.find_by_phone",
        lambda *a, **k: {"IdAgenda": "555", "PatientName": "Juan Pérez",
                         "Date": _LUNES, "time": "10:00"},
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
                                        {**_BASE_ARGS, "fecha_iso": _DOMINGO,
                                         "time": "10:00 AM"}))
    assert result["success"] is False
    assert result["error"] == "fuera_de_horario"
    mock_pw.assert_not_called()


def test_agendar_fecha_pasada_no_llama_playwright():
    """Dentidesk acepta una cita en el pasado sin chistar y deshacerlo es a mano."""
    ayer = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "fecha_iso": ayer, "time": "10:00 AM"}))
    assert result["success"] is False
    assert result["error"] == "fecha_pasada"
    mock_pw.assert_not_called()


def test_reagendar_fecha_pasada_no_llama_playwright():
    ayer = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    with patch("agent.tool_handlers.dentidesk_playwright.move_appointment") as mock_pw:
        result = json.loads(handle_tool("reagendar_cita_dentidesk", {
            "id_agenda": "999", "fecha_actual_iso": _LUNES, "patient_name": "Juan Pérez",
            "fecha_iso": ayer, "time": "10:00 AM",
        }))
    assert result["success"] is False
    assert result["error"] == "fecha_pasada"
    mock_pw.assert_not_called()


# --- buscar_cita_proxima_dentidesk: encontrar la cita sin conocer la fecha ---

def test_find_upcoming_encuentra_la_mas_temprana_por_telefono():
    from integrations import dentidesk
    # Agenda simulada por día: la cita del paciente está a 3 días.
    agenda = {
        "2026-07-08": [{"PatientName": "Otro", "Phone": "8090000000"}],
        "2026-07-09": [],
        "2026-07-11": [{"IdAgenda": "555", "PatientName": "Analeidy López",
                        "Phone": "18097873496", "Date": "2026-07-11", "time": "14:30"}],
    }
    with patch("integrations.dentidesk._today_clinic",
               return_value=__import__("datetime").date(2026, 7, 8)), \
         patch("integrations.dentidesk.get_agenda_day",
               side_effect=lambda d, loc=None: agenda.get(d, [])):
        cita = dentidesk.find_upcoming(phone="+1 809-787-3496", days=10)
    assert cita is not None
    assert cita["IdAgenda"] == "555"


def test_find_upcoming_salta_domingos_y_no_encuentra():
    from integrations import dentidesk
    called = []
    def _fake_day(d, loc=None):
        called.append(d)
        return []
    with patch("integrations.dentidesk._today_clinic",
               return_value=__import__("datetime").date(2026, 7, 8)), \
         patch("integrations.dentidesk.get_agenda_day", side_effect=_fake_day):
        cita = dentidesk.find_upcoming(cedula="031-0277909-1", days=7)
    assert cita is None
    assert "2026-07-12" not in called  # 2026-07-12 = domingo, no se consulta


def test_buscar_cita_proxima_handler_normaliza_shape():
    with patch("agent.tool_handlers.dentidesk.find_upcoming",
               return_value={"IdAgenda": "777", "PatientName": "Ulises Ramírez",
                             "Date": "2026-07-21", "time": "14:30",
                             "ProfessionalName": "Adriana Abreu"}):
        result = json.loads(handle_tool("buscar_cita_proxima_dentidesk",
                                        {"telefono": "+18099790205"}))
    assert result["found"] is True
    assert result["IdAgenda"] == "777"
    assert result["doctor"] == "Adriana Abreu"


def test_buscar_cita_proxima_handler_no_encontrada():
    with patch("agent.tool_handlers.dentidesk.find_upcoming", return_value=None):
        result = json.loads(handle_tool("buscar_cita_proxima_dentidesk",
                                        {"cedula": "000"}))
    assert result["found"] is False


# --- Chequeo de sesión del daemon (rework 2026-07-09): en vez de auto-login inútil (no pasa el
#     reCAPTCHA), fallar CLARO con SesionNoLogueada si el navegador no está logueado. ---

def test_session_live_true_cuando_agenda_cargada():
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.evaluate.return_value = True  # moment + open_modal_cita presentes
    assert dp._session_live(page) is True


def test_session_live_false_si_evaluate_revienta():
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.evaluate.side_effect = Exception("moment is not defined")
    assert dp._session_live(page) is False


def test_require_session_live_pasa_si_agenda_lista():
    # wait_for_function no lanza → sesión viva → no explota.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.wait_for_function.return_value = None
    dp._require_session_live(page)  # no debe lanzar


def test_require_session_live_lanza_si_hay_formulario_login():
    # La agenda es en realidad el login (#user-login) → SesionNoLogueada con mensaje claro.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.wait_for_function.side_effect = Exception("timeout")  # moment nunca aparece
    page.locator.return_value.count.return_value = 1  # existe #user-login
    with pytest.raises(dp.SesionNoLogueada) as exc:
        dp._require_session_live(page)
    assert "login" in str(exc.value).lower()


def test_call_daemon_409_devuelve_error_estructurado():
    # El daemon responde 409 (no logueado) → dict success:False, NO excepción.
    from integrations import dentidesk_playwright as dp
    resp = MagicMock()
    resp.status_code = 409
    resp.json.return_value = {"detail": "no logueado"}
    with patch.object(dp, "DAEMON_URL", "http://daemon:8100"), \
         patch("httpx.post", return_value=resp):
        out = dp._call_daemon("/crear_cita", {})
    assert out["success"] is False
    assert out["error"] == "daemon_no_logueado"
    assert "message" in out


# --- Guard del autocompletar de #rut (2026-08-19): un registro viejo de cédula CORTA matchea por
#     prefijo a una de 11 dígitos y el Enter final se lleva la ficha equivocada. ---

def test_type_rut_recognize_detecta_hijack_del_autocompletar():
    # Tecleamos 11 dígitos pero el campo quedó con la cédula corta del paciente viejo → hijack.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.input_value.return_value = "0310"
    with pytest.raises(dp.CedulaAutocompletarHijack) as exc:
        dp._type_rut_recognize(page, "03102778092")
    assert "03102778092" in str(exc.value) and "0310" in str(exc.value)
    # Debe abortar ANTES de dar por bueno el #id_paciente — si no, agenda en la ficha equivocada.
    page.wait_for_function.assert_not_called()


def test_type_rut_recognize_ok_aunque_dentidesk_reformatee_el_rut():
    # Dentidesk puede devolver el RUT con guion/puntos: se comparan solo dígitos, no el texto.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.input_value.return_value = "031-0277809-2"
    page.wait_for_function.return_value = None  # #id_paciente quedó != 0 → paciente existente
    assert dp._type_rut_recognize(page, "03102778092") is True


def test_type_rut_recognize_false_para_paciente_nuevo():
    # Nadie con esa cédula: la cédula sobrevive intacta y #id_paciente nunca se llena → alta nueva.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.input_value.return_value = "03102778092"
    page.wait_for_function.side_effect = Exception("timeout")
    assert dp._type_rut_recognize(page, "03102778092") is False


def test_type_rut_recognize_no_revienta_con_placeholder_sin_digitos():
    # _rut_field() manda '1-9' cuando la cédula viene vacía; ese caso no debe disparar el guard.
    from integrations import dentidesk_playwright as dp
    page = MagicMock()
    page.input_value.return_value = "1-9"
    page.wait_for_function.side_effect = Exception("timeout")
    assert dp._type_rut_recognize(page, "1-9") is False


# ── Citas de terceros (bug 2026-08-19: Carla agendó a nombre de quien escribía) ────────────────

def test_agendar_tercero_con_datos_del_titular_no_llama_playwright():
    """Cita declarada para un tercero pero con el nombre/cédula de quien escribe -> se corta."""
    titular = {"name": "Karen Ferreras", "cedula": "03102778092"}
    with patch("agent.tool_handlers.db.get_patient", return_value=titular), \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk", {
            **_BASE_ARGS, "patient_name": "karen  ferreras", "time": "10:00 AM",
            "cita_para_tercero": True}))
    assert result["success"] is False
    assert result["error"] == "datos_del_titular"
    mock_pw.assert_not_called()


def test_agendar_tercero_con_datos_propios_del_tercero_si_agenda():
    """Nombre y cédula del tercero (distintos a los del titular) -> agenda normal."""
    titular = {"name": "Karen Ferreras", "cedula": "03102778092"}
    with patch("agent.tool_handlers.db.get_patient", return_value=titular), \
         patch("agent.tool_handlers.dentidesk.find_by_cedula", return_value=None), \
         patch("agent.tool_handlers.dentidesk.find_by_phone") as mock_phone, \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"IdAgenda": "1"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk", {
            **_BASE_ARGS, "patient_name": "José Gabriel Ramírez", "cedula": "00112345678",
            "time": "10:00 AM", "cita_para_tercero": True}))
    assert result["success"] is True
    mock_pw.assert_called_once()
    # En cita de tercero NO se busca por el teléfono de quien escribe: encontraría la cita del titular.
    mock_phone.assert_not_called()


def test_agendar_titular_no_dispara_el_guard():
    """Cita propia con los datos del titular: normal, no es el caso del guard."""
    titular = {"name": "Juan Pérez", "cedula": "03102778092"}
    with patch("agent.tool_handlers.db.get_patient", return_value=titular), \
         patch("agent.tool_handlers.dentidesk.find_by_cedula", return_value=None), \
         patch("agent.tool_handlers.dentidesk.find_by_phone", return_value=None), \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"IdAgenda": "1"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk", {**_BASE_ARGS, "time": "10:00 AM"}))
    assert result["success"] is True
    mock_pw.assert_called_once()


def test_doctor_generico_para_ortodoncia_y_general():
    """Ortodoncia y general van a las fichas genéricas, no a un especialista con nombre propio."""
    from agent.tool_handlers import _resolve_doctor
    assert _resolve_doctor("ortodoncia") == "ortodoncia ortodoncia"
    assert _resolve_doctor("general") == "general general"
    assert _resolve_doctor("cirugia") == "Angel Lee"


def test_ortodoncia_y_general_son_fichas_DISTINTAS():
    """Regla del cliente: DR. ORTODONCIA es SOLO ortodoncia; limpieza/caries/general van a
    DOCTOR GENERAL. Son dos fichas separadas — jamás la misma ni una contenida en la otra."""
    from agent.tool_handlers import _resolve_doctor
    orto, gen = _resolve_doctor("ortodoncia"), _resolve_doctor("general")
    assert orto and gen and orto != gen
    assert orto not in gen and gen not in orto


# ── Carla elige el doctor del listado real de Dentidesk ───────────────────────────────────────

def test_doctor_elegido_por_carla_llega_a_playwright():
    with patch("agent.tool_handlers.dentidesk.find_by_cedula", return_value=None), \
         patch("agent.tool_handlers.dentidesk.find_by_phone", return_value=None), \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"IdAgenda": "1"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk", {
            **_BASE_ARGS, "time": "10:00 AM", "doctor": "Dra. Aimer Cedano"}))
    assert result["success"] is True
    # "Dra." y tildes se toleran; a Dentidesk va el nombre canónico del listado.
    assert mock_pw.call_args.kwargs["doctor_label"] == "Aimer Cedano"


def test_doctor_inventado_no_llama_playwright():
    """Un doctor que no está en el listado de Dentidesk se rechaza ANTES de escribir."""
    with patch("agent.tool_handlers.dentidesk_playwright.create_appointment") as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk", {
            **_BASE_ARGS, "time": "10:00 AM", "doctor": "Dr. Fulano de Tal"}))
    assert result["success"] is False
    assert result["error"] == "doctor_desconocido"
    mock_pw.assert_not_called()


def test_sin_doctor_elegido_cae_al_default_de_la_especialidad():
    with patch("agent.tool_handlers.dentidesk.find_by_cedula", return_value=None), \
         patch("agent.tool_handlers.dentidesk.find_by_phone", return_value=None), \
         patch("agent.tool_handlers.dentidesk_playwright.create_appointment",
               return_value={"IdAgenda": "1"}) as mock_pw:
        result = json.loads(handle_tool("agendar_cita_dentidesk",
                                        {**_BASE_ARGS, "time": "10:00 AM"}))
    assert result["success"] is True
    assert mock_pw.call_args.kwargs["doctor_label"] == "ortodoncia ortodoncia"


def test_fichas_genericas_estan_en_el_listado_real():
    """Los defaults tienen que existir en el listado de Dentidesk, o el agendado revienta en vivo."""
    from agent.tool_handlers import _DOCTOR_DEFAULTS, _doctor_de_la_lista
    for esp, needle in _DOCTOR_DEFAULTS.items():
        assert _doctor_de_la_lista(needle) is not None, f"{esp} -> '{needle}' no está en Dentidesk"


# ── Reagendar cita de un tercero: no buscar por los datos de quien escribe ─────────────────────

def test_buscar_tercero_descarta_el_telefono_de_quien_escribe():
    """Bug hermano del de agendar: buscar por el teléfono del remitente devuelve SU cita, y Carla
    terminaría moviendo la cita equivocada."""
    titular = {"name": "Karen Ferreras", "cedula": "03102778092"}
    with patch("agent.tool_handlers.db.get_patient", return_value=titular), \
         patch("agent.tool_handlers.dentidesk.find_upcoming", return_value=None) as mock_find:
        handle_tool("buscar_cita_proxima_dentidesk", {
            "telefono": "+18091234567", "cedula": "03102778092",
            "nombre": "José Gabriel Ramírez", "cita_para_tercero": True})
    kwargs = mock_find.call_args.kwargs
    assert kwargs["phone"] == ""                      # el teléfono del remitente, descartado
    assert kwargs["cedula"] == ""                     # su cédula, también
    assert kwargs["nombre"] == "José Gabriel Ramírez"  # se busca por el nombre del paciente


def test_buscar_tercero_conserva_el_telefono_si_es_del_paciente():
    """Si el teléfono resulta ser de verdad el del familiar, NO se descarta: sirve para encontrarla."""
    titular = {"name": "José Gabriel Ramírez", "cedula": "00112345678"}
    with patch("agent.tool_handlers.db.get_patient", return_value=titular), \
         patch("agent.tool_handlers.dentidesk.find_upcoming", return_value=None) as mock_find:
        handle_tool("buscar_cita_proxima_dentidesk", {
            "telefono": "+18099999999", "nombre": "José Gabriel Ramírez",
            "cita_para_tercero": True})
    assert mock_find.call_args.kwargs["phone"] == "+18099999999"


def test_buscar_cita_propia_no_descarta_nada():
    with patch("agent.tool_handlers.dentidesk.find_in_day", return_value=None) as mock_find:
        handle_tool("buscar_cita_dentidesk", {
            "fecha_iso": _LUNES, "telefono": "+18091234567", "cedula": "03102778092"})
    assert mock_find.call_args.kwargs["phone"] == "+18091234567"
    assert mock_find.call_args.kwargs["cedula"] == "03102778092"
