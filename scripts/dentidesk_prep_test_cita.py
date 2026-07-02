"""
Prepara (SIN guardar) una cita de prueba en el navegador del daemon ya logueado (CDP 9222).
Reusa el mismo codigo de selectores que integrations/dentidesk_playwright.py (create_appointment),
pero conectado via CDP a la sesion persistente en vez de lanzar un browser headless nuevo (evita
reCAPTCHA de un login fresco). Deja el modal de cita ABIERTO Y LISTO, con datos ficticios, sin
tocar #btn_guardar_cita -- eso lo dispara scripts/dentidesk_click_guardar.py cuando el usuario avise.

Uso:
    python scripts/dentidesk_prep_test_cita.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.dentidesk_playwright import (
    AGENDA_URL, _set_patient_name_fields, _fill_cita_form, _select_doctor_verified,
)

CDP_PORT = 9222

# Datos ficticios de prueba -- nombre marcado a proposito para reconocerla y borrarla despues.
TEST_NOMBRE = "PRUEBA BORRAR TEST"
TEST_CEDULA = "888-7776665-4"  # ficticia improbable (402-9999999-9 colisionaba con paciente real)
TEST_PHONE = "8095550199"
TEST_DOCTOR_LABEL = "Dra. Aimer Cedano"  # doctor real, label exacto incluye prefijo Dr./Dra.
TEST_SUCURSAL = "214"  # Arroyo Hondo
TEST_FECHA_ISO = "2026-07-10"
TEST_HORA = "11:00"
TEST_MOTIVO = "Primera vez en el centro"  # motivo real del dropdown (no existe "Consulta")


def main():
    from playwright.sync_api import sync_playwright

    anio, mes, dia = TEST_FECHA_ISO.split("-")
    hh, mm = TEST_HORA.split(":")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        t0 = time.perf_counter()  # arranca el cronometro del "agendar" (abrir + llenar modal)
        page.goto(AGENDA_URL, wait_until="networkidle")
        inicio = f"{anio}-{mes}-{dia} {hh}:{mm}"
        page.evaluate(
            "([ini]) => window.open_modal_cita(moment(ini), moment(ini).add(30, 'minutes'))",
            [inicio],
        )
        page.wait_for_selector("#modal_cita.show, #modal_cita.in, #btn_guardar_cita", timeout=5000)
        page.wait_for_timeout(2000)  # deja asentar la carga async de doctores (ver bug en dentidesk_playwright.py)

        _set_patient_name_fields(page, TEST_NOMBRE)
        _fill_cita_form(
            page, rut=TEST_CEDULA, fonocel=TEST_PHONE, email="", sucursal=TEST_SUCURSAL,
            doctor_label=TEST_DOCTOR_LABEL, motivo_label=TEST_MOTIVO, duracion_min=30,
        )
        page.select_option("#diacita", dia)
        page.select_option("#mescita", mes)
        page.select_option("#aniocita", anio)
        page.select_option("#horac", hh.zfill(2))
        page.select_option("#minutos", mm.zfill(2))
        # Doctor AL FINAL y verificado (mismo orden que create_appointment): evita el reset
        # silencioso de #dentista_cita al primer doctor de la lista (bug 2026-07-01).
        _select_doctor_verified(page, TEST_DOCTOR_LABEL)

        elapsed = time.perf_counter() - t0  # cronometro del agendado (sin Guardar)

        page.screenshot(path=os.path.join(os.path.dirname(__file__), "_out", "prep_test_cita.png"))

        veredicto = "PASS (< 3 min)" if elapsed < 180 else "FALLA (>= 3 min)"
        print("LISTO — modal de cita preparado, NO guardado.")
        print(f"  Paciente: {TEST_NOMBRE} | Cedula: {TEST_CEDULA} | Tel: {TEST_PHONE}")
        print(f"  Doctor: {TEST_DOCTOR_LABEL} (verificado fijo) | Sucursal: {TEST_SUCURSAL}")
        print(f"  Fecha/hora: {TEST_FECHA_ISO} {TEST_HORA} | Motivo: {TEST_MOTIVO}")
        print(f"  TIEMPO DE AGENDADO: {elapsed:.2f}s  ->  {veredicto}")
        print("  Screenshot: scripts/_out/prep_test_cita.png")
        print("  Navegador queda abierto con el modal visible. Esperando orden de Guardar.")
        # NO cerrar browser -- el daemon sigue vivo, NO se clickea #btn_guardar_cita aqui.


if __name__ == "__main__":
    main()
