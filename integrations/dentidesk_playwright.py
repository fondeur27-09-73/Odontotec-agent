"""
Dentidesk vía Playwright — lo que la API NO cubre (crear y mover citas).

La API de Dentidesk solo LEE agenda y CAMBIA status (ver integrations/dentidesk.py). No hay endpoint
público para crear cliente, crear cita nueva, ni mover fecha/hora (existe un `agenda/createAgenda.php`
oculto, no documentado y no probado — ver memoria del proyecto). Por eso esto se automatiza sobre la
UI web (https://app.dentidesk.com) con Playwright.

IMPORTANTE — IMPORT PEREZOSO:
  Este módulo se importa desde agent/tool_handlers.py, que corre en el contenedor Docker de
  producción. Ese contenedor NO tiene Playwright instalado (decisión: no inflar la imagen ~400MB).
  Por eso `from playwright...` va DENTRO de las funciones, nunca al tope: así importar este módulo
  no rompe el agente aunque Playwright no esté presente. Solo las funciones de escritura (que solo
  se usan en el campo de simulación, con un entorno que SÍ tenga Playwright) lo importan.

DOS MODOS DE ESCRITURA (create_appointment/move_appointment):
  1. `DENTIDESK_DAEMON_URL` seteado (producción real, 2 contenedores) — delega por HTTP a la API
     interna del contenedor "daemon" (`scripts/dentidesk_daemon_api.py`), que sí tiene Playwright y
     un navegador persistente ya logueado. Este contenedor (el del agente) NO necesita Playwright
     ni toca CDP directo — solo hace un POST con `DENTIDESK_DAEMON_SECRET`. Modo recomendado en
     producción.
  2. Sin esa env — corre Playwright LOCALMENTE en este mismo proceso (necesita el navegador
     instalado). Dentro de este modo, `DENTIDESK_CDP_URL` (mismo host/contenedor que el daemon)
     evita relanzar+loguear; sin ninguna de las dos, lanza un navegador nuevo y loguea de cero
     (fallback, riesgo de reCAPTCHA — ver memoria dentidesk-playwright-bugs-2026-07-07).

CANDADO: toda acción de ESCRITURA exige DENTIDESK_ALLOW_WRITES=1. Sin ese env, se detiene ANTES de
abrir el navegador (o de llamar al daemon). Solo en el campo de simulación autorizado se activa.

SELECTORES — capturados 2026-06-28 inspeccionando la app real (login, pacientes.php → ficha.php,
agenda → editar cita, agenda → nueva cita) más el código fuente JS de home.php (funciones globales
open_modal_cita/clean_modal_cita/guardar_cita/load_data_cita). Documentado en memoria del proyecto
(dentidesk-api-contract). NUNCA se ha pulsado Guardar en producción — todo lo de abajo está
verificado hasta "abrir formulario con los datos correctos", NO verificado en el guardado real.

NOTA — campo nombre duplicado: el input VISIBLE del formulario de cita es #nombre_norden, pero
guardar_cita() (JS) lee el valor de un input OCULTO #nombre. Llenar solo #nombre_norden vía
page.fill NO sincroniza #nombre (probado: el evento 'input' sintético no dispara el binding real
de la UI) → hay que escribir #nombre explícitamente también (ver _set_patient_name_fields).

NOTA — bloqueos por doctor ("No agendar Dra. X" naranja en el calendario): son notas reales en la
BD (mismo sistema de modal, formulario distinto: título/observación/sucursal/profesional/fecha/
hora/duración), no un horario fijo configurable. Por ahora no hay forma de leerlos vía API; el
server SÍ los valida al guardar una cita (devuelve cupo_disp=0 → "Horario no disponible para el
dentista seleccionado"). Pendiente: función de solo-lectura que recorra la agenda y los liste.
"""
import os

LOGIN_URL = "https://app.dentidesk.com/home.php"
# El login WEB usa la cuenta de staff (ej h.geronimo), distinta de la cuenta API (produccion@cero.ai).
WEB_USER = os.getenv("DENTIDESK_WEB_USER", "")
WEB_PASS = os.getenv("DENTIDESK_WEB_PASS", "")
HEADLESS = os.getenv("DENTIDESK_PW_HEADLESS", "1").lower() in ("1", "true", "yes")


