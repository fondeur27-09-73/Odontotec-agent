# CLAUDE.md — Carla Odontotec WhatsApp Agent

## 🟢 ARRANCAR AQUÍ — Sesión 2026-08-19: FASE 2 EN VUELO (inbox 849 creado, falta Meta + e2e)

**En una línea:** el cliente entregó el número oficial, el inbox de Chatwoot ya está creado, y
**la migración NO requiere tocar código** — falta pegar el webhook en Meta y probar.

### Datos del número nuevo (Fase 2)

| Dato | Valor |
|---|---|
| Número | `+18494107913` |
| Phone Number ID | `1242049598998643` |
| WABA ID | `935606856224223` |
| Token permanente | System User "Karla BOT" (no está en el repo) |
| Inbox Chatwoot | `Cliente Dental - 849` (cuenta 3) |
| Webhook URL | `https://testagente1-chatwoot.oyfspa.easypanel.host/webhooks/whatsapp/+18494107913` |
| Verify Token | NO se guarda acá (credencial). Está en Chatwoot → Settings → Inboxes → `Cliente Dental - 849`. ⚠️ quedó escrito en un chat — regenerar tras el e2e |

### ⚠️ La migración NO necesita cambios de código — verificado leyendo main.py

- `webhook` saca `conv_id` de `payload.data.conversation.id` (main.py:289) y contesta con
  `send_message(conv_id, ...)`. **Ya es dinámico**, no hay inbox fijo en el camino de respuesta.
- **El webhook NUNCA mira `inbox_id`.** Los webhooks de Chatwoot son a nivel de CUENTA → el inbox
  nuevo dispara solo, sin configurar nada en `odontotec`. Consecuencia: **con los dos inboxes vivos,
  Carla contesta en AMBOS números.** Es lo esperado durante la transición.
- **`CHATWOOT_INBOX_ID` NO es requisito de Fase 2.** Solo la lee `_inbox_id()` → `send_template()`,
  llamado por `scheduler/reminders.py:103` (Fase 3, PAUSADA) y `alerts.py:68` (que usa su propia
  `WATCHDOG_ALERT_INBOX_ID`). Cambiarla es preparación de Fase 3.

### PENDIENTE inmediato (por orden)

1. **Meta** → App → WhatsApp → Configuration → Webhooks → Edit: pegar Callback URL + Verify Token →
   **Verify and save**. Después, en **Webhook fields**, suscribir **`messages`** (esto se saltea seguido:
   sin `messages` todo se ve verde y no llega ni un mensaje).
2. **E2E:** WhatsApp al 849 → Carla contesta sola. Luego agendar/reagendar real.
3. **Solo tras el 100%:** apagar el inbox viejo. Nunca antes.
4. **Avisarle al cliente:** el 849 ya NO abre en la app de WhatsApp del celular (un número vive en la
   app O en la Cloud API, no en ambas). La ventana para ver las conversaciones es **Chatwoot**. Decirlo
   ANTES de que lo descubran solos y crean que se rompió algo. El QR que muestra Chatwoot es
   `wa.me/18494107913` — para que el PACIENTE lo escanee; no hay emparejamiento por QR en Cloud API.

## 🔍 (2026-08-18) El flapping "daemon caído" de agosto era FALSA ALARMA — causa raíz encontrada

**NO era OOM ni el VPS pobre.** Las firmas de error distinguen la causa — leer el CUERPO del correo,
no solo el asunto:

| Firma | Significa |
|---|---|
| `ReadTimeout: timed out` (los ~12 pares del 8–9 ago) | contenedor ARRIBA, API respondiendo, solo tardó >15s |
| `502 Bad Gateway` (el del 9 ago 15:09) | API viva; `/session` da 502 cuando `session_status()` lanza → **Chrome muerto, API viva** |
| `ConnectError: Name or service not known` | ESE sí es el contenedor apagado (firma de la prueba de julio) |

