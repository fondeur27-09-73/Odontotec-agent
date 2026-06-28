"""
Explorador de la UI de Dentidesk — SOLO LECTURA / captura de selectores.

NO guarda nada en el CRM. Navega, hace screenshots y vuelca selectores (inputs/botones) para que
construyamos el script de Playwright paso a paso.

La clave se lee de .env (DENTIDESK_WEB_USER/PASS) y NUNCA se imprime.
Headed (ventana visible) para que el usuario observe.

Uso:
    & ".venv\\Scripts\\Activate.ps1"
    python scripts/dentidesk_explore.py form     # abre login y vuelca campos del formulario
    python scripts/dentidesk_explore.py login    # inicia sesion y screenshot del dashboard
"""
import os
import sys
import json

OUT = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT, exist_ok=True)
LOGIN_URL = "https://app.dentidesk.com/home.php"


def _env():
    e = {}
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); e[k.strip()] = v.strip()
    return e


def dump_controls(page):
    """Lista inputs/selects/botones visibles con sus selectores utiles."""
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('input,select,textarea,button,a.btn,[type=submit]')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        out.push({
          tag: el.tagName.toLowerCase(),
          type: el.getAttribute('type') || '',
          id: el.id || '',
          name: el.getAttribute('name') || '',
          placeholder: el.getAttribute('placeholder') || '',
          text: (el.innerText || el.value || '').slice(0,40).trim()
        });
      }
      return out;
    }"""
    return page.evaluate(js)


PROFILE = os.path.join(OUT, "dd_profile")


def run_persistent(step):
    """Usa un perfil de navegador PERSISTENTE: el humano resuelve el captcha + login UNA vez;
    el perfil guarda la sesión y la reutiliza. Sin re-captcha en corridas siguientes."""
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True)
    e = _env()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, slow_mo=200,
            args=["--start-maximized", "--new-window"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.bring_to_front()
        print(">>> BUSCA una ventana de Chromium NUEVA (no esta terminal). Puede estar detras "
              "de otras ventanas — alt-tab si no la ves.")
        page.wait_for_timeout(1500)
        if step == "pcheck":
            logged = "Inicio de Sesi" not in page.title()
            page.screenshot(path=os.path.join(OUT, "dd_check.png"))
            print(f"SESION VIVA? {'SI' if logged else 'NO (pide login/captcha)'} | "
                  f"URL: {page.url} | TITLE: {page.title()}")
            ctx.close(); return
        if "Inicio de Sesi" in page.title():
            # No logueado: llenar creds y esperar a que el humano marque captcha + Iniciar sesión
            try:
                page.fill("#user-login", e.get("DENTIDESK_WEB_USER", ""))
                page.fill("#pass-login", e.get("DENTIDESK_WEB_PASS", ""))
            except Exception:
                pass
            print(">>> En la ventana: marca 'No soy un robot' y pulsa 'Iniciar sesión'. (180s)")
            try:
                page.wait_for_function("() => !document.title.includes('Inicio de Sesi')",
                                       timeout=180000)
            except Exception:
                page.screenshot(path=os.path.join(OUT, "dd_session_fail.png"))
                print("No se detectó login (timeout).")
                ctx.close(); return
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        if step == "pcookies":
            import datetime
            print("LOGUEADO. Cookies (nombre | expira):")
            for c in ctx.cookies():
                exp = c.get("expires", -1)
                if exp and exp > 0:
                    when = datetime.datetime.fromtimestamp(exp).isoformat()
                else:
                    when = "SESION (muere al cerrar navegador)"
                print(f"  {c.get('name')} | {when}")
            ctx.close(); return
        if step == "pmap":
            print(">>> PASO 1: en la ventana, cambia a vista 'Dia' y haz click en un hueco vacio "
                  "de un doctor para abrir 'nueva cita'. Tienes 90s.")
            page.wait_for_timeout(90000)
            page.screenshot(path=os.path.join(OUT, "dd_map_nuevacita.png"), full_page=True)
            print("--- estado tras tu click (nueva cita esperado) ---")
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            print("\n>>> PASO 2: cierra ese formulario SIN guardar, y abre una cita YA EXISTENTE "
                  "(click en una de las tarjetas con nombre de paciente) para ver el formulario de "
                  "editar/reagendar. Tienes 60s.")
            page.wait_for_timeout(60000)
            page.screenshot(path=os.path.join(OUT, "dd_map_editcita.png"), full_page=True)
            print("--- estado tras abrir cita existente (editar/reagendar esperado) ---")
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            print("\n>>> Cierra cualquier formulario (sin guardar/sin tocar status). Ventana se "
                  "cierra en 20s.")
            page.wait_for_timeout(20000)
            ctx.close(); return
        if step == "pagenda2":
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(OUT, "dd_agenda2_before.png"), full_page=True)
            target_x, target_y = 1150, 350  # columna Dom 28/6, fila 11:00 (vacia, leido del screenshot)
            print(f"Click directo en ({target_x},{target_y})")
            page.mouse.click(target_x, target_y)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(OUT, "dd_agenda2_click.png"), full_page=True)
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False)[:4000])
            print(">>> Ventana abierta 60s para revisar / ajustar manualmente.")
            page.wait_for_timeout(60000)
            page.screenshot(path=os.path.join(OUT, "dd_agenda2_final.png"), full_page=True)
            print("--- estado final ---")
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False)[:6000])
            ctx.close(); return
        if step == "pagenda":
            page.wait_for_timeout(1500)
            grid = page.evaluate("""() => {
              const tables = Array.from(document.querySelectorAll('table'));
              let best = null, bestCount = -1;
              for (const t of tables) {
                const n = t.querySelectorAll('tbody tr').length;
                if (n > bestCount) { bestCount = n; best = t; }
              }
              if (!best) return {error: 'no table found', tableCount: tables.length};
              const headerCells = Array.from(best.querySelectorAll('thead th, thead td'))
                .map(th => th.innerText.trim());
              const rows = Array.from(best.querySelectorAll('tbody tr'));
              const out = [];
              rows.forEach((tr, ri) => {
                const cells = Array.from(tr.children);
                const timeLabel = cells[0] ? cells[0].innerText.trim() : '';
                cells.slice(1).forEach((td, ci) => {
                  const r = td.getBoundingClientRect();
                  if (r.width === 0 || r.height === 0) return;
                  const text = (td.innerText || '').trim();
                  const hasOnclick = !!td.getAttribute('onclick');
                  out.push({
                    ri, ci, timeLabel, header: headerCells[ci + 1] || '',
                    empty: text.length === 0, hasOnclick,
                    x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)
                  });
                });
              });
              return {headerCells, rowCount: rows.length, cells: out};
            }""")
            print("--- estructura de la grilla de agenda ---")
            print(json.dumps(grid, indent=2, ensure_ascii=False)[:4000])
            page.screenshot(path=os.path.join(OUT, "dd_agenda_grid.png"), full_page=True)
            empty_cells = [c for c in grid.get("cells", []) if c.get("empty")]
            print(f"\nceldas vacias encontradas: {len(empty_cells)}")
            if empty_cells:
                target = empty_cells[len(empty_cells) // 2]  # una del medio, lejos de bordes
                print("Click en celda vacia:", json.dumps(target, ensure_ascii=False))
                page.mouse.click(target["x"], target["y"])
                page.wait_for_timeout(1500)
                page.screenshot(path=os.path.join(OUT, "dd_agenda_click.png"), full_page=True)
                print("--- tras click en celda vacia ---")
                print("URL:", page.url, "| TITLE:", page.title())
                print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            else:
                print("No se encontraron celdas vacias automaticamente.")
            print(">>> Ventana abierta 60s para revisar / abrir manualmente si hizo falta.")
            page.wait_for_timeout(60000)
            page.screenshot(path=os.path.join(OUT, "dd_agenda_final.png"), full_page=True)
            print("--- estado final ---")
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            ctx.close(); return
        if step == "pfillcliente":
            page.goto("https://app.dentidesk.com/pacientes.php", wait_until="networkidle")
            page.click("#btn_paciente")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            page.fill("#nombre", "PRUEBA")
            page.fill("#apellido", "NOGUARDAR")
            page.fill("#rut", "000-0000000-0")
            page.fill("#email", "prueba.noguardar@example.com")
            page.select_option("#diadbo", "15")
            page.select_option("#mesdbo", label="Enero")
            try:
                page.select_option("#aniodbo", "1990")
            except Exception as ex:
                print("No pude seleccionar anio 1990:", str(ex)[:100])
            page.check("#genero_1")
            page.fill("#fonofijo", "8095550000")
            page.fill("#fonocel", "8295550000")
            page.fill("#direccion", "Calle Falsa 123")
            page.fill("#ocupacion", "Prueba selectores")
            page.fill("#notas", "PRUEBA - NO GUARDAR - solo verificacion visual de selectores")
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(OUT, "dd_ficha_fake_fill.png"), full_page=True)
            print("Formulario lleno con datos ficticios. URL:", page.url)
            print(">>> NO se pulsa Guardar. Ventana abierta 90s para que la revises visualmente.")
            page.wait_for_timeout(90000)
            page.screenshot(path=os.path.join(OUT, "dd_ficha_fake_fill_final.png"), full_page=True)
            ctx.close(); return
        if step == "ppacientes":
            page.goto("https://app.dentidesk.com/pacientes.php", wait_until="networkidle")
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(OUT, "dd_pacientes.png"), full_page=True)
            print("PACIENTES.PHP | URL:", page.url, "| TITLE:", page.title())
            print("--- controles en pacientes.php ---")
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            # Buscar el botón/enlace "crear cliente" y abrirlo para capturar el formulario.
            crear_locator = page.locator(
                "a:has-text('Crear'), button:has-text('Crear'), "
                "a:has-text('Nuevo'), button:has-text('Nuevo'), "
                "a:has-text('Agregar'), button:has-text('Agregar')"
            ).first
            try:
                crear_locator.click(timeout=5000)
                page.wait_for_timeout(1500)
                page.screenshot(path=os.path.join(OUT, "dd_crear_cliente.png"), full_page=True)
                print("--- tras click 'crear cliente' ---")
                print("URL:", page.url, "| TITLE:", page.title())
                print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            except Exception as ex:
                print("No encontre boton 'crear cliente' automaticamente:", str(ex)[:120])
            print(">>> Si el formulario no se abrio solo, abrelo a mano ahora (60s).")
            page.wait_for_timeout(60000)
            page.screenshot(path=os.path.join(OUT, "dd_crear_cliente_manual.png"), full_page=True)
            print("--- estado final (tras ventana manual) ---")
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            ctx.close(); return
        page.screenshot(path=os.path.join(OUT, "dd_dashboard.png"), full_page=True)
        print("LOGUEADO. URL:", page.url, "| TITLE:", page.title())
        nav = page.evaluate("""() => {
          const out=[];
          for (const a of document.querySelectorAll('a,button,[onclick]')) {
            const t=(a.innerText||a.title||'').trim();
            if (!t) continue;
            out.push({text:t.slice(0,45), href:a.getAttribute('href')||'', id:a.id||''});
          }
          return out.slice(0,120);
        }""")
        print(json.dumps(nav, indent=2, ensure_ascii=False))
        page.wait_for_timeout(1000)
        ctx.close()


def main(step):
    if step.startswith("p"):
        return run_persistent(step)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="networkidle")
        if step == "form":
            page.screenshot(path=os.path.join(OUT, "dd_login.png"))
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
            page.wait_for_timeout(1500)
        elif step == "login":
            e = _env()
            page.fill("#user-login", e.get("DENTIDESK_WEB_USER", ""))
            page.fill("#pass-login", e.get("DENTIDESK_WEB_PASS", ""))
            page.click("#btn_login")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path=os.path.join(OUT, "dd_dashboard.png"), full_page=True)
            print("URL tras login:", page.url, "| TITLE:", page.title())
            # Menú / enlaces de navegación (texto + href) para ubicar crear cliente / agenda
            links = page.evaluate("""() => {
              const out=[];
              for (const a of document.querySelectorAll('a,[onclick],button')) {
                const t=(a.innerText||a.title||'').trim();
                if (!t) continue;
                out.push({text:t.slice(0,40), href:a.getAttribute('href')||'', id:a.id||''});
              }
              return out.slice(0,80);
            }""")
            print(json.dumps(links, indent=2, ensure_ascii=False))
            page.wait_for_timeout(1500)
        elif step == "session":
            # Login asistido: la herramienta llena usuario/clave; el HUMANO marca el reCAPTCHA y
            # pulsa "Iniciar sesión" en la ventana. Esperamos a salir del login y guardamos la sesión.
            e = _env()
            page.fill("#user-login", e.get("DENTIDESK_WEB_USER", ""))
            page.fill("#pass-login", e.get("DENTIDESK_WEB_PASS", ""))
            # Intentar marcar el reCAPTCHA (iframe). Si lanza reto de imagenes, lo resuelve el humano.
            try:
                page.frame_locator("iframe[title='reCAPTCHA']").locator(
                    ".recaptcha-checkbox-border, #recaptcha-anchor").click(timeout=8000)
                page.wait_for_timeout(2000)
            except Exception as ce:
                print("No pude marcar el captcha automaticamente:", str(ce)[:80])
            print(">>> Si aparece reto de imagenes, resuelvelo. Luego pulsa 'Iniciar sesion'. 150s.")
            try:
                page.wait_for_function(
                    "() => !document.title.includes('Inicio de Sesi')", timeout=150000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                ctx.storage_state(path=os.path.join(OUT, "dd_state.json"))
                page.screenshot(path=os.path.join(OUT, "dd_dashboard.png"), full_page=True)
                print("LOGIN OK. URL:", page.url, "| TITLE:", page.title())
                print("Sesion guardada en scripts/_out/dd_state.json")
            except Exception as ex:
                page.screenshot(path=os.path.join(OUT, "dd_session_fail.png"))
                print("No se detecto login (timeout o fallo):", str(ex)[:120])
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "form")
