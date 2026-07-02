"""
GUARDA REAL la cita que dentidesk_prep_test_cita.py dejo lista en el navegador del daemon (CDP 9222).
Pulsa #btn_guardar_cita de verdad y captura la respuesta ajax (ajaxAgenda.php accion=guardar_cita)
para leer el IdAgenda creado y sondear los campos que acepta el servidor.

CANDADO: exige DENTIDESK_ALLOW_WRITES=1 en .env. Sin eso NO clickea nada (aborta antes de tocar el
boton). Este es el UNICO script que escribe en el CRM real -- correr solo con aprobacion explicita.

Uso:
    python scripts/dentidesk_click_guardar.py
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CDP_PORT = 9222
OUT = os.path.join(os.path.dirname(__file__), "_out")


def _allow_writes() -> bool:
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith("DENTIDESK_ALLOW_WRITES"):
            return line.partition("=")[2].strip() in ("1", "true", "True")
    return False


def _read_modal_state(page):
    return page.evaluate(
        """() => {
            const g = id => { const e = document.getElementById(id); return e ? e.value : null; };
            const sel = id => {
                const e = document.getElementById(id);
                return e && e.selectedIndex >= 0 ? e.options[e.selectedIndex].text : null;
            };
            return {
                nombre: g('nombre'), nombre_visible: g('nombre_norden'), rut: g('rut'),
                id_paciente: g('id_paciente'), fono: g('fono'),
                doctor: sel('dentista_cita'), motivo: sel('motivo'),
                sucursal: sel('sucursal_cita'),
                dia: g('diacita'), mes: g('mescita'), anio: g('aniocita'),
                hora: g('horac'), min: g('minutos'), largo: g('largo'),
                guardar_visible: !!document.getElementById('btn_guardar_cita'),
            };
        }"""
    )


def main():
    if not _allow_writes():
        print("ABORTADO: candado activo. DENTIDESK_ALLOW_WRITES no esta en 1 (.env).")
        print("Setea DENTIDESK_ALLOW_WRITES=1 SOLO con aprobacion explicita para guardar de verdad.")
        return 1

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        st = _read_modal_state(page)
        if not st.get("guardar_visible"):
            print("ABORTADO: no hay modal de cita abierto (#btn_guardar_cita no existe).")
            print("Corre primero scripts/dentidesk_prep_test_cita.py.")
            return 1

        print("A punto de GUARDAR esta cita:")
        print(f"  Paciente: {st['nombre_visible']} | Cedula: {st['rut']} | "
              f"id_paciente(existente?): {st['id_paciente'] or 'vacio=nuevo'}")
        print(f"  Doctor: {st['doctor']} | Motivo: {st['motivo']} | Sucursal: {st['sucursal']}")
        print(f"  Fecha: {st['dia']}/{st['mes']}/{st['anio']} {st['hora']}:{st['min']} | "
              f"Duracion: {st['largo']} min")

        t0 = time.perf_counter()
        with page.expect_response(lambda r: "ajaxAgenda.php" in r.url, timeout=20000) as resp_info:
            page.click("#btn_guardar_cita")
        elapsed = time.perf_counter() - t0
        try:
            data = resp_info.value.json()
        except Exception:
            data = {"raw_text": resp_info.value.text()[:500]}

        page.screenshot(path=os.path.join(OUT, "guardar_result.png"))

        print(f"\nRESPUESTA guardar_cita ({elapsed:.2f}s):")
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        ok = bool(isinstance(data, dict) and data.get("id_agenda") and str(data.get("id_agenda")) != "0")
        print(f"\n{'GUARDADO OK — IdAgenda=' + str(data.get('id_agenda')) if ok else 'NO se creo (revisar respuesta arriba)'}")
        print("Screenshot: scripts/_out/guardar_result.png")
        # NO cerrar browser -- el daemon sigue vivo.
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
