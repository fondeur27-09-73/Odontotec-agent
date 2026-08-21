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


def _rut_field(cedula: str) -> str:
    """#rut es un campo RUT chileno: una cédula dominicana con guion (ej '0310277809-2') lo pone
    en rojo y NO deja guardar la cita; sin el guion ('03102778092') sí guarda (verificado en vivo
    2026-07-16). Dejamos solo dígitos. Cédula vacía -> '1-9', el placeholder que el propio
    Dentidesk sugiere en el formulario."""
    return "".join(c for c in cedula if c.isdigit()) or "1-9"


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


class SesionNoLogueada(RuntimeError):
    """El navegador persistente del daemon NO está logueado en Dentidesk (la agenda redirige al
    login). No es un error transitorio que se arregle reintentando: alguien tiene que iniciar sesión
    UNA vez a mano por VNC (el login exige resolver un reCAPTCHA de imágenes, imposible de
    automatizar — ver scripts/dentidesk_daemon.py). Se lanza para poder distinguir 'no logueado' de
    un fallo real del formulario y responder 409 (no 502) al agente."""


class CedulaAutocompletarHijack(RuntimeError):
    """El autocompletar de #rut enlazó un paciente DISTINTO al de la cédula tecleada.

    Pasa cuando la base tiene registros viejos con cédula CORTA: al teclear '03102778092' el
    dropdown ofrece por prefijo el registro de '0310', queda resaltado, y el Enter final —que
    debería solo cerrar la lista— SELECCIONA esa fila. Dentidesk sobrescribe #rut con la cédula
    corta y pone #id_paciente en la ficha equivocada.

    Sin este guard el agendado seguía adelante y creaba la cita en la ficha de OTRA persona, sin
    error visible: _type_rut_recognize devolvía True ('paciente reconocido') porque solo miraba que
    #id_paciente != 0, nunca DE QUIÉN. Se lanza ANTES de tocar #btn_guardar_cita — es preferible
    fallar ruidoso que reservar en la ficha equivocada."""


# La agenda logueada define moment.js y la global window.open_modal_cita; la página de login NO.
# Por eso "¿existe moment + open_modal_cita?" es un proxy fiable de "¿hay sesión?" — sirve tanto
# para el camino de crear (que sí los usa) como para el de mover (que trabaja por clicks).
_SESION_VIVA_JS = (
    "() => typeof moment !== 'undefined' && typeof window.open_modal_cita === 'function'"
)


def _session_live(page) -> bool:
    """True si la página actual es la agenda logueada (moment + open_modal_cita presentes)."""
    try:
        return bool(page.evaluate(_SESION_VIVA_JS))
    except Exception:
        return False


def _require_session_live(page) -> None:
    """Verifica que el navegador del daemon esté logueado ANTES de intentar escribir. Si no lo está,
    aborta con SesionNoLogueada y un mensaje que dice exactamente qué hacer — en vez de reventar más
    adelante con el 'moment is not defined' críptico que nos pasó 3 veces. Da un margen corto por si
    la agenda todavía está pintando tras el goto (resuelve en <1s si ya cargó)."""
    try:
        page.wait_for_function(_SESION_VIVA_JS, timeout=8000)
        return
    except Exception:
        pass
    hay_login = False
    try:
        hay_login = page.locator("#user-login").count() > 0
    except Exception:
        pass
    raise SesionNoLogueada(
        "El navegador del daemon NO está logueado en Dentidesk"
        + (" (está mostrando el formulario de login)." if hay_login else ".")
        + " Un humano debe iniciar sesión UNA vez por VNC (resolver el reCAPTCHA) y dejar la ventana"
        " abierta. Ver scripts/dentidesk_daemon.py y la memoria dentidesk-escalate-bug."
    )


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
    if r.status_code == 409:
        # El daemon respondió "sesión no logueada" (SesionNoLogueada). No es transitorio: no se
        # reintenta, hay que loguear a mano por VNC. Se devuelve estructurado para que el caller
        # (tool_handlers) le avise a Carla que la cita NO se registró, en vez de tumbar todo con 502.
        try:
            detalle = r.json().get("detail", "")
        except Exception:
            detalle = r.text
        return {"success": False, "error": "daemon_no_logueado", "detail": detalle,
                "message": "No pude registrar la cita en este momento por un problema técnico. "
                           "Un miembro del equipo la va a agendar enseguida y le confirmamos."}
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


