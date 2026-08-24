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


def test_romper_bucle_corta_la_repeticion_y_avisa(monkeypatch):
    """Caso Sra. Díaz (2026-08-24): el GUION F repetido sin fin. La 2ª vez ya no sale."""
    import main
    guion_f = "Sra. Díaz, permítame un momento, estoy registrando su cita."
    hist = [{"role": "assistant", "content": guion_f},
            {"role": "user", "content": "ok"}]
    avisos = []
    main._bucle_avisado.discard(7)
    monkeypatch.setattr(main, "_avisar_bucle", lambda cid, texto: avisos.append((cid, texto)))
    salida = main._romper_bucle(7, hist, guion_f)
    assert salida == main._MENSAJE_HANDOFF
    assert avisos == [(7, guion_f)]


def test_romper_bucle_no_pone_bot_off(monkeypatch):
    """El corte NO puede silenciar a Carla: bot-off es permanente y la deja muda en esa
    conversación también los días siguientes (decisión del usuario, 2026-08-24)."""
    import main
    from integrations import chatwoot
    etiquetas = []
    monkeypatch.setattr(chatwoot, "add_label", lambda cid, label: etiquetas.append(label))
    monkeypatch.setattr("integrations.alerts.send_dev_alert", lambda msg: None)
    main._bucle_avisado.discard(9)
    hist = [{"role": "assistant", "content": "Permítame un momento."}]
    main._romper_bucle(9, hist, "Permítame un momento.")
    import time; time.sleep(0.3)   # el aviso va en un hilo daemon
    assert chatwoot.BOT_OFF_LABEL not in etiquetas
    assert etiquetas == ["carla-atascada"]


def test_romper_bucle_no_repite_el_handoff(monkeypatch):
    """Si el paciente sigue escribiendo y Carla sigue atascada, callar — no repetirle el handoff."""
    import main
    monkeypatch.setattr(main, "_avisar_bucle", lambda cid, texto: None)
    main._bucle_avisado.discard(11)
    guion_f = "Permítame un momento, estoy registrando su cita."
    hist = [{"role": "assistant", "content": guion_f}]
    assert main._romper_bucle(11, hist, guion_f) == main._MENSAJE_HANDOFF
    hist2 = [{"role": "assistant", "content": main._MENSAJE_HANDOFF}]
    assert main._romper_bucle(11, hist2, main._MENSAJE_HANDOFF) is None


def test_romper_bucle_se_rearma_si_carla_dice_algo_distinto(monkeypatch):
    """Salir del bucle vuelve a dejar el aviso listo para la próxima vez."""
    import main
    monkeypatch.setattr(main, "_avisar_bucle", lambda cid, texto: None)
    main._bucle_avisado.add(13)
    hist = [{"role": "assistant", "content": "Permítame un momento."}]
    assert main._romper_bucle(13, hist, "Su cita quedó para el martes.") == "Su cita quedó para el martes."
    assert 13 not in main._bucle_avisado


def test_romper_bucle_ignora_mayusculas_y_espacios(monkeypatch):
    """Un espacio de más no es un mensaje nuevo."""
    import main
    monkeypatch.setattr(main, "_avisar_bucle", lambda cid, texto: None)
    main._bucle_avisado.discard(15)
    hist = [{"role": "assistant", "content": "Permítame un momento."}]
    assert main._romper_bucle(15, hist, "  permítame   un momento.  ") == main._MENSAJE_HANDOFF
