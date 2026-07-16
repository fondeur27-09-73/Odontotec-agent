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
import json
import os
import sys
import time

OUT = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT, exist_ok=True)
# DENTIDESK_PROFILE_DIR: en produccion (contenedor "daemon") apunta a un volumen persistente
# (ej /data/dd_profile) para que el login sobreviva a un redeploy -- igual que odontotec-data
# para el SQLite. En local usa la carpeta de siempre bajo scripts/_out.
PROFILE = os.getenv("DENTIDESK_PROFILE_DIR", os.path.join(OUT, "dd_profile"))
# La cookie de sesion PHP de Dentidesk es una *session cookie*: Chrome NO la escribe a disco
# aunque el perfil viva en el volumen, asi que muere cuando el contenedor reinicia y pide
# reCAPTCHA de nuevo. La volcamos a un JSON en el mismo volumen (/data/cookies.json en prod) y
# la restauramos al arrancar. Como la sesion server-side no expira (regla del cliente), revive
# el login sin captcha.
COOKIES = os.getenv("DENTIDESK_COOKIES_FILE", os.path.join(os.path.dirname(PROFILE), "cookies.json"))
LOGIN_URL = "https://app.dentidesk.com/home.php"
CDP_PORT = 9222
API_PORT = int(os.getenv("DENTIDESK_DAEMON_API_PORT", "8100"))


def _load_cookies(ctx):
    # Prioridad 1: archivo /data/cookies.json — lo reescribe _save_cookies en CADA login exitoso,
    # asi que siempre trae la cookie MAS reciente y, clave, generada DENTRO del contenedor (misma
    # IP/User-Agent del VPS). Si Dentidesk ata la sesion a la IP (hipotesis 2026-07-16), solo una
    # cookie nacida aqui aguanta -- una pegada desde otra maquina la rechaza.
    # Prioridad 2 (fallback): env var DENTIDESK_COOKIES_JSON. Util solo si viniera de una sesion
    # del mismo origen; NO debe tapar al archivo (bug corregido 2026-07-16: antes iba primero y
    # bloqueaba la cookie buena del volumen).
    if os.path.exists(COOKIES):
        try:
            ctx.add_cookies(json.load(open(COOKIES, encoding="utf-8")))
            print(f">>> Cookies restauradas desde {COOKIES}", flush=True)
            return
        except Exception as ex:
            print(f">>> {COOKIES} invalido ({ex}); intento con la env var.", flush=True)
    raw = os.getenv("DENTIDESK_COOKIES_JSON", "").strip()
    if raw:
        try:
            ctx.add_cookies(json.loads(raw))
            print(">>> Cookies restauradas desde DENTIDESK_COOKIES_JSON (env var)", flush=True)
        except Exception as ex:
            print(f">>> DENTIDESK_COOKIES_JSON invalido ({ex}); se seguira con login normal.", flush=True)


def _save_cookies(ctx):
    try:
        os.makedirs(os.path.dirname(COOKIES), exist_ok=True)
        json.dump(ctx.cookies(), open(COOKIES, "w", encoding="utf-8"))
        print(f">>> Cookies guardadas en {COOKIES}", flush=True)
    except Exception as ex:
        print(f">>> No se pudieron guardar cookies ({ex}).", flush=True)


def _wait_until_dead(page, poll=5):
    """Bloquea mientras el navegador viva; devuelve cuando muere.

    Chrome se muere solo (crash/OOM: evidencia 2026-07-16, 4 procesos `[chrome] <defunct>` con el
    daemon vivo desde el dia anterior). Antes esto era un `while True: sleep(5)` ciego: el proceso
    seguia dormido un dia entero con la ventana muerta (pantalla negra en VNC) y la API contestando
    contra un navegador cadaver, sin UNA linea en el log. El que llama sale con exit 1 -> el
    contenedor reinicia -> vuelve a abrir Chrome restaurando la cookie de /data/cookies.json.
    """
    while not page.is_closed():
        time.sleep(poll)


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
        # Este print separa "python no corre" de "python colgado abriendo Chrome": si sale este y
        # no sale el siguiente, el launch se quedo trabado (perfil bloqueado, Chrome ausente, etc).
        print(f">>> Abriendo Chrome (perfil {PROFILE}, DISPLAY={os.getenv('DISPLAY')})...", flush=True)
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, slow_mo=100, channel="chrome",
            args=[f"--remote-debugging-port={CDP_PORT}", "--start-maximized", "--new-window",
                  # Docker le da 64MB a /dev/shm; Chrome lo agota y se estrella (sospecha de por
                  # que aparecieron 4 `[chrome] <defunct>` con el daemon vivo, 2026-07-16). Con
                  # esto usa /tmp en vez de /dev/shm. Inofensivo si la causa resulta ser otra.
                  "--disable-dev-shm-usage"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _load_cookies(ctx)  # restaurar sesion antes del goto -- puede saltarse el login por completo
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
                _save_cookies(ctx)  # persistir la cookie de sesion recien obtenida al volumen
            except Exception:
                print("No se detecto login (timeout). Daemon sigue corriendo igual; reintenta "
                      "manualmente en la ventana.", flush=True)
        else:
            _save_cookies(ctx)  # ya logueado (cookie restaurada o perfil): re-guardar por si rotó
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
            # Modo produccion: sirve la API HTTP interna (scripts/dentidesk_daemon_api.py) en un
            # PROCESO APARTE (no uvicorn.run() en este mismo hilo/proceso). uvicorn.run() hace su
            # propio asyncio.run(), y sync_playwright ya deja este hilo con un event loop tocado
            # -- eso producia "RuntimeWarning: coroutine 'Server.serve' was never awaited" y el
            # proceso completo moria justo despues del login (contenedor reiniciaba en loop,
            # perdiendo la sesion recien logueada). Un subprocess corre en su propio interprete,
            # sin ese conflicto -- igual a como se probo manualmente con exito (uvicorn suelto).
            import subprocess
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            print(f"Sirviendo API interna en 0.0.0.0:{API_PORT} (DENTIDESK_DAEMON_API=1).",
                  flush=True)
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "scripts.dentidesk_daemon_api:app",
                 "--host", "0.0.0.0", "--port", str(API_PORT)],
                cwd=app_root,
            )
            print("Navegador se queda abierto (API en subproceso aparte).", flush=True)
            try:
                _wait_until_dead(page)
            except KeyboardInterrupt:
                print("Cerrando daemon (Ctrl+C).", flush=True)
                return
            print(">>> NAVEGADOR MUERTO. Salgo con exit 1 para que el contenedor reinicie y lo "
                  "vuelva a abrir con la cookie restaurada.", flush=True)
            sys.exit(1)

        print("Navegador se queda abierto. Usa scripts/dentidesk_attach.py <step> para explorar.",
              flush=True)
        print("Para detenerlo: Ctrl+C aqui, o cierra la ventana a mano.", flush=True)
        try:
            _wait_until_dead(page)
        except KeyboardInterrupt:
            print("Cerrando daemon (Ctrl+C).", flush=True)


if __name__ == "__main__":
    main()