# Busca en el dropdown del autocompletar la fila cuya cédula coincide EXACTA con la tecleada.
# Devuelve el elemento más PROFUNDO que la contiene (la fila, no el contenedor entero) para no
# clicar media pantalla. Solo dígitos: Dentidesk reformatea el RUT con puntos y guion.
_FILA_EXACTA_JS = r"""(digits) => {
    const soloDig = t => (t || '').replace(/\D/g, '');
    const visible = el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && el.offsetParent !== null;
    };
    const cand = Array.from(document.querySelectorAll('li, tr, td, div, span, a, p'))
        .filter(el => el.id !== 'rut' && visible(el) && soloDig(el.textContent).includes(digits));
    // el más profundo = el que no tiene ningún hijo que también matchee
    const hoja = cand.find(el => !cand.some(o => o !== el && el.contains(o)));
    if (!hoja) return false;
    hoja.setAttribute('data-carla-fila', '1');
    return true;
}"""


def _cerrar_autocompletar(page, tecleado: str) -> None:
    """Cierra el autocompletar de #rut SIN dejar que seleccione al paciente equivocado.

    BUG (producción 2026-08-21, bloqueó citas reales): antes esto era un `Enter` a ciegas. El Enter
    no solo cierra — si el dropdown dejó RESALTADA una fila, la SELECCIONA. Un registro viejo de
    cédula corta ('0310') matchea por PREFIJO a cualquier cédula de 11 dígitos que empiece igual
    (y '0310' es comunísimo en Santo Domingo), así que Dentidesk sobrescribía #rut con la cédula
    corta y enlazaba la ficha de otra persona. El guard lo detectaba y abortaba la cita — correcto,
    pero dejaba SIN PODER AGENDAR a todo paciente con ese prefijo.

    Ahora: si hay una fila cuya cédula es EXACTAMENTE la tecleada, se clica (enlaza al paciente
    correcto, que es lo que queremos). Si no la hay, se cierra con Escape SIN seleccionar nada →
    Dentidesk lo trata como paciente nuevo y crea la ficha con la cédula completa, que es lo
    correcto: el registro viejo de cédula corta es otra ficha, no ésta."""
    page.wait_for_timeout(600)   # dar tiempo al ajax del autocompletar
    try:
        if tecleado and page.evaluate(_FILA_EXACTA_JS, tecleado):
            page.click("[data-carla-fila='1']")
            page.wait_for_timeout(300)
            return
    except Exception:
        pass  # si el click falla, seguimos por el camino de Escape (nunca por el de Enter)
    # Ninguna fila coincide exacta: cerrar sin seleccionar. NUNCA Enter — aceptaría la resaltada.
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    # Escape puede dejar el campo vacío en algunos widgets: reponer tecleando (no con fill, que no
    # dispara los eventos que Dentidesk necesita para no marcar el campo en rojo).
    if tecleado and "".join(c for c in (page.input_value("#rut") or "") if c.isdigit()) != tecleado:
        page.fill("#rut", "")
        page.locator("#rut").press_sequentially(tecleado, delay=30)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)


