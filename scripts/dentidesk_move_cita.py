"""
REAGENDA (mueve fecha/hora) una cita existente en el navegador del daemon ya logueado (CDP 9222).
Cronometra lo que tarda el flujo completo (navegar a la fecha actual, ubicar la cita, abrir modal,
cambiar fecha/hora, Guardar) -- que es lo que haria Carla por cada reagenda.

Targetea la cita por su clase CSS `event_<IdAgenda>` en el calendario (FullCalendar) -> evita la
ambiguedad de dos citas con el mismo nombre de paciente. Salvaguarda: verifica #id_agenda del modal
abierto antes de tocar nada.

CANDADO: exige DENTIDESK_ALLOW_WRITES (se abre SOLO en este proceso via os.environ; el .env en disco
no se toca). Escritura real -> correr solo con aprobacion explicita.

El calendario FILTRA por profesional: la tarjeta de la cita solo aparece si su profesional esta
seleccionado en la barra lateral. Por eso se pasa el doctor_label y se selecciona primero (Carla
lo obtiene de la API getAgendaDay -> ProfessionalName).

Uso:
    python scripts/dentidesk_move_cita.py <IdAgenda> <fecha_actual_YYYY-MM-DD> <doctor_label> <nueva_YYYY-MM-DD> <HH:MM>
    (doctor_label exacto como aparece en la barra, ej "Dra. Aimer Cedano")
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CDP_PORT = 9222
OUT = os.path.join(os.path.dirname(__file__), "_out")


def _load_env():
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); os.environ[k.strip()] = v.strip()


def main():
    _load_env()
    os.environ["DENTIDESK_ALLOW_WRITES"] = "1"  # candado abierto SOLO en este proceso

    id_agenda = sys.argv[1]
    fecha_actual = sys.argv[2]           # YYYY-MM-DD (donde esta la cita hoy)
    doctor_label = sys.argv[3]           # ej "Dra. Aimer Cedano" (para seleccionar su columna)
    nueva_fecha = sys.argv[4]            # YYYY-MM-DD (destino)
    nueva_hora = sys.argv[5]             # HH:MM

    from integrations.dentidesk_playwright import AGENDA_URL
    from playwright.sync_api import sync_playwright

    ay, am, ad = fecha_actual.split("-")
    ny, nm, nd = nueva_fecha.split("-")
    hh, mm = nueva_hora.split(":")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        t0 = time.perf_counter()  # arranca cronometro del reagendado (ya logueado)
        # Salta directo a la fecha de la cita (el link de horas.php usa este mismo formato)
        page.goto(f"https://app.dentidesk.com/home.php?date={ad}/{am}/{ay}", wait_until="networkidle")
        # Selecciona el profesional para que su columna (y la tarjeta de la cita) se cargue
        page.locator(f"text={doctor_label}").first.click(timeout=8000)
        page.wait_for_timeout(1500)

        # Ubica la tarjeta exacta por su clase event_<IdAgenda> (sin ambiguedad de nombre)
        card = page.locator(f".event_{id_agenda}").first
        card.click(timeout=8000)
        page.wait_for_selector("#btn_guardar_cita", timeout=5000)

        abierto = page.input_value("#id_agenda")
        if str(abierto) != str(id_agenda):
            print(f"ABORTADO: el modal abierto es {abierto}, no {id_agenda}. No se toco nada.")
            return 1

        # Cambia fecha/hora destino
        page.select_option("#diacita", nd)
        page.select_option("#mescita", nm)
        page.select_option("#aniocita", ny)
        page.select_option("#horac", hh.zfill(2))
        page.select_option("#minutos", mm.zfill(2))

        with page.expect_response(lambda r: "ajaxAgenda.php" in r.url, timeout=20000) as resp_info:
            page.click("#btn_guardar_cita")
        elapsed = time.perf_counter() - t0
        try:
            data = resp_info.value.json()
        except Exception:
            data = {"raw_text": resp_info.value.text()[:400]}

        page.goto(AGENDA_URL, wait_until="networkidle")  # vuelve a Agenda al terminar
        page.screenshot(path=os.path.join(OUT, f"move_{id_agenda}.png"))

        ok = bool(isinstance(data, dict) and str(data.get("id_agenda")) == str(id_agenda))
        import json
        print(f"REAGENDA IdAgenda={id_agenda}: {fecha_actual} -> {nueva_fecha} {nueva_hora}")
        print(f"  Respuesta: {json.dumps(data, ensure_ascii=False)}")
        print(f"  TIEMPO REAGENDADO: {elapsed:.2f}s")
        print(f"  {'OK — cita movida' if ok else 'REVISAR respuesta (id_agenda no coincide)'}")
        print(f"  Screenshot: scripts/_out/move_{id_agenda}.png")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
