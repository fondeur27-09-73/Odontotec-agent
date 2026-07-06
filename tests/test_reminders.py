import importlib
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from integrations import db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "test.db"))
    db_mod.init_db()
    return db_mod


# --- SQLite recordatorios -------------------------------------------------

def test_idempotencia_por_tramo(db):
    assert db.ya_enviado("A1", "48h") is False
    db.marcar_recordatorio("A1", "48h", "18091234567", 55, "2026-07-08", "214")
    assert db.ya_enviado("A1", "48h") is True
    # el otro tramo sigue pendiente
    assert db.ya_enviado("A1", "24h") is False
    db.marcar_recordatorio("A1", "24h", "18091234567", 55, "2026-07-08", "214")
    assert db.ya_enviado("A1", "24h") is True


def test_buscar_cita_por_phone_normaliza(db):
    # guardado con formato E.164, búsqueda con formato local -> mismo últimos-10
    db.marcar_recordatorio("A2", "24h", "+18095551234", 99, "2026-07-08", "215")
    cita = db.buscar_cita_por_phone("809-555-1234")
    assert cita is not None
    assert cita["id_agenda"] == "A2"
    assert cita["conversation_id"] == 99
    assert cita["location"] == "215"


def test_buscar_cita_devuelve_la_mas_reciente(db):
    db.marcar_recordatorio("VIEJA", "48h", "18090000001", 1, "2026-07-08", "214")
    db.marcar_recordatorio("NUEVA", "24h", "18090000001", 2, "2026-07-09", "214")
    cita = db.buscar_cita_por_phone("18090000001")
    assert cita["id_agenda"] == "NUEVA"


# --- helpers de reminders -------------------------------------------------

def test_to_e164():
    from scheduler import reminders
    assert reminders._to_e164("8091234567") == "+18091234567"
    assert reminders._to_e164("+18091234567") == "+18091234567"
    assert reminders._to_e164("809-123-4567") == "+18091234567"
    assert reminders._to_e164("") == ""


def test_is_confirmada():
    from scheduler import reminders
    assert reminders._is_confirmada({"IdStatus": 1211}) is True   # confirmado
    assert reminders._is_confirmada({"IdStatus": 1210}) is False  # no_confirmado
    assert reminders._is_confirmada({"Status": "Confirmado"}) is True
    assert reminders._is_confirmada({"Status": "No confirmado"}) is False


def test_fecha_humana():
    from scheduler import reminders
    assert reminders._fecha_humana("2026-06-29") == "29/06/2026"


# --- procesar_tramo: filtra, envía, marca idempotente ---------------------

def test_procesar_tramo_envia_solo_no_confirmadas(db, monkeypatch):
    from scheduler import reminders
    from datetime import datetime

    agenda = [
        {"IdAgenda": "C1", "IdStatus": 1210, "PatientName": "Ana", "Date": "2026-07-08",
         "time": "10:00", "Reason": "Caries", "LocationName": "Arroyo Hondo", "Phone": "8091110001"},
        {"IdAgenda": "C2", "IdStatus": 1211, "PatientName": "Beto", "Date": "2026-07-08",
         "time": "11:00", "Reason": "Limpieza", "LocationName": "Arroyo Hondo", "Phone": "8091110002"},
        {"IdAgenda": "C3", "IdStatus": 1210, "PatientName": "Cira", "Date": "2026-07-08",
         "time": "12:00", "Reason": "Ortodoncia", "LocationName": "Naco", "Phone": ""},
    ]
    # una sola sucursal para no multiplicar
    monkeypatch.setattr(reminders.dentidesk, "LOCATIONS", {"arroyo_hondo": "214"})
    monkeypatch.setattr(reminders.dentidesk, "get_agenda_day", lambda f, loc: agenda)

    enviados = []
    def fake_send(phone, params, name=""):
        enviados.append((phone, params, name))
        return 700 + len(enviados)
    monkeypatch.setattr(reminders.chatwoot, "send_template", fake_send)
    monkeypatch.setattr(reminders, "db", db)

    n = reminders._procesar_tramo("48h", 2, datetime(2026, 7, 6))
    # C2 confirmada (skip), C3 sin teléfono (skip) -> solo C1
    assert n == 1
    assert enviados[0][0] == "+18091110001"
    assert enviados[0][1]["1"] == "Ana"
    assert enviados[0][1]["2"] == "08/07/2026"
    assert enviados[0][1]["4"] == "10:00"
    assert db.ya_enviado("C1", "48h") is True

    # segunda corrida: idempotente, no reenvía
    enviados.clear()
    n2 = reminders._procesar_tramo("48h", 2, datetime(2026, 7, 6))
    assert n2 == 0
    assert enviados == []


# --- interceptor de botones (main) ----------------------------------------

def test_button_action_detecta():
    import main
    assert main._button_action("✅ Confirmar") == "CONFIRMAR"
    assert main._button_action("CANCELAR") == "CANCELAR"
    assert main._button_action("🔁 Reagendar") == "REAGENDAR"
    assert main._button_action("Hola, quiero una cita") is None