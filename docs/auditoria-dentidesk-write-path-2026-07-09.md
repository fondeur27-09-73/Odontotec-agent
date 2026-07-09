# Auditoría del write-path de Dentidesk — 2026-07-09

> Mandato del cliente: *"ya no podemos seguir parcheando, sino reescribir todo si es necesario."*
> Resultado: **no** hizo falta reescribir todo. El diseño es correcto; el problema era **operativo**
> (el daemon no estaba logueado) y el código **enmascaraba** ese estado en vez de reportarlo claro.
> Esta auditoría endurece la detección de sesión y elimina los parches que no podían funcionar.

---

## 1. Causa raíz (por qué el bug volvía 3 veces)

`LOGIN_URL` y `AGENDA_URL` son **la misma URL** (`https://app.dentidesk.com/home.php`):

- **Logueado** → esa URL renderiza la agenda, **con** `moment.js` y `window.open_modal_cita`.
- **Deslogueado** → la misma URL renderiza el **formulario de login** (`#user-login`), **sin**
  `moment` ni `open_modal_cita`.

Entonces `create_appointment` llamaba `moment(...)` sobre lo que creía que era la agenda pero era
el login → **`ReferenceError: moment is not defined`** → 502 en bucle. El mensaje de error no decía
nada de "sesión caída", así que parecía un bug nuevo cada vez.

**Por qué el navegador estaba deslogueado:** el login de Dentidesk exige resolver un **reCAPTCHA de
imágenes** ("selecciona todos los buses"), que **no se puede automatizar**. Cada redeploy del daemon
reinicia Chrome ("Restore pages? Chrome didn't shut down correctly") y la sesión se cae → hay que
volver a resolver el captcha **a mano por VNC**.

### Los dos parches previos que NO podían funcionar
1. `_ensure_logged_in()` → llamaba `_login()` (fill usuario/clave + click), pero **eso no pasa el
   reCAPTCHA**. Nunca lograba re-loguear.
2. `wait_for_function(moment, 15s)` (commit 1cdb99f) → solo cambiaba el `ReferenceError` por un
   **timeout de 15s** igual de opaco. Trataba el síntoma, no la causa.

---

## 2. Cambios de esta auditoría (endurecimiento, no reescritura)

Archivos: `integrations/dentidesk_playwright.py`, `scripts/dentidesk_daemon_api.py`,
`tests/test_dentidesk_tools.py`.

| # | Cambio | Efecto |
|---|--------|--------|
| 1 | **`class SesionNoLogueada`** (excepción tipada) | Distingue "no logueado" de un fallo real del formulario. |
| 2 | **`_session_live(page)`** / **`_require_session_live(page)`** | Chequea `moment + open_modal_cita` ANTES de escribir; si no hay sesión, aborta con mensaje que dice *qué hacer* (loguear por VNC). |
| 3 | **Eliminado `_ensure_logged_in` + auto-login** | Quita el parche que no podía pasar el reCAPTCHA. |
| 4 | **Eliminado el `wait_for_function(moment)` de 15s** | Reemplazado por el chequeo de sesión claro (margen corto de 8s por si la agenda aún pinta). |
| 5 | **Endpoint `GET /session`** en la API del daemon | Permite **monitorear** si el daemon está logueado SIN intentar una cita. |
| 6 | **API daemon: `SesionNoLogueada` → HTTP 409** (no 502) | El agente distingue "no logueado" de un fallo genuino. |
| 7 | **`_call_daemon`: 409 → dict `daemon_no_logueado`** con `message` amable | Carla le dice al paciente *"la agenda enseguida"* en vez de reventar o afirmar que registró. |

**Tests:** +5 nuevos (`_session_live`, `_require_session_live` ok/lanza, `_call_daemon` 409).
Suite completa: **79/79 verde**.

---

## 3. Lo que este cambio NO arregla (sigue siendo operativo)

1. **El daemon se desloguea en cada redeploy.** Solución operativa: **no redeployes el daemon** salvo
   que sea imprescindible; después de loguear, déjalo quieto.
2. **Persistencia del perfil (`/data/dd_profile`).** Si el volumen estuviera bien montado *y*
   Dentidesk conservara la cookie, un redeploy **no** debería pedir login de nuevo. Como sí lo pide,
   hay que confirmar en EasyPanel → Storage que el volumen está **montado en `/data`** (pendiente,
   requiere el usuario).
3. **El login sigue necesitando un humano** (reCAPTCHA). Es inherente a Dentidesk; no hay forma
   soportada de evitarlo. El modelo correcto es: loguear **una vez** por VNC y dejar la ventana
   abierta (ya implementado en `scripts/dentidesk_daemon.py`).

---

## 4. Cómo se comporta ahora (antes → después)

| Situación | Antes | Después |
|-----------|-------|---------|
| Daemon logueado | Cita OK | Cita OK (sin cambios) |
| Daemon deslogueado | `moment is not defined` → 502 en bucle, Carla decía "estoy registrando" y nada | **409 `daemon_no_logueado`**, Carla avisa "la agenda enseguida", log del daemon dice *"sesión no logueada"* |
| Monitoreo | No había forma sin intentar una cita | `GET /session` → `{"logueado": true/false}` |

---

## 5. Pendientes (requieren el usuario / no automatizables aquí)

- [ ] **Confirmar volumen `/data/dd_profile` montado** en EasyPanel (persistencia del login).
- [ ] **Quitar el dominio VNC temporal** (`testagente1-daemonvnc...`) — expone control total del
      navegador; solo debía existir para el login manual.
- [ ] **`WEBHOOK_SECRET`** sigue faltando en el servicio `odontotec`.
- [ ] **Rotar secretos expuestos**: `DENTIDESK_WEB_PASS=Prueba.1` (débil) y la clave VNC que quedó a
      la vista en el chat.
- [ ] **Desplegar** el daemon con estos cambios (recordar: el redeploy pedirá re-login por VNC).

---

## 6. Veredicto

El write-path **no estaba mal diseñado** — la arquitectura de 2 contenedores + navegador persistente
+ API con secreto es sólida. Lo que fallaba era que el código **fingía recuperarse** de una sesión
caída (con un login que no podía pasar el captcha) en vez de **decir la verdad**: "no estoy
logueado, que alguien entre por VNC". Ahora lo dice, en un solo lugar, con un error claro y
monitoreable. **No se justifica una reescritura completa.**
