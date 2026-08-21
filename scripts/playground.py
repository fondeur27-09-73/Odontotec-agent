"""
Playground Playwright — practicar scraping y depurar errores en cualquier pagina.

Uso (con venv activo):
    python scripts/playground.py                 # usa example.com
    python scripts/playground.py https://tu.com  # usa la URL que le pases

Que hace:
- Abre Chrome VISIBLE (headless=False) en camara lenta (slow_mo) para que veas cada paso.
- En exito: guarda screenshot en scripts/_out/ok.png
- En error: imprime el error claro + screenshot scripts/_out/error.png
- Siempre: graba un trace (scripts/_out/trace.zip) para depurar paso a paso.

Para depurar el trace despues de correr:
    playwright show-trace scripts/_out/trace.zip

Para modo paso-a-paso interactivo (Playwright Inspector):
    $env:PWDEBUG=1 ; python scripts/playground.py https://tu.com
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
OUT = Path(__file__).parent / "_out"
OUT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context()
        # graba trace para depurar despues
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            print("OK  URL  :", page.url)
            print("OK  TITLE:", page.title())

            # ===================================================================
            # TU CODIGO AQUI — escribe los pasos a probar.
            # Ejemplos:
            #   page.fill("#nombre", "Juan Perez")
            #   page.fill("input[name='cedula']", "001-1234567-8")
            #   page.click("text=Buscar")
            #   page.wait_for_selector(".resultado")
            #   print(page.inner_text(".resultado"))
            # ===================================================================

            page.screenshot(path=str(OUT / "ok.png"), full_page=True)
            print("OK  shot :", OUT / "ok.png")
        except Exception as e:
            print("ERROR:", type(e).__name__, "-", e)
            try:
                page.screenshot(path=str(OUT / "error.png"), full_page=True)
                print("ERR shot :", OUT / "error.png")
            except Exception:
                pass
        finally:
            trace = OUT / "trace.zip"
            context.tracing.stop(path=str(trace))
            print("TRACE    :", trace)
            print("  ver con: playwright show-trace", trace)
            browser.close()


if __name__ == "__main__":
    main()
