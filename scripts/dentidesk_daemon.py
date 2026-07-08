"""
Lanza UN navegador Dentidesk que se queda abierto indefinidamente (puerto CDP 9222, SIEMPRE en
127.0.0.1 -- CDP no tiene autenticacion, nunca debe salir de este proceso/contenedor), para que
otros scripts (dentidesk_attach.py, dentidesk_bulk_*.py, o esta misma API en produccion) se
conecten las veces que haga falta SIN volver a abrir/cerrar ni pedir captcha de nuevo.

Uso local (exploracion manual):
    & ".venv\\Scripts\\Activate.ps1"
    python scripts/dentidesk_daemon.py

Uso en el contenedor "daemon" de produccion: DENTIDESK_DAEMON_API=1 hace que, despues del login,
en vez de quedarse en un loop de espera, levante tambien la API HTTP interna
(scripts/dentidesk_daemon_api.py) para que el agente le pida crear/mover citas por red.

REGLA (exploracion manual): este navegador es para EXPLORAR (click en lo que sea: agenda,
pacientes, doctores, intentar agendar/reagendar hasta el formulario). NUNCA pulsar Guardar /
cambiar status real fuera de los scripts de escritura ya auditados.
"""
import os
import sys
import time

OUT = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT, exist_ok=True)
# DENTIDESK_PROFILE_DIR: en produccion (contenedor "daemon") apunta a un volumen persistente
# (ej /data/dd_profile) para que el login sobreviva a un redeploy -- igual que odontotec-data
# para el SQLite. En local usa la carpeta de siempre bajo scripts/_out.
PROFILE = os.getenv("DENTIDESK_PROFILE_DIR", os.path.join(OUT, "dd_profile"))
LOGIN_URL = "https://app.dentidesk.com/home.php"
CDP_PORT = 9222
API_PORT = int(os.getenv("DENTIDESK_DAEMON_API_PORT", "8100"))


def _env():
    # En produccion (contenedor "daemon") EasyPanel inyecta las env vars directo al proceso, sin
    # archivo .env -- solo local (exploracion manual) tiene ese archivo. os.environ ya trae ambos casos.
    e = dict(os.environ)
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(p):
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
            PROFILE, headless=False, slow_mo=100, channel="chrome",
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
        try:
            # La Agenda de Dentidesk mantiene trafico de red constante (polling/websockets) que
            # rara vez llega a "networkidle" -- sin este try/except, el timeout tumbaba TODO el
            # daemon (excepcion no capturada -> proceso muere -> contenedor reinicia -> se pierde
            # la sesion recien logueada, pareciendo que "vuelve al login" en un loop infinito).
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        print(f"DAEMON LISTO. CDP en http://127.0.0.1:{CDP_PORT} | URL: {page.url}", flush=True)

        if os.getenv("DENTIDESK_DAEMON_API", "").lower() in ("1", "true", "yes"):
            # Modo produccion: sirve la API HTTP interna (scripts/dentidesk_daemon_api.py) en vez
            # de solo esperar. Bloquea aqui (uvicorn.run) -- el navegador de arriba se queda vivo
            # porque seguimos dentro del `with sync_playwright()`, uvicorn corre en este mismo
            # hilo/proceso sin pisar el loop interno de Playwright (hilo aparte).
            import uvicorn
            print(f"Sirviendo API interna en 0.0.0.0:{API_PORT} (DENTIDESK_DAEMON_API=1).",
                  flush=True)
            uvicorn.run("scripts.dentidesk_daemon_api:app", host="0.0.0.0", port=API_PORT)
            return

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
