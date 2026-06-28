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

CANDADO: toda acción de ESCRITURA exige DENTIDESK_ALLOW_WRITES=1. Sin ese env, se detiene ANTES de
abrir el navegador. Solo en el campo de simulación autorizado se activa.

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


def _login(page):
    """Inicia sesión en la UI web. Selectores capturados 2026-06-27."""
    if not WEB_USER or not WEB_PASS:
        raise RuntimeError("Faltan DENTIDESK_WEB_USER / DENTIDESK_WEB_PASS en el entorno")
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.fill("#user-login", WEB_USER)
    page.fill("#pass-login", WEB_PASS)
    page.click("#btn_login")
    page.wait_for_load_state("networkidle")


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


def _set_patient_name_fields(page, nombre_completo: str) -> None:
    """Llena el nombre del paciente en AMBOS inputs que usa el formulario de cita: el visible
    #nombre_norden y el oculto #nombre (este último es el que guardar_cita() realmente lee)."""
    page.fill("#nombre_norden", nombre_completo)
    page.evaluate("(v) => { document.getElementById('nombre').value = v; }", nombre_completo)


def _fill_cita_form(page, *, rut: str, fonocel: str, email: str, sucursal: str,
                     doctor_label: str, motivo_label: str, duracion_min: int) -> None:
    page.fill("#rut", rut)
    if email:
        page.fill("#email", email)
    if fonocel:
        page.fill("#fono", fonocel)
    page.select_option("#sucursal_cita", value=str(sucursal))
    if doctor_label:
        page.select_option("#dentista_cita", label=doctor_label)  # NO VERIFICADO en guardado real
    if motivo_label:
        try:
            page.select_option("#motivo", label=motivo_label)
        except Exception:
            pass  # motivo no es obligatorio para guardar_cita() salvo plan_radiologico
    if duracion_min:
        page.fill("#largo", str(duracion_min))


def create_appointment(
    cedula: str, patient_name: str, phone: str,
    specialty: str, fecha_iso: str, time: str,
    procedimiento: str = "", sucursal: str = "214",
) -> dict:
    """ESCRITURA (UI): crea una cita nueva en Dentidesk. Bajo candado DENTIDESK_ALLOW_WRITES.

    OJO — selección de doctor: `specialty` se usa como label contra el <select id="dentista_cita">,
    que lista NOMBRES de doctor, no especialidades. Esto solo funciona si el caller ya resolvió
    `specialty` a un nombre real de doctor (ver Profesionales en agenda). Mapear especialidad→doctor
    disponible es una decisión de negocio pendiente, fuera del alcance de este mapeo de selectores.
    """
    _require_writes_enabled()
    from playwright.sync_api import sync_playwright  # import perezoso (ver cabecera)
    anio, mes, dia = fecha_iso[:10].split("-")
    hh, mm = (time.split(":") + ["00"])[:2]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            _login(page)
            page.goto(AGENDA_URL, wait_until="networkidle")
            inicio = f"{anio}-{mes}-{dia} {hh}:{mm}"
            page.evaluate(
                "([ini]) => window.open_modal_cita(moment(ini), moment(ini).add(30, 'minutes'))",
                [inicio],
            )
            page.wait_for_selector("#modal_cita.show, #modal_cita.in, #btn_guardar_cita",
                                   timeout=5000)
            _set_patient_name_fields(page, patient_name)
            _fill_cita_form(
                page, rut=cedula or "1-9", fonocel=phone, email="", sucursal=sucursal,
                doctor_label=specialty, motivo_label=procedimiento, duracion_min=30,
            )
            # Asegura fecha/hora exactas (open_modal_cita ya las precarga, esto es redundante a
            # propósito por si el caller pide algo distinto del slot inicial).
            page.select_option("#diacita", dia)
            page.select_option("#mescita", mes)
            page.select_option("#aniocita", anio)
            page.select_option("#horac", hh.zfill(2))
            page.select_option("#minutos", mm.zfill(2))
            with page.expect_response(lambda r: "ajaxAgenda.php" in r.url, timeout=15000) as resp_info:
                page.click("#btn_guardar_cita")
            data = resp_info.value.json()
            page.goto(AGENDA_URL, wait_until="networkidle")  # vuelve a Agenda/Hoy al terminar
            if not data.get("id_agenda"):
                return {"success": False, "error": "guardar_cita_fallo", "raw": data}
            return {"success": True, "IdAgenda": data.get("id_agenda"),
                    "IdPaciente": data.get("id_paciente")}
        finally:
            browser.close()


def move_appointment(
    id_agenda: str, fecha_actual_iso: str, patient_name: str,
    nueva_fecha_iso: str, nueva_hora: str, sucursal: str = "214",
) -> dict:
    """ESCRITURA (UI): mueve (reagenda) una cita existente a otra fecha/hora — la API no puede.
    Bajo candado DENTIDESK_ALLOW_WRITES.

    fecha_actual_iso/patient_name (de buscar_cita_dentidesk) son necesarios porque el IdAgenda NO
    se muestra en pantalla — hay que navegar al día correcto y clickear la tarjeta por nombre.

    Navegación de fecha vía minicalendario (_goto_calendar_date) — VERIFICADA en vivo 2026-06-28
    (el intento anterior con fullCalendar('gotoDate', ...) estaba ROTO, mandaba a 1 Enero 1970).
    El click por texto del paciente SÍ está verificado (2 veces, en Semana y Día, sobre citas
    reales)."""
    _require_writes_enabled()
    from playwright.sync_api import sync_playwright  # import perezoso
    anio_act, mes_act, dia_act = fecha_actual_iso[:10].split("-")
    anio, mes, dia = nueva_fecha_iso[:10].split("-")
    hh, mm = (nueva_hora.split(":") + ["00"])[:2]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            _login(page)
            page.goto(AGENDA_URL, wait_until="networkidle")
            _goto_calendar_date(page, int(anio_act), int(mes_act), int(dia_act))
            page.locator(f"text={patient_name}").first.click(timeout=8000)
            page.wait_for_selector("#btn_guardar_cita", timeout=5000)
            # Salvaguarda: confirmar que el modal abierto es REALMENTE la cita pedida (nombre
            # repetido en otra fila/columna podría abrir una cita distinta con el mismo paciente).
            abierto = page.input_value("#id_agenda")
            if abierto and str(abierto) != str(id_agenda):
                page.click("text=Cerrar")
                page.goto(AGENDA_URL, wait_until="networkidle")  # vuelve a Agenda/Hoy al terminar
                return {"success": False, "error": "id_agenda_no_coincide",
                        "esperado": id_agenda, "abierto": abierto}
            page.select_option("#diacita", dia)
            page.select_option("#mescita", mes)
            page.select_option("#aniocita", anio)
            page.select_option("#horac", hh.zfill(2))
            page.select_option("#minutos", mm.zfill(2))
            with page.expect_response(lambda r: "ajaxAgenda.php" in r.url, timeout=15000) as resp_info:
                page.click("#btn_guardar_cita")
            data = resp_info.value.json()
            page.goto(AGENDA_URL, wait_until="networkidle")  # vuelve a Agenda/Hoy al terminar
            if not data.get("id_agenda"):
                return {"success": False, "error": "guardar_cita_fallo", "raw": data}
            return {"success": True, "IdAgenda": data.get("id_agenda")}
        finally:
            browser.close()
