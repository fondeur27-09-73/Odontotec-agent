# Playground Playwright — practicar y depurar

Scripts locales para practicar scraping con Playwright en cualquier pagina y
depurar errores. **Solo local** — no afecta el deploy Docker del agente.

## 1. Activar el entorno (siempre primero)

```powershell
cd "C:\Users\Ulises Ramirez\.claude\projects\whatsapp Agent"
& ".venv\Scripts\Activate.ps1"
```

El prompt debe mostrar `(.venv)` al inicio.

## 2. Grabar un flujo automaticamente (lo mas facil)

`codegen` abre la pagina + un grabador. Haces clicks a mano y escribe el script Python solo:

```powershell
playwright codegen https://la-pagina.com
```

Copias el codigo generado y lo pegas en `playground.py` (seccion "TU CODIGO AQUI").

## 3. Probar / editar pasos

Edita `scripts/playground.py` (zona "TU CODIGO AQUI") y corre:

```powershell
python scripts/playground.py https://la-pagina.com
```

Abre Chrome visible en camara lenta. Guarda screenshot en `scripts/_out/`.

## 4. Depurar errores

### a) Screenshots automaticos
Cada corrida deja `scripts/_out/ok.png` (exito) o `scripts/_out/error.png` (fallo).

### b) Trace viewer (paso a paso, lo mas potente)
Cada corrida graba `scripts/_out/trace.zip`. Para inspeccionarlo:

```powershell
playwright show-trace scripts/_out/trace.zip
```

Abre una UI con timeline, DOM de cada paso, network, y el error exacto.

### c) Inspector interactivo (pausar y avanzar a mano)
```powershell
$env:PWDEBUG=1 ; python scripts/playground.py https://la-pagina.com
```
Abre el Playwright Inspector — avanzas paso a paso, pruebas selectores en vivo.
Para apagarlo: `$env:PWDEBUG=0`.

## 5. Selectores utiles (cheatsheet)

```python
page.fill("#id", "texto")               # por id
page.fill("input[name='cedula']", "x")  # por atributo
page.click("text=Guardar")              # por texto visible
page.click("button:has-text('Buscar')") # boton que contiene texto
page.wait_for_selector(".resultado")    # esperar a que aparezca
page.inner_text(".ficha")               # leer texto
page.get_by_role("button", name="OK")   # por rol (recomendado)
page.get_by_label("Cedula")             # por etiqueta de campo
```

## Notas

- `headless=False` en `playground.py` = ves el browser. Cambia a `True` para correr sin ventana.
- `slow_mo=300` = 300ms entre acciones para que sigas el flujo. Bajalo/subelo a gusto.
- La carpeta `scripts/_out/` se ignora en git (screenshots y traces no se commitean).
