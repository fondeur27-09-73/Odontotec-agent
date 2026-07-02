"""
Lanza UN navegador Dentidesk que se queda abierto indefinidamente (puerto CDP 9222), para que
scripts/dentidesk_attach.py se conecte a el las veces que haga falta SIN volver a abrir/cerrar
ni pedir captcha de nuevo.

Uso:
    & ".venv\\Scripts\\Activate.ps1"
    python scripts/dentidesk_daemon.py

REGLA: este navegador es para EXPLORAR (click en lo que sea: agenda, pacientes, doctores,
intentar agendar/reagendar hasta el formulario). NUNCA pulsar Guardar / cambiar status real.
"""
import os
import sys
import time

OUT = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT, exist_ok=True)
PROFILE = os.path.join(OUT, "dd_profile")
LOGIN_URL = "https://app.dentidesk.com/home.php"
CDP_PORT = 9222


def _env():
    e = {}
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); e[k.strip()] = v.strip()
    return e


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True)
    e = _env()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, slow_mo=100,
            args=[f"--remote-debugging-port={CDP_PORT}", "--start-maximized", "--new-window"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.bring_to_front()
        if "Inicio de Sesi" in page.title():
            try:
                page.fill("#user-login", e.get("DENTIDESK_WEB_USER", ""))
                page.fill("#pass-login", e.get("DENTIDESK_WEB_PASS", ""))
            except Exception:
                pass
            print(">>> En la ventana: marca 'No soy un robot' y pulsa 'Iniciar sesion'. (180s)",
                  flush=True)
            t_captcha = time.perf_counter()  # cronometro: desde que aparece el captcha/login
            try:
                page.wait_for_function("() => !document.title.includes('Inicio de Sesi')",
                                       timeout=180000)
                print(f">>> LOGIN OK — captcha+login tardo {time.perf_counter() - t_captcha:.1f}s",
                      flush=True)
            except Exception:
                print("No se detecto login (timeout). Daemon sigue corriendo igual; reintenta "
                      "manualmente en la ventana.", flush=True)
        page.wait_for_load_state("networkidle")
        print(f"DAEMON LISTO. CDP en http://127.0.0.1:{CDP_PORT} | URL: {page.url}", flush=True)
        print("Navegador se queda abierto. Usa scripts/dentidesk_attach.py <step> para explorar.",
              flush=True)
        print("Para detenerlo: Ctrl+C aqui, o cierra la ventana a mano.", flush=True)
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            print("Cerrando daemon (Ctrl+C).", flush=True)


if __name__ == "__main__":
    main()