def _require_writes_enabled():
    if os.getenv("DENTIDESK_ALLOW_WRITES", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "Escritura a Dentidesk (Playwright) BLOQUEADA. Activa DENTIDESK_ALLOW_WRITES=1 solo en "
            "el campo de simulación autorizado. (Regla del cliente: nada de escrituras en dev.)"
        )


def _split_time_24h(time_str: str) -> tuple[str, str]:
    """Valida y separa una hora 24h 'HH:MM' -> ('HH', 'MM'). Rechaza cualquier otro formato
    ('10:00 AM', '3pm'): los selects #horac/#minutos toman valores numéricos y una hora con
    sufijo AM/PM o sin normalizar guardaría la cita a la hora equivocada (o reventaría)."""
    import re
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(time_str).strip())
    if not m:
        raise ValueError(
            f"Hora {time_str!r} inválida: se espera 24h 'HH:MM' (el caller normaliza con _to_24h)"
        )
    return m.group(1).zfill(2), m.group(2)


def _login(page):
    """Inicia sesión en la UI web. Selectores capturados 2026-06-27."""
    if not WEB_USER or not WEB_PASS:
        raise RuntimeError("Faltan DENTIDESK_WEB_USER / DENTIDESK_WEB_PASS en el entorno")
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.fill("#user-login", WEB_USER)
    page.fill("#pass-login", WEB_PASS)
    page.click("#btn_login")
    page.wait_for_load_state("networkidle")


def _ensure_logged_in(page) -> bool:
    """AUTO-RECUPERACIÓN de sesión. Si el navegador persistente del daemon quedó DESLOGUEADO (pasa
    tras un redeploy del daemon: reinicia Chrome y la sesión de Dentidesk se cae), la agenda redirige
    al formulario de login (#user-login). Antes eso hacía que create/move_appointment fallaran con
    502 en bucle y solo se arreglaba re-logueando a mano por VNC. Ahora, si detectamos el formulario
    de login, volvemos a loguear SOLOS con el perfil persistente — que por ser un device de confianza
    normalmente NO dispara reCAPTCHA. Devuelve True si tuvo que re-loguear (para re-navegar a la
    agenda). Si no hay formulario de login (sesión sana), no hace nada."""
    try:
        if page.locator("#user-login").count() > 0:
            _login(page)
            return True
    except Exception:
        # Un fallo aquí no debe enmascarar el error real del agendado: se sigue y, si de verdad no
        # hay sesión, el paso siguiente (open_modal_cita / tarjeta) fallará con un error visible.
        pass
    return False


CDP_URL = os.getenv("DENTIDESK_CDP_URL", "")  # ej "http://127.0.0.1:9222" -- mismo host/proceso
# DENTIDESK_DAEMON_URL: en producción real (agente en un contenedor, daemon en otro), en vez de
# CDP_URL se usa esto -- URL de la API HTTP interna del contenedor "daemon" (ver
# scripts/dentidesk_daemon_api.py), no del navegador directo. CDP es un protocolo SIN
# autenticación (control total del navegador logueado); exponerlo entre contenedores es
# inseguro, así que el daemon NUNCA expone su CDP fuera de sí mismo -- solo esta API, protegida
# por DENTIDESK_DAEMON_SECRET. Si está seteado, tiene prioridad sobre CDP_URL/login fresco.
DAEMON_URL = os.getenv("DENTIDESK_DAEMON_URL", "").rstrip("/")
DAEMON_SECRET = os.getenv("DENTIDESK_DAEMON_SECRET", "")


def _call_daemon(path: str, payload: dict) -> dict:
    """POST a la API interna del contenedor daemon (ver scripts/dentidesk_daemon_api.py)."""
    import httpx
    headers = {"X-Daemon-Secret": DAEMON_SECRET} if DAEMON_SECRET else {}
    r = httpx.post(f"{DAEMON_URL}{path}", json=payload, headers=headers, timeout=30.0)
    r.raise_for_status()
    return r.json()


