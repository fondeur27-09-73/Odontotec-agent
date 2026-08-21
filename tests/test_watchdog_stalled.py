"""Alerta de conversación estancada (pedido del usuario 2026-07-22): avisar cuando un paciente
lleva > umbral esperando respuesta en una conversación abierta. Vigila el síntoma, no la causa."""
import os
import time
from unittest.mock import patch

os.environ.setdefault("WATCHDOG_STALLED_MIN", "5")

from scheduler import watchdog


def _reset():
    watchdog._alerted_stalled.clear()


def _run(convs, msgs_por_conv, bot_off=False):
    """Corre el chequeo con Chatwoot mockeado. Devuelve la lista de mensajes de alerta enviados."""
    enviados = []
    with patch("integrations.chatwoot.get_open_conversations", return_value=convs), \
         patch("integrations.chatwoot.get_conv_messages", side_effect=lambda cid: msgs_por_conv.get(cid, [])), \
         patch("integrations.chatwoot.is_bot_off", return_value=bot_off), \
         patch("scheduler.watchdog.alerts.send_dev_alert", side_effect=enviados.append):
        report = {}
        watchdog._check_stalled_conversations(report)
    return enviados, report


def test_paciente_esperando_mas_del_umbral_dispara_una_alerta():
    _reset()
    viejo = int(time.time()) - 600  # 10 min, último mensaje del PACIENTE (type 0)
    convs = [{"id": 7}]
    msgs = {7: [{"message_type": 0, "content": "Hola?", "created_at": viejo}]}
    enviados, report = _run(convs, msgs)
    assert len(enviados) == 1
    assert "7" in enviados[0] and "esperando" in enviados[0].lower()
    assert report["stalled"] == {"count": 1, "ids": [7]}


def test_edge_trigger_no_repite_alerta_en_el_siguiente_ciclo():
    _reset()
    viejo = int(time.time()) - 600
    convs = [{"id": 7}]
    msgs = {7: [{"message_type": 0, "content": "Hola?", "created_at": viejo}]}
    e1, _ = _run(convs, msgs)
    e2, _ = _run(convs, msgs)   # mismo estado estancado -> silencio
    assert len(e1) == 1
    assert len(e2) == 0


def test_carla_ya_contesto_no_alerta():
    _reset()
    t = int(time.time()) - 600
    convs = [{"id": 7}]
    # último mensaje es de Carla (type 1) -> la pelota no está de nuestro lado
    msgs = {7: [{"message_type": 0, "content": "Hola?", "created_at": t - 10},
                {"message_type": 1, "content": "Dígame", "created_at": t}]}
    enviados, report = _run(convs, msgs)
    assert enviados == []
    assert report["stalled"]["count"] == 0


def test_mensaje_reciente_no_alerta():
    _reset()
    reciente = int(time.time()) - 30  # 30s, bajo el umbral
    convs = [{"id": 7}]
    msgs = {7: [{"message_type": 0, "content": "Hola?", "created_at": reciente}]}
    enviados, _ = _run(convs, msgs)
    assert enviados == []


def test_conversacion_ya_escalada_a_humano_no_alerta():
    _reset()
    viejo = int(time.time()) - 600
    convs = [{"id": 7}]
    msgs = {7: [{"message_type": 0, "content": "Quiero un humano", "created_at": viejo}]}
    enviados, _ = _run(convs, msgs, bot_off=True)  # ya tiene label bot-off -> alguien la tiene
    assert enviados == []


def test_se_rearma_cuando_deja_de_estar_estancada():
    _reset()
    viejo = int(time.time()) - 600
    convs = [{"id": 7}]
    msgs_estancada = {7: [{"message_type": 0, "content": "Hola?", "created_at": viejo}]}
    # respondida: último mensaje ahora es de Carla
    msgs_ok = {7: [{"message_type": 0, "content": "Hola?", "created_at": viejo},
                   {"message_type": 1, "content": "Aquí estoy", "created_at": int(time.time())}]}
    e1, _ = _run(convs, msgs_estancada)   # alerta
    e2, _ = _run(convs, msgs_ok)          # resuelta -> sale del set, silencio
    e3, _ = _run(convs, msgs_estancada)   # se vuelve a estancar -> alerta de nuevo
    assert len(e1) == 1 and len(e2) == 0 and len(e3) == 1


