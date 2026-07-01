# Setup en laptop — continuar pruebas Dentidesk

Checklist paso a paso pa' dejar la laptop lista y seguir donde quedó la PC de escritorio.

## 1. Claude Code en VS Code

1. Instalar VS Code (si no está).
2. Marketplace VS Code → buscar "Claude Code" → instalar extensión.
3. Login con la misma cuenta Anthropic (mismo plan, no requiere config extra).

## 2. Clonar el repo

```powershell
git clone https://github.com/fondeur27-09-73/Odontotec-agent.git
cd Odontotec-agent
git checkout feat/dentidesk-integration
git pull
```

## 3. Copiar `.env`

`.env` NO viaja por git (gitignored, tiene credenciales). Copiarlo a mano desde la PC de escritorio
por canal seguro (USB, gestor de contraseñas) — **nunca por chat ni por git**. Debe traer, entre
otras, las variables `DENTIDESK_USER`, `DENTIDESK_PASS`, `DENTIDESK_WEB_USER`, `DENTIDESK_WEB_PASS`,
`DENTIDESK_BASE`, `DENTIDESK_LOCATION`.

## 4. Python + dependencias

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

El último paso descarga el binario de Chromium (~150-300MB) — obligatorio, no viene con `pip install`
ni con git. Sin esto, `dentidesk_daemon.py` no puede abrir navegador.

## 5. Primer login (daemon)

```powershell
.venv\Scripts\python.exe scripts\dentidesk_daemon.py
```

Corre en segundo plano, abre un Chromium con perfil persistente (`scripts/_out/dd_profile`, se crea
local en la laptop, no sincroniza con la PC de escritorio). Va a pedir resolver el reCAPTCHA + login
manual **una sola vez** — normal, es la primera vez que este perfil ve la sesión.

## 6. Verificar conexión

```powershell
.venv\Scripts\python.exe scripts\dentidesk_attach.py dump
```

Confirma que está logueado antes de seguir con cualquier prueba.

## 7. Activar escritura (solo con aprobación explícita esa sesión)

Agregar a `.env`:

```
DENTIDESK_ALLOW_WRITES=1
```

Sin esto, las funciones de escritura (`create_appointment`, `move_appointment`, `confirm_appointment`)
lanzan error antes de tocar nada — es el candado de seguridad, no lo actives sin decisión consciente.

## Contexto técnico completo

Ver `docs/dentidesk-arquitectura.md` (qué hace API vs Playwright, reglas de negocio) y
`docs/correo-cero-ai-endpoints.md` (borrador de correo al proveedor, pendiente de envío condicionado
al resultado de la prueba real). Memoria de sesión (`~/.claude/projects/.../memory/`) NO sincroniza
entre esta PC y la laptop salvo que se configure aparte — estos docs son la fuente de verdad portátil.