def _open_write_page(p):
    """Abre la page que van a usar create_appointment/move_appointment para escribir.

    REGLA DEL CLIENTE (2026-07-07): la sesión web de Dentidesk NO expira — una vez logueada, se
    queda logueada indefinidamente. Por eso la forma correcta de escribir en producción es
    conectarse por CDP al navegador persistente YA LOGUEADO (`scripts/dentidesk_daemon.py`, login
    resuelto a mano UNA vez) en vez de lanzar un navegador nuevo y loguear de cero en cada llamada.
    Loguear de cero cada vez además dispara reCAPTCHA en modo headless sin nadie para resolverlo
    (BUG 2026-07-07, ver memoria dentidesk-playwright-bugs-2026-07-07) — con el navegador
    persistente ese problema desaparece porque el login solo pasa una vez.

    Si `DENTIDESK_CDP_URL` está seteado, se conecta a ese navegador y NO se loguea (ya lo está).
    Si no está seteado (tests, o entorno sin daemon corriendo), cae al modo viejo: navegador nuevo
    + login fresco — sigue disponible como fallback, con el riesgo de reCAPTCHA ya documentado.

    Devuelve `(page, cerrar)`; `cerrar()` hace lo correcto en cada modo (nunca cierra el navegador
    persistente ajeno del daemon)."""
    if CDP_URL:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        return page, (lambda: None)
    browser = p.chromium.launch(headless=HEADLESS, channel="chrome")
    page = browser.new_page()
    _login(page)
    return page, browser.close


PACIENTES_URL = "https://app.dentidesk.com/pacientes.php"


def _fill_new_patient_form(page, nombre: str, apellido: str, rut: str, fonocel: str,
                            email: str = "", doctor_label: str = "") -> None:
    """Llena el formulario 'Nuevo Paciente' (ficha.php). Selectores capturados 2026-06-28
    desde pacientes.php → #btn_paciente → ficha.php. NO pulsa Guardar (#btn_guardar_datos):
    eso queda a cargo del caller, bajo candado y solo el día de simulación.

    OJO: doctor_paciente / id_convenio / idioma son selects nativos detrás de un botón con
    estilo custom (chosen/select2-like) — page.select_option debería disparar el 'change' que
    sincroniza el botón visible, pero NO VERIFICADO (no se ha guardado nunca un registro real)."""
    page.goto(PACIENTES_URL, wait_until="networkidle")
    page.click("#btn_paciente")
    page.wait_for_load_state("networkidle")
    page.fill("#nombre", nombre)
    page.fill("#apellido", apellido)
    page.fill("#rut", rut)
    page.fill("#fonocel", fonocel)
    if email:
        page.fill("#email", email)
    if doctor_label:
        page.select_option("#doctor_paciente", label=doctor_label)  # NO VERIFICADO


AGENDA_URL = "https://app.dentidesk.com/home.php"

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}


def _goto_calendar_date(page, anio: int, mes: int, dia: int) -> None:
    """Navega el calendario principal (#calendar, FullCalendar) a una fecha arbitraria usando
    el minicalendario (#datepicker, jQuery UI Datepicker) — VERIFICADO en vivo 2026-06-28.

    `$('#calendar').fullCalendar('gotoDate', ...)` (intento anterior) está ROTO: manda la vista
    a 1 de Enero de 1970 (la firma asumida no es la real de esta versión de FullCalendar). El
    datepicker SÍ navega el calendario principal al hacer click en un día (confirmado: el header
    de #calendar pasa a mostrar la fecha clickeada)."""
    for _ in range(60):  # tope de seguridad, nunca deberian hacer falta mas de ~12 meses
        mes_actual = page.eval_on_selector(".ui-datepicker-month", "el => el.textContent.trim().toLowerCase()")
        anio_actual = int(page.eval_on_selector(".ui-datepicker-year", "el => el.textContent.trim()"))
        mes_actual_num = _MESES_ES[mes_actual]
        if (anio_actual, mes_actual_num) == (anio, mes):
            break
        ir_adelante = (anio_actual, mes_actual_num) < (anio, mes)
        selector = ".ui-datepicker-next" if ir_adelante else ".ui-datepicker-prev"
        page.click(selector)
        page.wait_for_timeout(150)
    else:
        raise RuntimeError(f"No se pudo navegar el minicalendario a {anio}-{mes:02d}")
    dia_str = str(dia)
    page.locator("#datepicker a", has_text=dia_str).filter(
        has_text=__import__("re").compile(rf"^{dia_str}$")
    ).first.click()
    page.wait_for_load_state("networkidle")


