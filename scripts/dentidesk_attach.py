"""
Se CONECTA al navegador ya abierto por dentidesk_daemon.py (puerto CDP 9222). Lee/explora/clickea
sin abrir ni cerrar nada — la sesion del daemon sigue viva despues de que este script termina.

Uso:
    python scripts/dentidesk_attach.py dump            # dump_controls de la pagina actual
    python scripts/dentidesk_attach.py goto <url-relativa>
    python scripts/dentidesk_attach.py click <selector>
    python scripts/dentidesk_attach.py click_text <texto>   # click por texto visible
    python scripts/dentidesk_attach.py screenshot <nombre>
    python scripts/dentidesk_attach.py legend           # abre 'Ver mas' de Clasificacion Estados de Cita
"""
import os
import sys
import json

OUT = os.path.join(os.path.dirname(__file__), "_out")
CDP_PORT = 9222


def dump_controls(page):
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('input,select,textarea,button,a.btn,[type=submit]')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        out.push({
          tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          id: el.id || '', name: el.getAttribute('name') || '',
          placeholder: el.getAttribute('placeholder') || '',
          text: (el.innerText || el.value || '').slice(0,40).trim()
        });
      }
      return out;
    }"""
    return page.evaluate(js)


def main():
    from playwright.sync_api import sync_playwright
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    cmd = args[0]
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        if cmd == "dump":
            print("URL:", page.url, "| TITLE:", page.title())
            print(json.dumps(dump_controls(page), indent=2, ensure_ascii=False))
        elif cmd == "goto":
            url = args[1]
            if not url.startswith("http"):
                url = "https://app.dentidesk.com/" + url.lstrip("/")
            page.goto(url, wait_until="networkidle")
            print("URL:", page.url, "| TITLE:", page.title())
        elif cmd == "click":
            page.click(args[1], timeout=8000)
            page.wait_for_timeout(800)
            print("Click OK en", args[1], "| URL:", page.url)
        elif cmd == "click_text":
            text = args[1]
            page.locator(f"text={text}").first.click(timeout=8000)
            page.wait_for_timeout(800)
            print("Click OK en texto", text, "| URL:", page.url)
        elif cmd == "screenshot":
            name = args[1] if len(args) > 1 else "dd_attach.png"
            page.screenshot(path=os.path.join(OUT, name), full_page=True)
            print("Screenshot guardado:", name)
        elif cmd == "legend":
            try:
                page.locator("text=Ver m").first.click(timeout=5000)
                page.wait_for_timeout(500)
                print("Click en 'Ver mas' OK")
            except Exception as ex:
                print("No pude click 'Ver mas':", str(ex)[:150])
            html = page.evaluate("""() => {
              const el = [...document.querySelectorAll('*')].find(
                e => (e.innerText||'').includes('Clasificaci'));
              return el ? el.closest('div').outerHTML.slice(0, 3000) : 'no encontrado';
            }""")
            print(html)
        elif cmd == "dblclick_xy":
            x, y = int(args[1]), int(args[2])
            page.mouse.dblclick(x, y)
            page.wait_for_timeout(800)
            print(f"Doble click OK en ({x},{y}) | URL:", page.url)
        elif cmd == "key":
            page.keyboard.press(args[1])
            page.wait_for_timeout(500)
            print("Key OK:", args[1], "| URL:", page.url)
        elif cmd == "fill":
            page.fill(args[1], args[2])
            print(f"Fill OK {args[1]} = {args[2]!r}")
        elif cmd == "click_xy":
            x, y = int(args[1]), int(args[2])
            page.mouse.click(x, y)
            page.wait_for_timeout(800)
            print(f"Click OK en ({x},{y}) | URL:", page.url)
        elif cmd == "eval":
            js = args[1]
            print(json.dumps(page.evaluate(js), indent=2, ensure_ascii=False, default=str))
        else:
            print("Comando desconocido:", cmd)
        # NO cerrar browser/ctx/page — el daemon sigue vivo.


if __name__ == "__main__":
    main()
