"""
Lanza Playwright codegen sobre la UI web de Dentidesk para CAPTURAR selectores.

Uso (en el .venv que tiene Playwright):
    & ".venv\\Scripts\\Activate.ps1"
    python scripts/dentidesk_codegen.py

Qué hace:
  Abre un navegador + el inspector de codegen en https://app.dentidesk.com/home.php. Tú inicias
  sesión a mano y recorres los flujos. Codegen va imprimiendo el código (page.fill/page.click) de
  cada acción. Copias esos selectores a integrations/dentidesk_playwright.py (bloques TODO(codegen)).

REGLA: mientras se desarrolla, NO pulses el botón final de "guardar" en crear/mover cita — solo
recorre hasta el formulario para capturar los selectores. La escritura real es el día de simulación
con DENTIDESK_ALLOW_WRITES=1 y tu aprobación.

Flujos a capturar:
  1. Login (usuario/clave/botón entrar).
  2. Nueva cita: abrir formulario, campos cédula/nombre/teléfono/especialidad/fecha/hora/procedimiento.
  3. Reagendar: buscar cita, abrir editar, campos de fecha/hora.
"""
import subprocess
import sys

URL = "https://app.dentidesk.com/home.php"

if __name__ == "__main__":
    print(__doc__)
    print(f"Lanzando codegen sobre {URL} ...\n")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "codegen", URL], check=False)
    except FileNotFoundError:
        print("Playwright no encontrado. Activa el .venv:  & \".venv\\Scripts\\Activate.ps1\"")
        sys.exit(1)