def _wait_doctors_loaded(page, cap_ms: int = 2500) -> None:
    """Espera a que el modal termine de cargar la lista de doctores (#dentista_cita) por ajax,
    en vez de dormir un tiempo fijo. Antes: wait_for_timeout(2000) siempre. Ahora: retorna apenas
    hay >1 opción (caso común, suele ser <2000ms) y como MÁXIMO espera cap_ms.

    Motivo del wait (bug 2026-07-01): si #dentista_cita se toca antes de que asiente esta carga,
    la carga tardía lo resetea al primer doctor SILENCIOSAMENTE. _select_doctor_verified (que corre
    después) igual verifica y reintenta, así que este wait es la primera línea, no la única."""
    try:
        page.wait_for_function(
            "() => { const s = document.getElementById('dentista_cita');"
            " return s && s.options.length > 1; }",
            timeout=cap_ms,
        )
    except Exception:
        # Si no cargó en el tope, seguimos: _select_doctor_verified detecta y aborta si el doctor
        # pedido no está, en vez de reservar con el equivocado.
        pass


def _set_patient_name_fields(page, nombre_completo: str) -> None:
    """Llena el nombre del paciente en AMBOS inputs que usa el formulario de cita: el visible
    #nombre_norden y el oculto #nombre (este último es el que guardar_cita() realmente lee)."""
    page.fill("#nombre_norden", nombre_completo)
    page.evaluate("(v) => { document.getElementById('nombre').value = v; }", nombre_completo)


def _select_sucursal_safe(page, sucursal: str) -> None:
    """Selecciona #sucursal_cita solo si hace falta. BUG 2026-07-07: esta cuenta solo tiene UNA
    sucursal (Arroyo Hondo) y el <select> nativo queda con esa opcion preseleccionada pero con
    layout colapsado (ancho/alto cero) -- page.select_option() cuelga ~30s esperando "visible" y
    nunca lo logra, aunque el valor ya sea el correcto. Comparar el value actual antes de tocarlo
    evita el cuelgue en el caso de 1 sola opcion, y sigue funcionando si el cliente agrega mas
    sucursales en el futuro (ese caso SI tiene el select interactivo)."""
    actual = page.eval_on_selector("#sucursal_cita", "el => el.value")
    if actual == str(sucursal):
        return
    page.select_option("#sucursal_cita", value=str(sucursal))


def _fill_cita_form(page, *, rut: str, fonocel: str, email: str, sucursal: str,
                     doctor_label: str, motivo_label: str, duracion_min: int) -> None:
    page.fill("#rut", rut)
    if email:
        page.fill("#email", email)
    if fonocel:
        page.fill("#fono", fonocel)
    _select_sucursal_safe(page, sucursal)
    # OJO: #dentista_cita NO se selecciona aqui a proposito -- ver _select_doctor_verified()
    # (bug real descubierto 2026-07-01: seleccionarlo temprano se pierde por una carga async
    # tardia del modal que resetea el campo al primer doctor de la lista).
    if motivo_label:
        _select_motivo_fuzzy(page, motivo_label)
    if duracion_min:
        page.fill("#largo", str(duracion_min))


def _select_motivo_fuzzy(page, motivo_label: str) -> None:
    """Selecciona #motivo por coincidencia flexible de texto (no exacta).

    `select_option(label=...)` exige el texto EXACTO de la opcion; en la prueba 2026-07-01
    "Consulta" no matcheo ninguna opcion (el label real difiere, ej "Consulta General") y el
    motivo quedo vacio. Aqui se busca la primera opcion cuyo texto CONTENGA motivo_label
    (case-insensitive) y se selecciona por value. Motivo no es obligatorio para guardar_cita()
    salvo plan_radiologico, asi que si no hay match no se rompe -- solo se deja sin seleccionar."""
    val = page.evaluate(
        """(needle) => {
            const sel = document.getElementById('motivo');
            if (!sel) return null;
            const n = needle.toLowerCase();
            const opt = Array.from(sel.options).find(
                o => !o.disabled && o.value && o.value !== '0'
                     && o.text.toLowerCase().includes(n));
            return opt ? opt.value : null;
        }""",
        motivo_label,
    )
    if val:
        page.select_option("#motivo", value=val)


