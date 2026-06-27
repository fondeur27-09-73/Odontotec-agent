"""
Dentidesk vía Playwright — lo que la API NO cubre (crear y mover citas).

La API de Dentidesk solo LEE agenda y CAMBIA status (ver integrations/dentidesk.py). No hay endpoint
para crear cliente, crear cita nueva, ni mover fecha/hora. Eso se automatiza sobre la UI web
(https://app.dentidesk.com) con Playwright.

IMPORTANTE — IMPORT PEREZOSO:
  Este módulo se importa desde agent/tool_handlers.py, que corre en el contenedor Docker de
  producción. Ese contenedor NO tiene Playwright instalado (decisión: no inflar la imagen ~400MB).
  Por eso `from playwright...` va DENTRO de las funciones, nunca al tope: así importar este módulo
  no rompe el agente aunque Playwright no esté presente. Solo las funciones de escritura (que solo
  se usan en el campo de simulación, con un entorno que SÍ tenga Playwright) lo importan.

CANDADO: toda acción de ESCRITURA exige DENTIDESK_ALLOW_WRITES=1. Sin ese env, se detiene ANTES de
abrir el navegador. Solo en el campo de simulación autorizado se activa.

SELECTORES PENDIENTES:
  Los page.fill/page.click reales se capturan el día de la simulación con:
      & ".venv\\Scripts\\Activate.ps1"
      python scripts/dentidesk_codegen.py        # abre codegen sobre la UI logueada
  Recorrer "nueva cita" y "reagendar" SIN pulsar guardar, copiar los selectores que imprime codegen
  y pegarlos en los bloques marcados TODO(codegen) más abajo.
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
    """Inicia sesión en la UI web. SELECTORES PENDIENTES (capturar con codegen)."""
    if not WEB_USER or not WEB_PASS:
        raise RuntimeError("Faltan DENTIDESK_WEB_USER / DENTIDESK_WEB_PASS en el entorno")
    page.goto(LOGIN_URL)
    # TODO(codegen): page.fill("<selector usuario>", WEB_USER)
    # TODO(codegen): page.fill("<selector clave>", WEB_PASS)
    # TODO(codegen): page.click("<selector botón entrar>")
    # TODO(codegen): page.wait_for_url("**/agenda**")  # o el landing real tras login
    raise NotImplementedError("Selectores de login pendientes de capturar con codegen")


def create_appointment(
    cedula: str, patient_name: str, phone: str,
    specialty: str, fecha_iso: str, time: str,
    procedimiento: str = "", sucursal: str = "214",
) -> dict:
    """ESCRITURA (UI): crea una cita nueva en Dentidesk. Bajo candado DENTIDESK_ALLOW_WRITES.
    No operativo hasta capturar selectores (TODO(codegen))."""
    _require_writes_enabled()
    from playwright.sync_api import sync_playwright  # import perezoso (ver cabecera)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            _login(page)
            # TODO(codegen): abrir "nueva cita", rellenar cédula/nombre/teléfono/especialidad/
            #   fecha/hora/procedimiento y GUARDAR. Devolver el IdAgenda creado si la UI lo expone.
            raise NotImplementedError("create_appointment: selectores pendientes (codegen)")
        finally:
            browser.close()


def move_appointment(
    id_agenda: str, nueva_fecha_iso: str, nueva_hora: str, sucursal: str = "214",
) -> dict:
    """ESCRITURA (UI): mueve (reagenda) una cita existente a otra fecha/hora — la API no puede.
    Bajo candado DENTIDESK_ALLOW_WRITES. No operativo hasta capturar selectores."""
    _require_writes_enabled()
    from playwright.sync_api import sync_playwright  # import perezoso
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            _login(page)
            # TODO(codegen): buscar la cita (por id_agenda/paciente), abrir editar, cambiar
            #   fecha/hora y GUARDAR.
            raise NotImplementedError("move_appointment: selectores pendientes (codegen)")
        finally:
            browser.close()