def test_carla_repitiendose_dispara_alerta_aunque_ella_sea_la_ultima_en_hablar():
    """Incidente 2026-08-21: con el daemon devolviendo 502, Carla repetía 'permítame un momento'
    sin parar. El último mensaje era suyo -> el watchdog daba la conversación por sana y el
    paciente nunca conseguía cita. Ese silencio duró horas."""
    _reset()
    ahora = int(time.time())
    guion_f = "Sr. Ramírez, permítame un momento, estoy registrando su cita."
    msgs = [
        {"message_type": 0, "content": "sí, confirmo", "created_at": ahora - 400},
        {"message_type": 1, "content": guion_f, "created_at": ahora - 300},
        {"message_type": 1, "content": guion_f, "created_at": ahora - 200},
        {"message_type": 1, "content": guion_f, "created_at": ahora - 100},
    ]
    enviados, _ = _run([{"id": 7}], {7: msgs})
    assert len(enviados) == 1
    assert "ATASCADA" in enviados[0]


def test_carla_contestando_cosas_distintas_no_es_bucle():
    _reset()
    ahora = int(time.time())
    msgs = [
        {"message_type": 0, "content": "hola", "created_at": ahora - 300},
        {"message_type": 1, "content": "¿Con quién tengo el gusto?", "created_at": ahora - 200},
        {"message_type": 1, "content": "¿Me indica su cédula?", "created_at": ahora - 100},
    ]
    enviados, _ = _run([{"id": 8}], {8: msgs})
    assert enviados == []


def _run_con_retry(convs, msgs_por_conv, retry_ok=True):
    """Como _run pero capturando el empujón automático a Carla."""
    enviados, empujones = [], []
    def _fake_retry(cid, phone):
        empujones.append((cid, phone))
        return retry_ok
    with patch("integrations.chatwoot.get_open_conversations", return_value=convs), \
         patch("integrations.chatwoot.get_conv_messages", side_effect=lambda cid: msgs_por_conv.get(cid, [])), \
         patch("integrations.chatwoot.is_bot_off", return_value=False), \
         patch("scheduler.watchdog._reintentar_conversacion", side_effect=_fake_retry), \
         patch("scheduler.watchdog.alerts.send_dev_alert", side_effect=enviados.append):
        watchdog._check_stalled_conversations({})
    return enviados, empujones


def test_conversacion_atascada_recibe_un_empujon_automatico():
    """Avisar por correo no basta: si nadie lo mira, el paciente se queda sin cita (conv 18 del
    incidente 2026-08-21). El watchdog intenta destrabarla solo, UNA vez."""
    _reset()
    ahora = int(time.time())
    guion_f = "Sra. Bastardo, permítame un momento, estoy registrando su cita."
    msgs = [
        {"message_type": 0, "content": "sí", "created_at": ahora - 400},
        {"message_type": 1, "content": guion_f, "created_at": ahora - 200},
        {"message_type": 1, "content": guion_f, "created_at": ahora - 100},
    ]
    conv = {"id": 18, "meta": {"sender": {"phone_number": "+18294753460"}}}
    enviados, empujones = _run_con_retry([conv], {18: msgs})
    assert empujones == [(18, "+18294753460")]
    assert any("ATASCADA" in m for m in enviados)
    assert any("empujón automático" in m for m in enviados)


def test_el_empujon_se_da_una_sola_vez():
    """Edge-trigger: en el ciclo siguiente, con la conversación igual de atascada, no se repite."""
    _reset()
    ahora = int(time.time())
    msgs = [
        {"message_type": 0, "content": "sí", "created_at": ahora - 400},
        {"message_type": 1, "content": "un momento", "created_at": ahora - 200},
        {"message_type": 1, "content": "un momento", "created_at": ahora - 100},
    ]
    conv = {"id": 18, "meta": {"sender": {"phone_number": "+18294753460"}}}
    _run_con_retry([conv], {18: msgs})
    _, empujones2 = _run_con_retry([conv], {18: msgs})
    assert empujones2 == []