_NORMALIZE_JS = """(s) => s.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim()"""


def _select_doctor_verified(page, doctor_label: str, attempts: int = 3) -> None:
    """Selecciona #dentista_cita por coincidencia FLEXIBLE y VERIFICA que quedo puesto,
    reintentando si algo lo reseteo.

    Coincidencia flexible (no `label=` exacto): el select lista los nombres tal cual estan en la
    BD del CRM ("ALTEMI CABRERA", "Dra. Altemi Cabrera Sime", con/sin tildes...), que no tienen
    por que coincidir letra a letra con la lista del cliente. Se busca la primera opcion cuyo
    texto normalizado (minusculas, sin tildes) CONTENGA doctor_label normalizado; si ninguna
    matchea se aborta con error claro en vez de guardar con el doctor que este puesto.

    BUG DESCUBIERTO 2026-07-01 (prueba real, cita de prueba): el modal de cita carga la lista de
    doctores por ajax; una seleccion hecha antes de que esa carga asiente (o interrumpida por
    otro cambio en el formulario) se pierde SILENCIOSAMENTE, quedando el primer doctor de la
    lista (alfabetico) en su lugar -- reservaria con el doctor equivocado sin ningun error.
    Por eso esto se llama AL FINAL, justo antes de #btn_guardar_cita, y se verifica el texto
    real del select despues de cada intento."""
    if not doctor_label:
        return
    find_option_js = """(needle) => {
        const norm = %s;
        const sel = document.getElementById('dentista_cita');
        if (!sel) return null;
        const n = norm(needle);
        const opt = Array.from(sel.options).find(
            o => !o.disabled && o.value && o.value !== '0' && norm(o.text).includes(n));
        return opt ? opt.value : null;
    }""" % _NORMALIZE_JS
    check_selected_js = """(needle) => {
        const norm = %s;
        const sel = document.getElementById('dentista_cita');
        const opt = sel.options[sel.selectedIndex];
        return opt && norm(opt.text).includes(norm(needle)) ? opt.text.trim() : null;
    }""" % _NORMALIZE_JS
    selected_text = None
    for _ in range(attempts):
        val = page.evaluate(find_option_js, doctor_label)
        if val is None:
            opciones = page.eval_on_selector(
                "#dentista_cita",
                "el => Array.from(el.options).map(o => o.text.trim()).filter(Boolean).join(' | ')",
            )
            raise RuntimeError(
                f"Doctor '{doctor_label}' no existe en #dentista_cita. Opciones: {opciones}"
            )
        page.select_option("#dentista_cita", value=val)
        page.wait_for_timeout(400)
        selected_text = page.evaluate(check_selected_js, doctor_label)
        if selected_text:
            return
    raise RuntimeError(
        f"No se pudo fijar el doctor '{doctor_label}' en #dentista_cita (se resetea solo "
        f"tras {attempts} intentos)"
    )


def _select_doctor_filter(page, doctor_needle: str) -> bool:
    """Selecciona el radio de #filtro_profesional (sidebar de home.php) cuyo texto matchea
    doctor_needle (fuzzy, sin acentos). BUG 2026-07-07: la vista semanal/dia de la agenda SOLO
    muestra las citas del doctor marcado en ese filtro -- buscar una tarjeta por texto sin
    seleccionar antes el doctor correcto falla en silencio (timeout) aunque la cita exista, en
    cuanto hay mas de un doctor involucrado. Devuelve False si no encontro el radio (deja el
    filtro como estaba, el caller decide si abortar)."""
    radio_id = page.evaluate(
        """(needle) => {
            const norm = s => s.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
            const n = norm(needle);
            const radios = Array.from(document.querySelectorAll('input[name=filtro_profesional]'));
            for (const r of radios) {
                const li = r.closest('li') || r.parentElement;
                if (li && norm(li.textContent).includes(n)) return r.id;
            }
            return null;
        }""",
        doctor_needle,
    )
    if not radio_id:
        return False
    page.check(f"#{radio_id}")
    page.wait_for_load_state("networkidle")
    return True