**Causa raíz (verificada en código, SIN ARREGLAR):** `session_status()`
([dentidesk_playwright.py:667](integrations/dentidesk_playwright.py#L667)) hace
`page.goto(AGENDA_URL, wait_until="networkidle")`. La agenda hace polling de fondo — el propio daemon
lo documenta en `dentidesk_daemon.py:208` ("rara vez llega a networkidle"). Sin ventana de 500ms en
silencio, el goto corre hasta el default de Playwright (30s) mientras el watchdog corta a los 15s
([watchdog.py:46](scheduler/watchdog.py#L46)). **15s < 30s → ReadTimeout intermitente** = flapping cada
5 min sin causa externa. El heartbeat ya usa `domcontentloaded`; `session_status` quedó con
`networkidle` por inconsistencia, no por decisión.

**Fix pendiente (2 líneas, NO aplicado):**
1. `session_status()`: `wait_until="domcontentloaded"`.
2. **`/session` debe tomar `_write_lock`** — hoy NO lo toma ([daemon_api.py:89](scripts/dentidesk_daemon_api.py#L89))
   y navega **`pages[0]`** vía `_open_write_page`, la MISMA página que usa `crear_cita`. El `_write_lock`
   solo serializa escrituras entre sí. → El watchdog, cada 5 min, **puede navegar la página fuera de
   debajo de una cita a medio crear**. Carrera real, nunca vista disparar. El heartbeat sí evita
   `pages[0]` a propósito.

**Hueco de la auto-cura:** `_wait_until_dead` solo mira `page.is_closed()` (`dentidesk_daemon.py:87`)
→ detecta Chrome MUERTO, no Chrome COLGADO. Un Chrome congelado se ve vivo para el daemon, nunca
reinicia, y la UI en VNC no responde a clics. Se resolvió con **Restart manual** del contenedor.

**⚠️ El silencio del watchdog NO es señal de salud.** Estuvo 9 días sin mandar nada y eso admitía tres
lecturas incompatibles (se curó y odontotec reinició tragándose el aviso / sigue roto y el edge-trigger
se calló / odontotec mismo está muerto). Hicieron falta 3 chequeos externos para desambiguar. **Antes
de operar, comprobar el estado ACTIVAMENTE, nunca inferirlo de la ausencia de alertas.**

**Cierre 2026-08-19 00:39Z:** correo `✅ daemon recuperado` = `/session` devolvió `logueado: true`.
Daemon sano, cookie fresca. `odontotec` `/health` → 200.

**NO resucitar:** se sospechó que el reCAPTCHA no marcaba por `--enable-automation` /
`navigator.webdriver` de `launch_persistent_context`. **El fix NO se aplicó** — tras el restart el
usuario logueó bien. No tocar sin evidencia nueva.

---

## 🟢 (previo) ARRANCAR AQUÍ — Sesión 2026-07-23 CERRADA: 6 fixes desplegados, todo verificado

Branch `feat/dentidesk-integration`, HEAD `e166d38`. **Los dos bugs post-suspensión + 3 pendientes
resueltos y desplegados esta sesión.** Detalle en memorias [[singleton_lock_daemon_crashloop_2026-07-23]],
[[pendiente_alerta_conversacion_estancada_2026-07-22]], [[pendiente_alerta_escalada_2026-07-16]].

| Commit | Qué | Contenedor | Estado |
|--------|-----|-----------|--------|
| `d498e8d` | heartbeat: re-guarda cookie cada 5 min (`_keep_session_warm`) | daemon | ✅ desplegado+verificado |
| `7c9d03b` | **SingletonLock**: borra el lock rancio antes de abrir Chrome | daemon | ✅ desplegado+verificado |
| `2354dae` | guard de antigüedad: ignora mensajes viejos (no más fantasma) | odontotec | ✅ desplegado |
| `a031ddd` | watchdog conversación estancada +5 min | odontotec | ✅ desplegado+**verificado en vivo** |
| `fd4c0e4` | cédula 11 dígitos obligatorios en el agente | odontotec | ✅ desplegado |
| `e166d38` | alerta de escalada (bot-off) por email fire-and-forget | odontotec | ✅ desplegado |

**PROBLEMA 1 (Chrome muere → daemon caído todo el día) — RESUELTO.** La causa del "caído todo el día"
NO era solo la cookie: era un **`SingletonLock` rancio** en `/data/dd_profile` que, tras un reinicio,
apuntaba al hostname del contenedor MUERTO → Chrome rehusaba arrancar (`exitCode=21`,
`process_singleton_posix`) → crash-loop, ni por VNC. `_clear_singleton_locks` lo borra antes del
launch. La teoría "SingletonLock" que el handoff daba por FALSA era falsa SOLO mientras el daemon no
reiniciaba; el heartbeat lo hace reiniciar → se volvió real. **Cadena de auto-cura COMPLETA verificada
en vivo:** Chrome muere → reinicia → borra lock → restaura cookie fresca → logueado, SIN humano. ⚠️ NO
perseguir un "Chrome muere cada 6 min": eran teardowns de EasyPanel al desplegar, NO OOM (el watchdog
no mandó correo caído/recuperado → no se cayó). El flapping OOM histórico del 22-jul sí fue real pero
NO urgente (se auto-cura); sacar con `dmesg -T | grep "killed process"` + `free -h` solo si molesta.

**PROBLEMA 2 (saludos fantasma) — RESUELTO.** `main.py` corría el agente por cada `message_created`
sin mirar antigüedad → cuando odontotec estuvo caído, Sidekiq reentregaba el webhook HORAS después
(backoff exponencial = las horas irregulares 5:17/7:34/1:54) y Carla contestaba un mensaje viejo.
Fix = **guard de antigüedad** (`_message_age_secs`: ignora mensajes con `created_at` de hace
>`WEBHOOK_MAX_MSG_AGE_SECS`=600s), NO dedup por id (no sobrevive reinicio). Fail-open: si falta
`created_at` procesa igual y loguea las keys. (La evidencia directa del 22-jul se perdió: logs rotaron.)

**Los 3 pendientes que se cerraron de regalo:** (a) cédula 11 dígitos ya vivía en `fd4c0e4`; (b)
watchdog de conversación estancada +5 min (`_check_stalled_conversations`, edge-trigger, verificado en
vivo: detectó convs 7/3 + correos llegaron — eran procesos viejos colgados, no pacientes reales); (c)
alerta de escalada (`_escalate_to_human` avisa por email en hilo daemon fire-and-forget).

**Suite: 124 pasan** (6+2 fallos preexistentes de respx/py3.14, no míos).

**PRÓXIMO (nada urgente):** ⭐ Fase 2 (número oficial Meta + plantillas — bloqueada por el cliente,
desbloquea WhatsApp para TODAS las alertas que hoy salen por email). Seguridad: `WEBHOOK_SECRET=MISSING`
en odontotec (webhook acepta cualquier origen), rotar `DENTIDESK_WEB_PASS`, quitar dominio VNC. E2E de
cédula por WhatsApp tras limpieza del cliente.

---

## 🟢 (previo) Sesión 2026-07-22 (madrugada): duplicados/RUT RESUELTO EN CÓDIGO

**En una línea:** el experimento VNC del usuario corrigió la causa raíz. El fix son **11 dígitos de
cédula obligatorios EN EL AGENTE** (commit `fd4c0e4`, pusheado). **Falta:** deploy de `odontotec` +
e2e tras la limpieza del cliente.

**Lo que el experimento VNC mató (2026-07-22):**
- El **"Debe especificar un rut/DNI válido" es cédula VACÍA**, NO rechazo de la cédula dominicana por
  RUT chileno. El usuario creó a mano un paciente con cédula `123` (3 dígitos) y **guardó**: Dentidesk
  NO valida longitud ni check-digit. → El "2º problema" (cédula RD choca con RUT chileno) **NO EXISTE**.
- La **truncación a `0310`** la causaba un registro viejo de cédula corta que el autocompletar de
  `#rut` ofrecía como prefijo y el Enter seleccionaba. **Sin cédulas cortas en la BD, no hay prefijo
  que envenene → no hay truncación.** El guardrail de 11 dígitos lo previene en el origen.
- El paciente **se enlaza por cédula** (teclear cédula + Enter → Dentidesk busca por ella, sin elegir
  del dropdown), no solo por nombre. El `_type_rut_recognize` actual (press_sequentially+Enter) **ya
  es el mecanismo correcto** — no se tocó.

**El fix (commit `fd4c0e4`, 4 archivos, +62/-6):** guardrail donde debe vivir — el agente, no
Dentidesk que acepta cualquier cosa.
- `agent/tool_handlers.py::_cedula_dominicana_ok()` — exige EXACTAMENTE 11 dígitos; early-return
  `cedula_invalida` en `_agendar_cita_dentidesk` antes de tocar Playwright.
- `agent/prompts.py` (Paso 2) — Carla pide los 11 dígitos, insiste si faltan, no inventa, explica que
  la cédula identifica al paciente.
- `integrations/dentidesk_playwright.py` — corregidos 2 comentarios FALSOS ("duplicado que el CRM
  rechaza" y "choca con RUT chileno").
- Tests: `test_cedula_dominicana_ok` (9 casos) + `test_agendar_cedula_corta_no_llama_playwright` +
  cédula válida en `_BASE_ARGS`. **Suite 113 pasan** (solo los 6+2 preexistentes respx/py3.14 fallan).
- **NO se construyó** el guard de truncación en Playwright (YAGNI: el de 11 dígitos lo cubre).

**PENDIENTE inmediato:**
1. **Deploy de `odontotec`** en EasyPanel (el guard vive en el AGENTE, no en el daemon — no tocar
   `dentidesk-daemon`). Prod construye de `feat/dentidesk-integration` = `fd4c0e4`.
2. **E2E por WhatsApp** tras la limpieza del cliente. Prueba clave: cédula corta (`123`) → Carla debe
   pedir los 11 completos y NO agendar; cédula de 11 dígitos → agenda sin duplicar.

**Decisión previa (sigue vigente):** agendado pausado + el **cliente borra** las citas de prueba (la
cuenta `carlaodontotec` no tiene permiso de borrar) y se arranca de cero.

**Entregable de esta sesión:** `docs/dentidesk-debilidades-para-odonta.md` — debilidades de Dentidesk
para replicar/corregir en **Odonta** (el CRM propio del usuario, Fable 5, aún sin probar). Dentidesk
NO es del usuario.

Memoria: [[bug_rut_cedula_dominicana_regresion_2026-07-21]], [[odonta_debilidades_dentidesk_2026-07-22]].

---

## ⏳ ESPERANDO AL JEFE DE ODONTOTEC (bloqueos externos, no de código) — 2026-07-22

Dos decisiones que dependen del cliente, no de nosotros:

1. **Número oficial de WhatsApp para Carla — pendiente de aprobación del Jefe de Odontotec.** Hasta
   que apruebe y entregue el número Meta oficial de la clínica, Carla sigue en el número de prueba.
   Esto desbloquea la **Fase 2** (migrar `whatsapp_phone_number`) y de rebote la Fase 3 (recordatorios)
   y la alerta de escalada por WhatsApp. Ver [[pendiente_numero_clinica_plantillas_2026-07-16]].

2. **Dónde se aloja el proyecto: VPS vs. PC dentro de la clínica — lo decide el Jefe de Odontotec.**
   Sigue abierto. Nuestra recomendación basada en evidencia (ver sección "El usuario propuso BOTAR el
   VPS" abajo): NO botar el VPS a ciegas — los 2 problemas que lo quemaron (Chrome muere + cookie) ya
   se arreglaron el 2026-07-16 y aún no se ha visto correr CON el fix. Una PC en la clínica trae
   cortes de luz, cierres accidentales de Chrome, y sin acceso remoto para arreglar. Decidir después
   del veredicto de estabilidad, no antes.

---

**Estado actual:** 2026-07-22 (madrugada) | Branch: `feat/dentidesk-integration` (prod construye de ESTA branch — verificado en EasyPanel Source) | HEAD = `fd4c0e4` (fix cédula 11 dígitos, pusheado) | ⚠️ `odontotec` en prod aún corre código viejo — FALTA Deploy | Repo indexado (552 nodos, 1900 edges)

**Lo de hoy en una línea:** el watchdog está **desplegado y probado contra prod** (detecta, manda el
correo, no spamea) y **la cookie del daemon SÍ sobrevive el reinicio** — las dos cosas verificadas en
vivo, no en tests. Queda abierto: cuánto dura la cookie antes de caducar, y el veredicto de Chrome a 48h.

## 🔴 ABIERTO (2026-07-16): CHROME se muere solo — la cookie NO era el problema

### 1. Daemon "deslogueado" = Chrome muerto + daemon durmiendo sin enterarse
- **Evidencia dura** (`ps aux` DENTRO del contenedor, EasyPanel → dentidesk-daemon → **Terminal**):
  ```
  appuser  1   Ssl  Jul15  python scripts/dentidesk_daemon.py   <- vivo 1 dia, NO reinicia
  appuser 153  Sl   Jul15  python -m uvicorn ...daemon_api:app  <- API viva
  appuser  35  Z    Jul15  [chrome] <defunct>                   <- Chrome MUERTO (x4 zombies)
  ```
- **Causa:** Chrome arranca bien y se muere después. El daemon quedaba en `while True: sleep(5)` ciego → un día entero durmiendo con navegador cadáver, API contestando errores, **cero líneas en el log**. El bug real era el fallo SILENCIOSO, no la sesión.
- **Fix — commit `4aa9a4f`, pusheado y ✅ DESPLEGADO (2026-07-16 13:48).** Dos capas independientes en `scripts/dentidesk_daemon.py`:
  1. **Prevención:** `--disable-dev-shm-usage`. `df -h /dev/shm` en el contenedor → **64M** (default de Docker); Chrome lo agota con horas de polling de la agenda y se estrella (killer canónico de Chrome-en-Docker). Con el flag usa `/tmp`. ⚠️ Sospecha fuerte, NO huella: marcaba `0%` de uso porque Chrome ya estaba muerto.
  2. **Auto-cura:** `_wait_until_dead(page)` reemplaza los dos `while True: sleep(5)`; sale con `sys.exit(1)` al cerrarse la página → PID 1 muere → contenedor reinicia → Chrome reabre con `/data/cookies.json`. Cubre igual si la causa real es otra.
  Tests `test_wait_until_dead_*`, suite 53/53.
- **✅ Verificado tras deploy (13:52, contenedor `b9110268feb8`):** `ps aux` → stack COMPLETO vivo, **cero `<defunct>`**: PID 1 daemon, Chrome browser (26), 2 crashpad, 2 zygotes, network+storage utility, 3 renderers, gpu-process (151), uvicorn API (202, puerto 8100). El flag propagó a los hijos (`--disable-dev-shm-usage` visible en network/gpu/storage).
- **⏳ EL VEREDICTO ES A 24-48h.** Que Chrome viva a los minutos NO prueba nada — el 15 jul también arrancó bien y murió después. Si sigue vivo el 2026-07-18 → era el `/dev/shm`. Si muere, el watchdog lo dirá en el log (`>>> NAVEGADOR MUERTO` + un segundo `>>> Abriendo Chrome`) → ir por el OOM con `dmesg -T | grep -i "killed process"` **en el VPS por SSH**.

### 1b. reCAPTCHA "marca el robot" cuando YA está marcado — NO se puede quitar
- **Causa:** el token del reCAPTCHA de Google vive **~2 minutos**. El daemon abre la pantalla de login y espera 180s ([`dentidesk_daemon.py:111`](scripts/dentidesk_daemon.py#L111)); si el humano llega 10 min después, el check se ve marcado pero el token venció → Dentidesk lo rechaza. **No es debilidad del contenedor** — pasa igual en la PC local, no se nota porque ahí logueas al instante.
- **NO es removible.** Es el formulario de login de Dentidesk, en SU servidor. Nunca prometer lo contrario al usuario (ya se hizo y quemó confianza).
- **Procedimiento cuando toque loguear por VNC** (`https://testagente1-daemonvnc.oyfspa.easypanel.host`, clave = `DAEMON_VNC_PASSWORD`): **F5 primero** para refrescar la página, luego usuario `carlaodontotec@odontotec.com.do` + `Prueba.1` + marcar robot + "Iniciar sesión" **de corrido, sin pausas** (<2 min). Verificado funcionando 2026-07-16.
- **✅ LA COOKIE FUNCIONA — verificado en vivo 2026-07-16 ~16:05.** Restart del contenedor → el daemon fue directo a `home.php` en <1 min, **sin `>>> LOGIN OK — captcha+login tardo Xs`** (esa línea solo sale cuando un humano teclea) + `GET /session 200`. El usuario confirmó por VNC: Chrome adentro de Dentidesk sin pedir nada. `/data` persiste y `_load_cookies()` sirve. **La cadena de auto-cura está COMPLETA:** Chrome muere → contenedor reinicia → cookie restaura → sigue solo, sin humano corriendo al reCAPTCHA.
- **⚠️ PERO la cookie CADUCA — abierto: cuánto dura.** Misma tarde, un restart anterior SÍ pidió login manual (`captcha+login tardo 114.9s`) porque la cookie era **del día anterior**. La de minutos antes sirvió. Lectura: la sesión de Dentidesk **sí expira**, en algún punto entre "minutos" y "un día" — lo que **contradice la regla anotada del cliente** ("la sesión no expira", ver `dentidesk_playwright_bugs_2026-07-07`). NO inventar el número; sale solo con el tiempo. Importa porque define si un reinicio de madrugada se auto-cura o se queda esperando a un humano.
- **Mejora NO construida (YAGNI hasta que la cookie falle):** que el daemon refresque la página de login cada ~90s mientras nadie loguea, para que el captcha nunca esté rancio. Riesgo: interrumpe al humano mientras teclea. No vale la pena si loguear pasa a ser un evento raro.

### 1c. ⚠️ El usuario propuso BOTAR el VPS y poner una PC en la clínica
- **Su argumento:** "en mi PC nunca pasa esto, Dentidesk siempre queda logueado; el VPS tiene recursos pobres".
- **Corrección basada en evidencia:** (a) el Docker **nunca se reinició** (PID 1 con 1 día de uptime) — su premisa era falsa; (b) `/dev/shm` de 64M es un **default de Docker**, no del tamaño del VPS — un VPS de 64GB tendría el mismo bug; (c) en su PC no pasa porque su Chrome no corre dentro de Docker.
- **Recomendación dada:** NO botar el VPS todavía. Los dos problemas que lo quemaron se arreglaron el 2026-07-16; nunca ha visto el sistema correr CON el fix. Una PC en la clínica trae: cortes de luz, alguien cierra Chrome, Windows reinicia solo, y sin acceso remoto para arreglarlo. **Decidir después del veredicto de 48h**, no antes.
- Si la cookie NO aguanta y Chrome sigue muriendo, la conversación de arquitectura se reabre en serio (opción de fondo: escrituras por la API de Dentidesk en vez de Playwright → adiós navegador).
- **⚠️ TEORÍAS DESCARTADAS (no resucitar):** cookie IP-bound, "la cookie no aguanta el reinicio", "falta la línea python en el Dockerfile", "colgado en `launch_persistent_context`/SingletonLock`". Todas **falsas**: el contenedor no reinicia (PID 1 con 1 día), y `daemon_entrypoint.sh:28` sí hace `exec python ...` (además `exec` = PID 1: si Python muriera, el contenedor moriría y no habría logs de Xvfb/VNC). El commit `a646497` (prioridad de cookie) queda como mejora, no como fix.
- **Trampa de lectura:** logs de EasyPanel = la COLA. `DAEMON LISTO` se imprimió el Jul15, arriba del scroll. Sin reinicio no hay salida nueva → "no veo prints de Python" **no** significa que Python no corre.
- **ABIERTO — ¿por qué muere Chrome?** Sospecha SIN confirmar: OOM del host (Resources ilimitado = sin cap de cgroup, pero el kernel del VPS igual mata al proceso más gordo, y Chrome lo es). Evidencia a sacar en el VPS (no dentro del contenedor): `dmesg | grep -i "killed process"`. El fix auto-cura pero no evita la caída.
- Ver memoria `daemon_chrome_muere_silencioso_2026-07-16`.

### 2. Cédula con guion no dejaba guardar cita → RESUELTO + desplegado
- **Causa:** campo `#rut` de Dentidesk es RUT chileno; cédula dominicana con guion (`0310277809-2`) lo pone en rojo, no guarda. Sin guion sí (probado en vivo).
- **Fix (commit `9132a31`):** `_rut_field()` en `dentidesk_playwright.py:~53` deja solo dígitos venga como venga por WhatsApp; vacía→`"1-9"`. Punto único (línea ~494), cubre local y daemon. Test `tests/test_dentidesk_tools.py::test_rut_field` 6/6. Ver memoria `dentidesk_cedula_guion_2026-07-16`.
- **⚠️ La creación de cita corre en el DAEMON** (odontotec delega por HTTP) → el fix toma efecto en `dentidesk-daemon`.

### Deploy de hoy
- **Desplegado a prod (feat/dentidesk-integration):** `dentidesk-daemon` + `odontotec`, ambos con Deploy. Llevaron: `38d5ed0` (cookie), `9132a31` (cédula), `7ea2050`+`269996d` (Carla muda/amnesia), `9aa2996` (/health + .dockerignore).
- **odontotec vivo:** `/health` en `https://testagente1-odontotec.oyfspa.easypanel.host/health` → `{status:ok, model:openai/gpt-5.5}`.

## ✅ WATCHDOG — DESPLEGADO Y PROBADO EN PRODUCCIÓN (2026-07-16 tarde)

Commit `c2a6b40`, pusheado y desplegado. **Ya no es "código que pasa los tests": se ejercitó contra
prod y se vio funcionar.** Prueba real (apagar `dentidesk-daemon` a propósito):

```
CRITICAL:odontotec.watchdog:[CARLA-WATCHDOG] ⚠️ daemon caído: ConnectError: [Errno -2] Name or service not known
INFO:odontotec.alerts:send_dev_alert: email enviado a fondeur28@gmail.com
   (ciclo siguiente, daemon aún caído: cero alerta — edge-trigger funcionando)
```

1. **Detecta** — el DNS de `dentidesk-daemon` deja de resolver y el chequeo falla.
2. **Avisa** — correo **recibido y confirmado por el usuario**. El App Password de Gmail sirve.
3. **Se calla** — segundo ciclo con el daemon caído: silencio. Nada de 288 correos/día.

- **Env vars ya puestas en `odontotec`** (EasyPanel): `SMTP_HOST/PORT/USER/PASS` + `WATCHDOG_ALERT_EMAIL`.
- **Línea de arranque a buscar en logs:** `watchdog: activo, chequea cada 5 min`.
- **Chequeo sano se ve así:** `watchdog check: {'daemon': {'ok': True, 'detail': 'logueado'}, 'llm': {'ok': True, 'detail': 'ok'}, 'metrics': {...0}}`.
- El chequeo del LLM pega contra **openrouter.ai** (`/health` dice `openai/gpt-5.5` pero `OPENAI_BASE_URL` rutea por OpenRouter) — o sea que vigila exactamente donde ocurrió el 402 que dejó a Carla muda 2 días. Correcto, no tocar.
- ⚠️ **NO esperar el correo de "recuperado" si reinicias `odontotec`.** `_last_ok` vive en memoria; al reiniciar arranca vacío y sin transición enfermo→sano no hay aviso. Es el diseño (falla del lado seguro: si al arrancar algo está caído, SÍ alerta). No es un bug, no perseguirlo.
- ⚠️ **Suite: 103 pasan, 6 fallan + 2 errores PREEXISTENTES** (`test_chatwoot.py`, `scripts/test_dentidesk_cookies.py`): `respx` no intercepta httpx con Python 3.14. Verificado con `git stash` que ya fallaban en HEAD limpio. **No son del watchdog, no perseguirlos.** El "suite 53/53" de más abajo es de otro intérprete.
- De regalo en el mismo commit: `_fecha_no_pasada()` bloquea agendar/reagendar hacia fecha ya pasada (Dentidesk lo aceptaba sin chistar y deshacerlo era a mano).
- Ver memoria `watchdog_implementado_2026-07-16` y `docs/watchdog-alertas-plan-fase2-migracion-numero.md`.

## 🔴 PENDIENTE (próxima sesión)

0. **🔔 ALERTA DE ESCALADA — el hueco más caro que queda.** Pedido por el usuario 2026-07-16 tarde,
   **para hacer esta noche o mañana** (decidió no hacerlo el mismo día). Hoy, cuando Carla le pasa
   un paciente a un humano, **NO se entera nadie**:
   ```python
   def _escalate_to_human(reason: str, conversation_id: int) -> dict:
       chatwoot.add_label(conversation_id, "bot-off")   # <- eso es TODO
       return {"success": True, "escalated": True}
   ```
   Una etiqueta pasiva en Chatwoot. Si nadie está mirando, el paciente pidió un humano y espera sin
   que se entere un alma. Es el único agujero con **una persona concreta esperando del otro lado**.
   - **Tampoco avisa un mensaje suelto que Carla no contestó:** `agent_failed` existe pero su umbral
     son 3 fallos en el MISMO ciclo de 5 min. Está pensado para "Carla está caída", no para "a este
     señor no le contestaron". (Y `escalate_blocked` es lo CONTRARIO: cuenta escaladas que el
     guardrail impidió.)
   - **Recomendación (verificada en código, no teoría):** una línea en `_escalate_to_human` llamando
     a `alerts.send_dev_alert(...)`. `integrations/alerts.py` **ya tiene los dos canales escritos**:
     email (vivo hoy) y WhatsApp (dormido, solo le falta `WATCHDOG_ALERT_INBOX_ID`). O sea que sale
     por email ahora y **se convierte en WhatsApp solo, sin tocar código**, cuando aterrice la Fase 2.
     Esta es justo la intuición del usuario: escribir el código ya, enchufarlo cuando llegue el número.
   - **Va en el contenedor `odontotec`**, no en el daemon (lo notó el usuario, correcto: la escalada
     ocurre en el agente).
   - ⚠️ **Cuidado al construirlo:** `send_dev_alert()` es SMTP **síncrono con timeout de 15s**. Metido
     tal cual en el loop del agente, le puede colgar la respuesta al paciente hasta 15s. Mandarlo
     fire-and-forget (BackgroundTasks o hilo), no inline.
   - **Pregunta abierta que decide el diseño: ¿cada cuánto escala Carla?** Sin ese dato no se sabe si
     es correo por escalada (3/día = bien) o agrupado (30/día = buzón quemado). El usuario no lo
     supo responder. Barato de medir: `metrics.increment("escalated")` y leerlo en el log del
     watchdog, que ya reporta métricas cada 5 min.
   - **Canal preferido del usuario: WhatsApp** a su número o al de quien atienda en la clínica —
     "el correo se te llena y nadie lo mira". Bloqueado por lo mismo que la Fase 2 (inbox dedicado +
     plantilla Meta), NO por código.

0b. **🕐 ALERTA DE CONVERSACIÓN ESTANCADA — pedido por el usuario 2026-07-22.** El watchdog debe
   avisar cuando **Carla lleva +5 min con una conversación ABIERTA con un cliente sin concluirla**,
   sea cual sea la causa: daemon Dentidesk caído, saldo LLM en 0, Chatwoot, VPS, cualquier cosa.
   - **Por qué es distinto de todo lo que ya existe:** este NO parte de una causa conocida, parte del
     **síntoma visible al paciente** (lleva rato esperando y la conversación no cerró). El watchdog
     actual vigila las CAUSAS (daemon/LLM/métricas); esto vigila el EFECTO. Cubre el hueco de un fallo
     que ninguna sonda anticipó. Distinto de la alerta de escalada (#0: dispara cuando Carla PIDE un
     humano) y de `agent_failed` (3 fallos en un ciclo = "Carla caída").
   - **Señal detectable (verificar en `integrations/chatwoot.py`):** conversación con status `open`,
     último mensaje del PACIENTE (no de Carla), antigüedad de ese mensaje > 5 min, sin label de
     resuelto/`bot-off`. El watchdog ya corre cada 5 min → encaja natural como un chequeo más.
   - **Reusa lo que ya hay:** `alerts.send_dev_alert()` (email vivo, WhatsApp dormido hasta Fase 2) +
     edge-trigger (avisar UNA vez por conversación estancada, no cada ciclo — igual que el watchdog no
     spamea). Va en el contenedor **`odontotec`** (el watchdog vive ahí).
   - **Abierto:** ¿Chatwoot expone el timestamp del último mensaje y quién lo envió por API? (revisar
     antes de escribir). Si no, hay que leer los mensajes de cada conversación abierta.
   - Ver [[watchdog_implementado_2026-07-16]] y [[pendiente_alerta_escalada_2026-07-16]].

1. **✅ VEREDICTO DEL DAEMON (2026-07-22): la auto-cura FUNCIONA en producción — probada en vivo.**
   Cerca de medianoche del 2026-07-22 Chrome **flapeó** (murió/reinició en ráfaga): el watchdog mandó
   varios pares caído↔recuperado por correo (transiciones reales, edge-trigger OK). **En la mañana el
   usuario entró por el daemon a Dentidesk y creó pacientes sin problema.** → Lectura: el
   `--disable-dev-shm-usage` **NO evitó** que Chrome muriera (la prevención falla), PERO la cadena de
   auto-cura (`_wait_until_dead` → exit 1 → contenedor reinicia → cookie restaura) **se recuperó sola
   cada vez y el sistema quedó usable.** El usuario dio esto por bueno: "que avise que se cayó y volvió,
   está bueno" — **NO perseguir por qué muere Chrome** (YAGNI). Esto **PESA A FAVOR DE QUEDARSE EN EL
   VPS** (ver 1c): incluso con Chrome inestable, el VPS se auto-repara y avisa; una PC en la clínica no
   tendría ni la auto-cura ni el aviso. Si algún día molesta el flapping: sacar el OOM con
   `dmesg -T | grep -i "killed process"` en el VPS por SSH y/o buscar un cron nocturno (`crontab -l`).
2. **⭐ FASE 2 — Migrar Carla al número oficial de la clínica + formalizar plantillas.** El pendiente que el usuario marcó como prioritario (2026-07-16). Dos partes: (a) cambiar `whatsapp_phone_number` al número oficial Meta de Odontotec cuando el cliente lo entregue; (b) **formalizar el trabajo con las plantillas** (templates aprobadas por Meta) — es lo que desbloquea también la Fase 3 (recordatorios 48h/24h, código listo en `c8a7a33`, pausado por falta de `CHATWOOT_INBOX_ID` + template aprobada). Ver `docs/carla-plantilla-confirmacion.md` (texto real del cliente) y memoria `pendiente_numero_clinica_plantillas_2026-07-16`.
3. **Verificar end-to-end con Carla** (empleados van a probar): agendar cita por WhatsApp dando cédula CON guion (`031-0277809-2`) → debe guardar en Dentidesk sin guion. Guión de prueba entregado al usuario.
4. **/health dice `commit: desconocido`** — ✅ **causa CONFIRMADA 2026-07-16** (`ls -la /app/.git/HEAD` en la Terminal de `odontotec` → `No such file or directory`). El `.dockerignore` **no** excluye `.git` (tiene un comentario explícito de que se deja a propósito), pero **EasyPanel manda el contexto de build sin `.git`** igual. Por eso `_git_commit()` cae al `except` y devuelve `desconocido`.
   - **Fix propuesto:** `ARG GIT_COMMIT` + `ENV GIT_COMMIT=$GIT_COMMIT` en el Dockerfile, y que EasyPanel lo pase como build arg en cada build. **Sin verificar si EasyPanel soporta build args** — averiguar antes de escribir nada.
   - ⚠️ **NO ponerlo como env var normal de runtime:** se queda fijo y a los 3 deploys te MIENTE diciendo un commit que ya no corre. `desconocido` es honesto; una mentira es peor.
   - ⚠️ **TEORÍA FALSADA (no resucitar):** "las refs vienen empaquetadas en `packed-refs` en un clon fresco y por eso `open(.git/refs/heads/...)` revienta". **Falso, probado**: `git clone` local → la ref del branch checkouteado SÍ queda suelta y `_git_commit()` devolvió el hash correcto. Casi se arregla un bug inexistente.
   - **Mientras tanto, para saber qué código corre sin el hash:** `ls -la /app/scheduler/watchdog.py` en la Terminal del contenedor y comparar bytes con `git cat-file -s $(git rev-parse HEAD:scheduler/watchdog.py)`. Así se confirmó el deploy de hoy (4989 = 4989).
5. **Seguridad:** rotar `DENTIDESK_WEB_PASS` (=`Prueba.1`, débil); quitar dominio VNC temporal (`testagente1-daemonvnc...`, expone el navegador logueado); `WEBHOOK_SECRET` falta en servicio `odontotec`.

> ~~"Daemon: ¿por qué se reinicia?"~~ — **pendiente ELIMINADO**: el daemon NO se reinicia. Evidencia `ps aux` 2026-07-16 (PID 1 con 1 día de uptime). Lo que moría era Chrome. No resucitar esta línea de investigación.

## Handoff 3 fases (2026-07-10) — Fase 1 casi cerrada

### Fase 1: Pruebas Carla ↔ Dentidesk + Deploy fixes
- **Status:** deploy HECHO (arriba). Falta: verificación end-to-end con Carla (pendiente #1).
- **Env vars Dentidesk (para redeploy a otra PC):** Ver doc `dentidesk_credenciales_api_root_cause_2026-07-10.md` — contiene TODAS

### Fase 2: Migrar a número oficial Meta del cliente
- **Status:** PREPARADO, en pausa
- **Próximo:** Cliente da número oficial → cambiar whatsapp_phone_number en código + test e2e

### Fase 3: Recordatorios 48h/24h (SMS/WhatsApp)
- **Status:** Código listo (commit c8a7a33), PAUSADO
- **Blocker:** CHATWOOT_INBOX_ID + template aprobada por cliente
- **Track:** B (velocidad Playwright)

---

## Bugs / Issues recientes

### ✅ RESUELTO: Carla muda (2026-07-13)
- **Causa:** OpenRouter saldo 402 (crash) + 3 silencios en código (razonador/historial/except)
- **Fix:** Commits 7ea2050 + 269996d + 829a576
- **Deploy:** HECHO 2026-07-16 (odontotec redeployado desde feat/dentidesk-integration).
- **Notas:** Recargar moneda en OpenRouter revive el agente; los 3 commits cierran silencios para el futuro

### ✅ RESUELTO: Dentidesk credenciales
- **Causa:** Faltaban DENTIDESK_USER/DENTIDESK_PASS en `.env` prod (búsquedas vía API, no daemon)
- **Fix:** Commit 829a576
- **Verificación:** Reagendado José Gabriel 13→21 jul confirmado vía API

### ✅ Daemon Dentidesk en prod — DESPLEGADO y logueado
- **Status:** servicio `dentidesk-daemon` corriendo en EasyPanel (proyecto `testagente1`), volumen `/data`, env vars OK, cookie de sesión persistente (ver RESUELTO HOY #1).
- **Impacto:** Playwright crea/reagenda citas vía API interna del daemon.
- **Pendiente:** investigar por qué se reinicia si se quiere cero caídas (ver PENDIENTE #2).

---

## Stack

- **Agent:** Python, `run_agent()` con anti-bucle
- **LLM:** gpt-5.5 vía OpenAI (era OpenRouter, cambio reciente)
- **Integración Dentidesk:** API HTTP + Playwright (persistent browser vía CDP)
- **Chat:** Chatwoot (fix SMTP + webhook→polling confirmado)
- **Airtable:** Tabla Citas (base appEtSSoyYpUPgj2Q) — temporal hasta Dentidesk
- **Recordatorios:** Playwright track B (código listo, pausado)

---

## Archivos críticos

- `agent/run_agent.py` — punto entrada, citas → Dentidesk
- `integrations/dentidesk_playwright.py` — Playwright + selectores reales
- `integrations/dentidesk_api.py` — API wrapper (login, create/move appointment)
- `.env` — DENTIDESK_USER/PASS (¡LEER memoria si olvidado!)
- `docs/handoff-bug-escalate-reagendar-2026-07-08.md` — contexto reagendado
- `docs/carla-plantilla-confirmacion.md` — template cliente real

---

## Codebase Memory MCP

Proyecto indexado. Usa PRIMERO:
- `search_graph(name_pattern="run_agent")` — encontrar funciones
- `trace_path(function_name="...", direction="both")` — quién llama a X
- `detect_changes()` — impacto de cambios locales
- `get_architecture()` — vista general

Fallback: grep/Glob si grafo insuficiente.

---

## Comandos quick

```bash
# Pytest suite (54 tests, all passing last check)
pytest

# Local run (dev)
python -m agent.run_agent

# Check env
cat .env | grep DENTIDESK
```

---

## ⚠️ Notas críticas

1. **Saldo OpenRouter:** Monitoreado ahora; si baja → Carla muda. Recargar o cambiar a OpenAI.
2. **Chatwoot webhook:** No dispara nada; solución implementada = poller.py cada 10s.
3. **Dentidesk sesión:** Login manual 1 vez → persiste. No resetear perfil Chrome.
4. **Acordeón:** No escala al reagendar (fix verificado prod, commit 4e1cf31).
5. **Especialistas:** Caries + otros NO llevan especialista (personal fijo, regla cliente).

---

## Sesión anterior

- Indexé repo en Codebase Memory (552 nodos)
- Creé CLAUDE.md local (este archivo)
- Próximo: verificar si 829a576 ya en prod; si no, desplegar

**Contacto:** fondeur27@hotmail.com | GitHub: fondeur27-09-73 (private)
