"""
Reagenda (mueve) UNA cita real en Dentidesk reusando la sesion ya logueada del daemon (CDP 9222).
Misma logica que integrations.dentidesk_playwright.move_appointment pero conectado via CDP.

CANDADO: exige DENTIDESK_ALLOW_WRITES=1 en .env -- si no esta, aborta antes de tocar nada.

Uso:
    python scripts/dentidesk_bulk_move.py <id_agenda> <fecha_actual_iso> <patient_name> <nueva_fecha_iso> <nueva_hora> <doctor_needle>

NOTA: la vista semanal/dia de la agenda filtra por UN doctor a la vez (radios #filtro_profesional).
Hay que seleccionar el radio del doctor de la cita ANTES de buscar la tarjeta por texto, si no,
el paciente no aparece aunque la cita exista (queda oculta bajo el filtro de otro doctor).
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.dentidesk_playwright import AGENDA_URL, _goto_calendar_date

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

    id_agenda, fecha_actual_iso, patient_name, nueva_fecha_iso, nueva_hora, doctor_needle = sys.argv[1:7]
    anio_act, mes_act, dia_act = fecha_actual_iso[:10].split("-")
    anio, mes, dia = nueva_fecha_iso[:10].split("-")
    hh, mm = nueva_hora.split(":")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        t0 = time.perf_counter()
        page.goto(AGENDA_URL, wait_until="networkidle")

        # Selecciona el radio de #filtro_profesional que matchea doctor_needle (fuzzy, sin acentos)
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
            print(f"ABORTADO: doctor '{doctor_needle}' no encontrado en filtro_profesional.")
            return 1
        page.check(f"#{radio_id}")
        page.wait_for_load_state("networkidle")

        _goto_calendar_date(page, int(anio_act), int(mes_act), int(dia_act))
        page.locator(f"text={patient_name}").first.click(timeout=8000)
        page.wait_for_selector("#btn_guardar_cita", timeout=5000)

        abierto = page.input_value("#id_agenda")
        if abierto and str(abierto) != str(id_agenda):
            print(json.dumps({"success": False, "error": "id_agenda_no_coincide",
                               "esperado": id_agenda, "abierto": abierto}, ensure_ascii=False))
            return 1

        page.select_option("#diacita", dia)
        page.select_option("#mescita", mes)
        page.select_option("#aniocita", anio)
        page.select_option("#horac", hh.zfill(2))
        page.select_option("#minutos", mm.zfill(2))

        def _es_respuesta_guardar(r):
            if "ajaxAgenda.php" not in r.url or r.request.method != "POST":
                return False
            post_data = r.request.post_data or ""
            return "guardar_cita" in post_data

        with page.expect_response(_es_respuesta_guardar, timeout=15000) as resp_info:
            page.click("#btn_guardar_cita")
        elapsed = time.perf_counter() - t0
        try:
            data = resp_info.value.json()
        except Exception:
            data = {"raw_text": resp_info.value.text()[:500]}

        page.screenshot(path=os.path.join(OUT, f"move_{id_agenda}.png"))

        ok = bool(isinstance(data, dict) and data.get("id_agenda") and str(data.get("id_agenda")) != "0")
        print(f"RESPUESTA move ({elapsed:.2f}s): {json.dumps(data, ensure_ascii=False, default=str)}")
        print(f"{'MOVIDO OK' if ok else 'NO se movio'} | TIEMPO TOTAL: {elapsed:.2f}s")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