def _es_respuesta_guardar_cita(response) -> bool:
    """Filtro de `page.expect_response` para el ajax de guardado. BUG 2026-07-07: matchear solo
    por URL ("ajaxAgenda.php" in r.url) es demasiado amplio -- el cambio de #filtro_profesional
    (ver _select_doctor_filter) tambien pega a esa misma URL (devuelve la lista de profesionales) y,
    si esa respuesta llega justo despues de entrar al `with expect_response(...)`, se captura ESA
    en vez de la del guardado real -- se reporta fallo aunque el click a #btn_guardar_cita SI haya
    guardado la cita.

    Un primer intento filtrando por 'guardar_cita' en el post_data del REQUEST resultó poco
    confiable en pruebas reales (falso negativo verificado: la cita SÍ se guardó según
    get_agenda_day pero expect_response igual dio timeout). Filtrar por la FORMA de la RESPUESTA
    en vez del request es más robusto: la respuesta de guardar_cita trae 'id_agenda' en el JSON;
    la lista de profesionales es un array plano sin esa clave."""
    if "ajaxAgenda.php" not in response.url or response.request.method != "POST":
        return False
    try:
        data = response.json()
    except Exception:
        return False
    return isinstance(data, dict) and "id_agenda" in data


def create_appointment(
    cedula: str, patient_name: str, phone: str,
    doctor_label: str, fecha_iso: str, time: str,
    procedimiento: str = "", sucursal: str = "214",
) -> dict:
    """ESCRITURA (UI): crea una cita nueva en Dentidesk. Bajo candado DENTIDESK_ALLOW_WRITES.

    `doctor_label` es el nombre (o fragmento distintivo) del doctor a seleccionar en
    #dentista_cita — el caller (agent/tool_handlers.py) ya resolvió especialidad→doctor con
    _resolve_doctor(). La coincidencia contra las opciones reales del select es flexible
    (ver _select_doctor_verified).

    `time` DEBE venir en 24h 'HH:MM' (el caller normaliza con _to_24h). '3:00 PM' crudo aquí
    seleccionaría las 03:00 de la madrugada — por eso se valida y se rechaza."""
    _require_writes_enabled()
    if DAEMON_URL:
        # Modo producción (2 contenedores): delega al daemon por HTTP, sin tocar Playwright aquí.
        return _call_daemon("/crear_cita", {
            "cedula": cedula, "patient_name": patient_name, "phone": phone,
            "doctor_label": doctor_label, "fecha_iso": fecha_iso, "time": time,
            "procedimiento": procedimiento, "sucursal": sucursal,
        })
    from playwright.sync_api import sync_playwright  # import perezoso (ver cabecera)
    anio, mes, dia = fecha_iso[:10].split("-")
    hh, mm = _split_time_24h(time)
    with sync_playwright() as p:
        page, cerrar = _open_write_page(p)
        try:
            page.goto(AGENDA_URL, wait_until="networkidle")
            # Auto-recuperación: si la sesión se cayó (redeploy del daemon), re-loguea y vuelve a la
            # agenda antes de intentar abrir el modal (si no, window.open_modal_cita no existiría).
            if _ensure_logged_in(page):
                page.goto(AGENDA_URL, wait_until="networkidle")
            inicio = f"{anio}-{mes}-{dia} {hh}:{mm}"
            # Tras un login fresco (auto-recuperación), la agenda a veces aún no terminó de definir
            # moment.js ni window.open_modal_cita aunque networkidle ya disparó — llamarlos de una
            # vez tiraba "ReferenceError: moment is not defined" y la cita fallaba con 502. Espera a
            # que AMBOS globales existan antes de abrir el modal (resuelve en <1s si ya cargaron).
            page.wait_for_function(
                "() => typeof moment !== 'undefined' && typeof window.open_modal_cita === 'function'",
                timeout=15000,
            )
            page.evaluate(
                "([ini]) => window.open_modal_cita(moment(ini), moment(ini).add(30, 'minutes'))",
                [inicio],
            )
            page.wait_for_selector("#modal_cita.show, #modal_cita.in, #btn_guardar_cita",
                                   timeout=5000)
            # BUG DESCUBIERTO 2026-07-01 (prueba real): el modal recien abierto todavia carga la
            # lista de doctores por ajax; si #dentista_cita se selecciona demasiado pronto, esa
            # carga tardia LO RESETEA al primer doctor de la lista (Adriana Abreu) sin avisar,
            # silenciosamente reservando con el doctor equivocado. Verificado en vivo: esperar a
            # que esa carga asiente antes de tocar el campo evita el reset. Espera por CONDICIÓN
            # (lista de doctores cargada) en vez de 2000ms fijos: más rápido cuando carga rápido.
            _wait_doctors_loaded(page)
            _set_patient_name_fields(page, patient_name)
            _fill_cita_form(
                page, rut=cedula or "1-9", fonocel=phone, email="", sucursal=sucursal,
                doctor_label=doctor_label, motivo_label=procedimiento, duracion_min=30,
            )
            # Asegura fecha/hora exactas (open_modal_cita ya las precarga, esto es redundante a
            # propósito por si el caller pide algo distinto del slot inicial).
            page.select_option("#diacita", dia)
            page.select_option("#mescita", mes)
            page.select_option("#aniocita", anio)
            page.select_option("#horac", hh.zfill(2))
            page.select_option("#minutos", mm.zfill(2))
            # Doctor AL FINAL, justo antes de guardar: el select se resetea al primer doctor si se
            # toca antes de que asiente la carga async del modal (bug 2026-07-01). Verifica y aborta
            # si no queda fijo, en vez de reservar en silencio con el doctor equivocado.
            _select_doctor_verified(page, doctor_label)
            # Detecta si la cédula ya pertenece a un paciente registrado: Dentidesk autocompleta el
            # oculto #id_paciente al reconocer la cédula. Si trae valor, el paciente YA existe (no es
            # alta nueva) — se reporta al caller para que Carla vincule la ficha existente en vez de
            # intentar crear un duplicado (que el CRM rechaza). Informativo: no bloquea el agendado,
            # porque una cita para un paciente existente es válida. Ver memoria
            # dentidesk-prueba-agendado-2026-07-01.
            id_paciente_detectado = (page.input_value("#id_paciente") or "").strip()
            paciente_existe = bool(id_paciente_detectado and id_paciente_detectado != "0")
            with page.expect_response(_es_respuesta_guardar_cita, timeout=15000) as resp_info:
                page.click("#btn_guardar_cita")
            data = resp_info.value.json()
            # (Antes había un page.goto(AGENDA_URL, networkidle) aquí "para volver a Hoy": puro
            # desperdicio — la cita ya está guardada (data capturada) y el navegador se cierra en
            # el finally. Se quitó: ahorra un networkidle por agendado sin cambiar el resultado.)
            if not data.get("id_agenda"):
                return {"success": False, "error": "guardar_cita_fallo", "raw": data,
                        "paciente_existe": paciente_existe}
            return {"success": True, "IdAgenda": data.get("id_agenda"),
                    "IdPaciente": data.get("id_paciente"),
                    "paciente_existe": paciente_existe}
        finally:
            cerrar()


