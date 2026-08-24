import os

for k, v in {
    "CHATWOOT_URL": "https://test.com",
    "CHATWOOT_API_TOKEN": "test",
    "CHATWOOT_ACCOUNT_ID": "1",
    "BOT_OFF_LABEL": "bot-off",
    "OPENAI_API_KEY": "test",
    "TIMEZONE": "America/Santo_Domingo",
}.items():
    os.environ.setdefault(k, v)


def test_health():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_button_action_reconoce_botones_cortos():
    from main import _button_action
    assert _button_action("Confirmar") == "CONFIRMAR"
    assert _button_action("✅ Confirmar") == "CONFIRMAR"
    assert _button_action("Cancelar cita") == "CANCELAR"
    assert _button_action("Reagendar") == "REAGENDAR"
    assert _button_action("Reprogramar mi cita") == "REAGENDAR"


def test_button_action_no_secuestra_frases_conversacionales():
    from main import _button_action
    # Frase larga durante el flujo del agente: debe ir al LLM, no al handler de botón.
    assert _button_action("sí, quiero confirmar mi cita para el martes") is None
    # Substring incrustado / typo: no es palabra completa, no cuenta (bug 2026-07-08).
    assert _button_action("si, conrfirmado") is None
    assert _button_action("") is None


# --- bucle: Carla nunca manda dos veces seguidas el mismo mensaje ---

def test_romper_bucle_deja_pasar_una_respuesta_distinta():
    from main import _romper_bucle
    hist = [{"role": "user", "content": "hola"},
            {"role": "assistant", "content": "Buenos días, ¿en qué le puedo ayudar?"}]
    assert _romper_bucle(1, hist, "¿Para qué día desea su cita?") == "¿Para qué día desea su cita?"


def test_romper_bucle_corta_la_repeticion_y_escala(monkeypatch):
    """Caso Sra. Díaz (2026-08-24): el GUION F repetido sin fin. La 2ª vez ya no sale."""
    import main
    guion_f = "Sra. Díaz, permítame un momento, estoy registrando su cita."
    hist = [{"role": "assistant", "content": guion_f},
            {"role": "user", "content": "ok"}]
    escaladas = []
    monkeypatch.setattr("agent.tool_handlers._escalate_to_human",
                        lambda reason, cid: escaladas.append((reason, cid)))
    salida = main._romper_bucle(7, hist, guion_f)
    assert salida == main._MENSAJE_HANDOFF
    assert salida != guion_f
    assert len(escaladas) == 1 and escaladas[0][1] == 7


def test_romper_bucle_ignora_mayusculas_y_espacios(monkeypatch):
    """Un espacio de más no es un mensaje nuevo."""
    import main
    monkeypatch.setattr("agent.tool_handlers._escalate_to_human", lambda reason, cid: None)
    hist = [{"role": "assistant", "content": "Permítame un momento."}]
    assert main._romper_bucle(1, hist, "  permítame   un momento.  ") == main._MENSAJE_HANDOFF