def _type_rut_recognize(page, rut: str, cap_ms: int = 3000) -> bool:
    """Teclea la cédula en #rut como un humano y CIERRA la selección con Enter.

    BUG 2026-07-21 (confirmado en VNC): `page.fill("#rut", ...)` planta el value de golpe. Dentidesk
    nunca dispara el autocompletar que reconoce al paciente existente y llena el oculto #id_paciente,
    así que trata la cédula como RUT chileno NUEVO, corre el check-digit, la pone en rojo y bloquea el
    guardado -> el click en #btn_guardar_cita no produce respuesta -> expect_response cuelga 15s -> 502.
    Un humano teclea y CIERRA la selección con Enter (o click en la fila) -> el paciente carga y guarda.
    Esto replica ese cierre: press_sequentially teclea de verdad (dispara el evento) y Enter cierra.

    Devuelve True si reconoció un paciente existente (#id_paciente quedó != 0). Para un paciente NUEVO
    no hay fila que cerrar y devuelve False -> se guarda como alta nueva. OJO (probado 2026-07-22):
    Dentidesk NO rechaza la cédula dominicana por "RUT chileno" -- acepta cualquier dígito, sin mínimo
    ni check-digit (guardó un paciente con '123'). El único bloqueo real es cédula VACÍA. Por eso el
    guardrail de 11 dígitos vive en el agente (agent/tool_handlers._cedula_dominicana_ok), no aquí."""
    tecleado = "".join(c for c in rut if c.isdigit())
    page.fill("#rut", "")                                  # limpia por si trae algo
    page.locator("#rut").press_sequentially(rut, delay=30)  # teclea -> dispara autocompletar
    _cerrar_autocompletar(page, tecleado)
    quedo = "".join(c for c in (page.input_value("#rut") or "") if c.isdigit())
    if tecleado and quedo != tecleado:
        raise CedulaAutocompletarHijack(
            f"El autocompletar de Dentidesk cambió la cédula {tecleado} por {quedo or '(vacío)'}: "
            "hay un paciente viejo con cédula corta que matchea por prefijo. Agendar ahora crearía "
            "la cita en la ficha equivocada. Hay que borrar los registros de cédula incompleta."
        )
    try:
        page.wait_for_function(
            "() => { const el = document.getElementById('id_paciente');"
            " return el && el.value && el.value !== '0'; }",
            timeout=cap_ms,
        )
        return True
    except Exception:
        return False


def _fill_cita_form(page, *, rut: str, fonocel: str, email: str, sucursal: str,
                     doctor_label: str, motivo_label: str, duracion_min: int) -> None:
    _type_rut_recognize(page, rut)
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
        const opts = Array.from(sel.options).filter(
            o => !o.disabled && o.value && o.value !== '0');
        // Coincidencia EXACTA primero: si el needle es el nombre completo de una ficha
        // ("DOCTOR GENERAL", "DR. ORTODONCIA"), gana esa y no otra que solo la contenga.
        const opt = opts.find(o => norm(o.text) === n) ||
                    opts.find(o => norm(o.text).includes(n));
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
            # Chequeo de sesión ANTES de tocar nada: si el daemon no está logueado, la agenda es en
            # realidad el formulario de login (sin moment ni open_modal_cita) y todo lo de abajo
            # reventaría con 'moment is not defined'. Aquí aborta CLARO con SesionNoLogueada (→ 409),
            # que le dice al humano que loguee por VNC, en vez de fallar en bucle sin explicación.
            _require_session_live(page)
            inicio = f"{anio}-{mes}-{dia} {hh}:{mm}"
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
                page, rut=_rut_field(cedula), fonocel=phone, email="", sucursal=sucursal,
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
            # alta nueva) — se reporta al caller. OJO: Dentidesk SÍ permite duplicados (no valida
            # cédula única, probado 2026-07-22); el enlace a la ficha existente lo hace este mismo
            # teclear-cédula+Enter de _type_rut_recognize. Informativo: no bloquea el agendado,
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
            # Chequeo de sesión ANTES de tocar nada (ver create_appointment / _require_session_live):
            # si el daemon no está logueado, aborta CLARO con SesionNoLogueada en vez de fallar por
            # timeout buscando la tarjeta en una página de login.
            _require_session_live(page)
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


def session_status() -> dict:
    """SOLO LECTURA: ¿el navegador persistente del daemon está logueado en Dentidesk? Navega a la
    agenda y verifica la liveness de sesión (moment + open_modal_cita). No exige
    DENTIDESK_ALLOW_WRITES (no escribe nada) y no lanza SesionNoLogueada — devuelve el estado para
    que el endpoint /session lo reporte. Solo aplica en modo CDP (daemon)."""
    if not CDP_URL:
        raise RuntimeError(
            "session_status solo aplica en modo daemon/CDP (DENTIDESK_CDP_URL vacío)."
        )
    from playwright.sync_api import sync_playwright  # import perezoso
    with sync_playwright() as p:
        page, cerrar = _open_write_page(p)
        try:
            page.goto(AGENDA_URL, wait_until="networkidle")
            return {"logueado": _session_live(page)}
        finally:
            cerrar()