def move_appointment(
    id_agenda: str, fecha_actual_iso: str, patient_name: str,
    nueva_fecha_iso: str, nueva_hora: str, sucursal: str = "214", doctor_label: str = "",
    nuevo_doctor_label: str = "", nuevo_procedimiento: str = "",
) -> dict:
    """ESCRITURA (UI): mueve (reagenda) una cita existente a otra fecha/hora — la API no puede.
    Bajo candado DENTIDESK_ALLOW_WRITES.

    fecha_actual_iso/patient_name (de buscar_cita_dentidesk) son necesarios porque el IdAgenda NO
    se muestra en pantalla — hay que navegar al día correcto y clickear la tarjeta por nombre.

    `doctor_label` (de buscar_cita_dentidesk, campo "doctor") es el doctor ACTUAL de la cita, y es
    necesario porque la vista de la agenda filtra por UN doctor a la vez (ver _select_doctor_filter)
    — sin esto, la tarjeta del paciente no aparece si el filtro quedó en otro doctor (BUG 2026-07-07,
    silencioso: timeout sin explicar por qué). Si viene vacío (caso "general", personal fijo sin
    doctor asignado) se omite el filtro y se confía en lo que ya esté seleccionado.

    CAMBIO DE TRATAMIENTO (opcional): el modal de editar cita es el MISMO que el de crear, así que
    también se pueden cambiar el doctor (#dentista_cita) y el motivo (#motivo) al reagendar. Si
    `nuevo_procedimiento` viene, se actualiza #motivo; si `nuevo_doctor_label` viene (no vacío), se
    cambia el doctor al nuevo (el caller resolvió especialidad→doctor). Ambos se dejan tras
    fecha/hora y justo antes de guardar, reutilizando los mismos helpers verificados de create.

    Navegación de fecha vía minicalendario (_goto_calendar_date) — VERIFICADA en vivo 2026-06-28
    (el intento anterior con fullCalendar('gotoDate', ...) estaba ROTO, mandaba a 1 Enero 1970).
    El click por texto del paciente SÍ está verificado (2 veces, en Semana y Día, sobre citas
    reales)."""
    _require_writes_enabled()
    if DAEMON_URL:
        # Modo producción (2 contenedores): delega al daemon por HTTP, sin tocar Playwright aquí.
        return _call_daemon("/mover_cita", {
            "id_agenda": id_agenda, "fecha_actual_iso": fecha_actual_iso,
            "patient_name": patient_name, "nueva_fecha_iso": nueva_fecha_iso,
            "nueva_hora": nueva_hora, "sucursal": sucursal, "doctor_label": doctor_label,
            "nuevo_doctor_label": nuevo_doctor_label, "nuevo_procedimiento": nuevo_procedimiento,
        })
    from playwright.sync_api import sync_playwright  # import perezoso
    anio_act, mes_act, dia_act = fecha_actual_iso[:10].split("-")
    anio, mes, dia = nueva_fecha_iso[:10].split("-")
    hh, mm = _split_time_24h(nueva_hora)  # exige 24h 'HH:MM'; el caller normaliza con _to_24h
    with sync_playwright() as p:
        page, cerrar = _open_write_page(p)
        try:
            page.goto(AGENDA_URL, wait_until="networkidle")
            # Auto-recuperación de sesión (ver create_appointment / _ensure_logged_in).
            if _ensure_logged_in(page):
                page.goto(AGENDA_URL, wait_until="networkidle")
            if doctor_label:
                _select_doctor_filter(page, doctor_label)
            _goto_calendar_date(page, int(anio_act), int(mes_act), int(dia_act))
            page.locator(f"text={patient_name}").first.click(timeout=8000)
            page.wait_for_selector("#btn_guardar_cita", timeout=5000)
            # Salvaguarda: confirmar que el modal abierto es REALMENTE la cita pedida (nombre
            # repetido en otra fila/columna podría abrir una cita distinta con el mismo paciente).
            abierto = page.input_value("#id_agenda")
            if abierto and str(abierto) != str(id_agenda):
                return {"success": False, "error": "id_agenda_no_coincide",
                        "esperado": id_agenda, "abierto": abierto}
            page.select_option("#diacita", dia)
            page.select_option("#mescita", mes)
            page.select_option("#aniocita", anio)
            page.select_option("#horac", hh.zfill(2))
            page.select_option("#minutos", mm.zfill(2))
            # Cambio de tratamiento (si el paciente lo pidió): motivo primero, doctor AL FINAL
            # (mismo motivo que en create: el select de doctores se resetea si se toca antes de que
            # asiente su carga async — _select_doctor_verified verifica y reintenta).
            if nuevo_procedimiento:
                _select_motivo_fuzzy(page, nuevo_procedimiento)
            if nuevo_doctor_label:
                _wait_doctors_loaded(page)
                _select_doctor_verified(page, nuevo_doctor_label)
            with page.expect_response(_es_respuesta_guardar_cita, timeout=15000) as resp_info:
                page.click("#btn_guardar_cita")
            data = resp_info.value.json()
            # (goto AGENDA_URL redundante eliminado: la cita ya se movió y el navegador se cierra
            # en el finally. Ahorra un networkidle por reagendado.)
            if not data.get("id_agenda"):
                return {"success": False, "error": "guardar_cita_fallo", "raw": data}
            return {"success": True, "IdAgenda": data.get("id_agenda")}
        finally:
            cerrar()
