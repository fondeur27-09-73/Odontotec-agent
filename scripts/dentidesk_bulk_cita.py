"""
Registra UNA cita real en Dentidesk reusando la sesion ya logueada del daemon (CDP 9222) --
evita relogin/recaptcha por cada paciente. Usa el mismo codigo de selectores verificado en
integrations/dentidesk_playwright.py (create_appointment) pero conectado via CDP en vez de
lanzar un browser nuevo.

CANDADO: exige DENTIDESK_ALLOW_WRITES=1 en .env -- si no esta, aborta antes de tocar nada.

Uso:
    python scripts/dentidesk_bulk_cita.py <nombre> <telefono> <doctor_needle> <motivo> <fecha_iso> <hora_24h>

Ej:
    python scripts/dentidesk_bulk_cita.py "Raimy Estefany De La Rosa Montero" 8299439291 "General General" "Operatoria Dental" 2026-07-08 09:00
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.dentidesk_playwright import (
    AGENDA_URL, _set_patient_name_fields, _fill_cita_form, _select_doctor_verified,
    _wait_doctors_loaded, _select_motivo_fuzzy,
)

CDP_PORT = 9222
OUT = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT, exist_ok=True)


def _allow_writes() -> bool:
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith("DENTIDESK_ALLOW_WRITES"):
            return line.partition("=")[2].strip() in ("1", "true", "True")
    return False


def main():
    if not _allow_writes():
        print("ABORTADO: candado activo. DENTIDESK_ALLOW_WRITES no esta en 1 (.env).")
        return 1

    nombre, telefono, doctor_needle, motivo, fecha_iso, hora = sys.argv[1:7]
    anio, mes, dia = fecha_iso.split("-")
    hh, mm = hora.split(":")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        t0 = time.perf_counter()
        page.goto(AGENDA_URL, wait_until="networkidle")
        inicio = f"{anio}-{mes}-{dia} {hh}:{mm}"
        page.evaluate(
            "([ini]) => window.open_modal_cita(moment(ini), moment(ini).add(30, 'minutes'))",
            [inicio],
        )
        page.wait_for_selector("#modal_cita.show, #modal_cita.in, #btn_guardar_cita", timeout=5000)
        _wait_doctors_loaded(page)

        _set_patient_name_fields(page, nombre)
        # rut = telefono a peticion del cliente (no se usa cedula real en esta prueba)
        page.fill("#rut", telefono)
        page.fill("#fono", telefono)
        # #sucursal_cita ya viene preseleccionado (unica sucursal, "214") -- no tocar, el select
        # nativo queda con layout colapsado y Playwright cuelga esperando visibilidad.
        page.wait_for_timeout(600)  # deja que Dentidesk busque/"tome" el cliente por telefono
        id_paciente_pre = (page.input_value("#id_paciente") or "").strip()
        paciente_existe = bool(id_paciente_pre and id_paciente_pre != "0")

        if motivo:
            _select_motivo_fuzzy(page, motivo)
        page.select_option("#diacita", dia)
        page.select_option("#mescita", mes)
        page.select_option("#aniocita", anio)
        page.select_option("#horac", hh.zfill(2))
        page.select_option("#minutos", mm.zfill(2))
        _select_doctor_verified(page, doctor_needle)

        page.screenshot(path=os.path.join(OUT, f"bulk_pre_{telefono}.png"))
        print(f"Cliente {'EXISTENTE (id=' + id_paciente_pre + ')' if paciente_existe else 'NUEVO'} detectado por telefono.")

        with page.expect_response(lambda r: "ajaxAgenda.php" in r.url, timeout=15000) as resp_info:
            page.click("#btn_guardar_cita")
        elapsed = time.perf_counter() - t0
        try:
            data = resp_info.value.json()
        except Exception:
            data = {"raw_text": resp_info.value.text()[:500]}

        page.screenshot(path=os.path.join(OUT, f"bulk_post_{telefono}.png"))

        ok = bool(isinstance(data, dict) and data.get("id_agenda") and str(data.get("id_agenda")) != "0")
        print(f"\nRESPUESTA guardar_cita ({elapsed:.2f}s):")
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        print(f"\n{'GUARDADO OK — IdAgenda=' + str(data.get('id_agenda')) if ok else 'NO se creo (revisar respuesta arriba)'}")
        print(f"TIEMPO TOTAL: {elapsed:.2f}s")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())