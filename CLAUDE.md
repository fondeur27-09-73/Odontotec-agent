# CLAUDE.md — Carla Odontotec WhatsApp Agent

## 🔴 ARRANCAR AQUÍ — Sesión 2026-08-21 (tarde): INCIDENTE EN PRODUCCIÓN, RESUELTO

**En una línea:** durante el e2e se descubrió que **NINGUNA cita se podía crear** — el daemon
devolvía `502` y Carla repetía el GUION F. Había **pacientes reales esperando**. Arreglado en 3
iteraciones; la última creó la cita de verdad (`IdAgenda 2357240`).

### La cadena del fallo (cada capa tapaba a la siguiente)

1. `_type_rut_recognize` cerraba el autocompletar de `#rut` con **Enter**. El Enter no solo cierra:
   **SELECCIONA la fila resaltada**. Un registro viejo de cédula corta (`031`, `0310`) matchea por
   PREFIJO — y en Santo Domingo ese prefijo es comunísimo → Dentidesk sobrescribía la cédula y
   enlazaba **la ficha de otra persona**.
2. El guard del 19-ago lo detectaba y abortaba (**correcto**: mejor no agendar que agendar mal),
   pero eso dejó **sin poder agendar a todo paciente con ese prefijo**. Guard bueno, sistema roto.
3. Se dependía de que el cliente borrara los registros de cédula corta. **Nunca lo hizo.** Ya NO
   hace falta.

### El arreglo (3 commits, cada uno destapó el siguiente error)

| Commit | Qué |
|---|---|
| `b08f5cb` | No más Enter. Si hay una fila con la cédula EXACTA → se clica (enlaza al paciente correcto). Si no → cerrar sin seleccionar. |
| `9590089` | **Escape NO**: el modal de cita es Bootstrap y Escape lo cierra ENTERO → el `fill("#fono")` siguiente esperaba 30s por un campo que ya no estaba. Se cierra con **blur**. |
| `5d7727c` | Dentidesk igual dejaba `031` en el campo. Ahora se le **IMPONE** la cédula (value + eventos `input`/`change`) y se pone `#id_paciente=0` → ficha nueva con la cédula completa. |

📌 **Regla del usuario (2026-08-21):** que un registro viejo coincida por 3, 5 o 6 dígitos **da
igual, es normal**. Lo ÚNICO que no puede pasar es que el campo acabe con algo distinto de **los 11
dígitos tecleados**. El guard sigue como red de seguridad y solo aborta si ni así se puede restaurar.

**Resultado verificado en vivo:** `{"success":true,"IdAgenda":"2357240","IdPaciente":"455642",
"paciente_existe":false}` — ficha NUEVA, no colgada de la de nadie.

### 🔧 Cómo se diagnosticó (repetir esto la próxima vez, ahorra horas)

Los logs de `odontotec` solo dicen `502`, y los del daemon estaban **inundados** por el heartbeat.
Lo que funcionó fue **pedirle el error al daemon directamente**, porque el `502` lleva el motivo en
el cuerpo (`detail=str(e)`) y nadie lo loguea:

```sh
# en dentidesk-daemon -> Terminal
curl -s -X POST localhost:8100/crear_cita -H 'Content-Type: application/json'   -H "X-Daemon-Secret: $DENTIDESK_DAEMON_SECRET"   -d '{"cedula":"03102795602","patient_name":"Anthony Ramirez","phone":"+18099790205","doctor_label":"General General","fecha_iso":"2026-08-25","time":"15:00","procedimiento":"Limpieza dental","sucursal":"214"}'
```
⚠️ Escribe en Dentidesk de verdad si sale bien.

### 👁️ Los 3 agujeros que hicieron esto invisible — ARREGLADOS Y DESPLEGADOS (`3cd3c27`)

El incidente no duró horas por ser difícil, sino porque **nada avisó**. Los tres fallaban en silencio:

1. **El heartbeat se tragaba la muerte de Chrome** (`ignoro y sigo`): cientos de líneas idénticas
   tapando el traceback real, y si moría el navegador entero el daemon seguía vivo sobre un cadáver.
   Ahora **reabre la pestaña como sonda de vida REAL**: si tampoco puede, sale → el contenedor
   reinicia y se auto-cura. Log limitado a la 1ª vez y luego 1/hora.
2. **El watchdog no vigilaba las escrituras.** Solo miraba `/session`, que devolvía 200 mientras
   `crear_cita` llevaba horas en `502`. Nueva métrica **`cita_write_failed`** (`_escribir_o_contar`
   en `agent/tool_handlers.py`) y **alerta al PRIMER fallo**: una cita que no entra ya es un
   paciente sin cita.
3. **Carla atascada pasaba por sana.** `_patient_waiting_secs` daba la conversación por buena en
   cuanto el último mensaje era de Carla — pero repetía el GUION F ("permítame un momento") sin
   parar y el paciente nunca conseguía cita. **`_carla_en_bucle`** detecta 2+ respuestas idénticas
   seguidas y alerta: "Carla está ATASCADA en la conversación X".

Con esto, el escenario del 21-ago habría mandado 3 correos en los primeros 5 minutos.

**Verificado en los contenedores (2026-08-21):** daemon `dentidesk_daemon.py` 15136 +
`/session {"logueado":true}` (se auto-curó tras el reinicio, sin VNC); odontotec `tool_handlers.py`
24178 · `watchdog.py` 11107 · `metrics.py` 1300. Suite: **148 pasan**.

### ⏳ PENDIENTE PARA LA PRÓXIMA SESIÓN (por orden)

1. **Verificar en Dentidesk la cita `IdAgenda 2357240`**: 25-ago 3:00 PM · Anthony Ramirez ·
   `Dr. General General` · cédula completa `03102795602` (NO `031`/`0310`). Quedó sin comprobar.
2. **🕐 La Sra. Bastardo (conv 18, `+18294753460`) se quedó SIN CITA.** Cayó en el bucle del GUION F
   mientras el daemon estaba roto. Retomarla desde Chatwoot o dejar que Carla lo reintente.
3. ✅ **HECHO Y DESPLEGADO** — los 3 agujeros de observabilidad (commit `3cd3c27`). Ver el bloque
   👁️ de abajo.
5. **📋 PREGUNTARLE AL CLIENTE — 4 preguntas sobre los doctores.** Mientras no conteste, Carla
   trabaja con un listado incompleto:
   - **¿Qué especialidad atiende cada uno?** Están en Dentidesk pero Carla NO puede elegirlos hasta
     saberlo: **Dra. Mirleinis Casado**, **Dra. Monica Vargas**, **Dr. Roner Capellan**.
   - **¿Y la Dra. Ekaterina Fernandez?** El cliente la dio como ODONTOPEDIATRÍA, pero **NO aparece
     en el listado de Profesionales de Dentidesk**. ¿Ya no trabaja ahí, o falta registrarla en el
     sistema? Se quitó del prompt por no estar en Dentidesk. ⚠️ **Si sigue trabajando, hay un
     problema real: odontopediatría se queda con UNA sola doctora (Daniela Bastidas)** — y si ella
     falta o se llena, no hay a quién agendar los niños.
   - **La regla:** cada vez que cambien un doctor en Profesionales de Dentidesk (alta, baja o cambio
     de especialidad), **tienen que avisarnos** para actualizar `agent/prompts.py`. Carla no se
     entera sola.
   - **¿Falta alguien más?** El listado que tenemos se sacó de una captura del sidebar (17
     profesionales, termina en Dr. Roner Capellan). Que lo confirmen completo.
6. **E2E completo por WhatsApp** (agendar + reagendar para un tercero), que fue lo que destapó todo
   esto y sigue sin terminarse.
7. Lo de antes: limpiar env vars basura de `odontotec` (`SMTP_*` duplicado — **la buena es la de
   abajo**, líneas 28-31) y los recordatorios masivos (Fase 3, bloqueada por plantilla de Meta).

---

## 🟢 (previo)  Sesión 2026-08-21 CERRADA: todo desplegado y verificado

**En una línea:** los dos bugs del 19-ago (cita a nombre de quien escribe · ortodoncia con una
cirujana) están **arreglados, desplegados y verificados en el contenedor**; se cerró además el mismo
hueco al REAGENDAR y un bug de teléfonos compartidos en familia. **Falta solo el e2e por WhatsApp.**

| Cosa | Estado |
|---|---|
| `odontotec` corriendo `a2b14a0` (4 archivos verificados byte a byte) | ✅ |
| `dentidesk-daemon` al día + auto-curado tras el reinicio (`logueado: true` solo) | ✅ |
| Guard del `#rut` del 19-ago | ✅ **desplegado, pendiente cerrado** |
| Alertas del watchdog a los 2 correos, verificadas en el contenedor | ✅ |
| Crédito del LLM | ✅ +8 USD — **está en OPENROUTER**, no en platform.openai.com |
| **E2E por WhatsApp (agendar + reagendar para un tercero)** | ❌ **LO ÚNICO QUE FALTA** |

### ▶️ LO PRIMERO AL RETOMAR: el E2E

Un solo hilo al **849**, y prueba todo de golpe:
1. **Agendar para un familiar** → Carla debe preguntar *"¿la cita es para usted o para otra
   persona?"* y pedir nombre + cédula (11 díg) **del familiar**. La cita cae en la ficha DEL
   FAMILIAR.
2. **Reagendar esa misma cita** → debe preguntar *"¿a nombre de quién está la cita?"*, buscarla por
   ese nombre y **confirmar de quién es antes de moverla**.
3. Si la primera es de **ortodoncia**, debe caer en `Dr. Ortodoncia Ortodoncia`; si es limpieza o
   caries, en `Dr. General General`.

### 🔒 Los 4 candados que quedaron puestos (todos verificados con tests)

1. **Agendar para un tercero** — `cita_para_tercero` obligatorio + `_datos_del_paciente_real()`:
   si declara "es de un tercero" pero manda el nombre/cédula del titular del teléfono, corta con
   `datos_del_titular` **antes** de tocar Dentidesk. Corre ANTES de mirar la especialidad → **aplica
   a TODOS los servicios**, no solo ortodoncia.
2. **Buscar la cita de un tercero** — `_datos_de_busqueda_del_tercero()` descarta el teléfono y la
   cédula que se puedan PROBAR del titular (están en la BD local con otro nombre). Buscar con el
   teléfono de quien escribe devolvía **SU** cita → Carla movía la cita equivocada, sin error.
   Si el teléfono resulta ser de verdad el del familiar, **se conserva**.
3. **El teléfono ya no decide el match** (`_cita_matches` en `integrations/dentidesk.py`) — una
   familia entera deja el mismo número. El teléfono se probaba ANTES que el nombre → buscando al
   primo salía la cita de la mamá. Ahora, **con nombre (>=2 tokens) el teléfono no cuenta**; manda
   nombre+apellido o cédula de 11 dígitos, como en Dentidesk. Sin nombre, el teléfono sigue valiendo.
4. **Elegir doctor** — `_DOCTORES_DENTIDESK` + `_doctor_de_la_lista()`: un nombre fuera del listado
   real se rechaza con `doctor_desconocido` sin escribir nada.

### 🔁 Si el paciente dice "esa no es" al reagendar (escalera, una cosa por mensaje)

1. Otra vez por el nombre del familiar — pedir que lo **deletree como está en la cédula**.
2. Por la **cédula del familiar** (11 dígitos).
3. Por el nombre de **QUIEN ESCRIBE** — hay citas viejas de familiares que quedaron guardadas a
   nombre de quien las pidió (por el bug de antes del 2026-08-20). Confirmarla igual antes de mover.

⚠️ **Eso es parche de LECTURA, no de datos.** Las citas mal creadas siguen con el nombre equivocado
en Dentidesk. Corregirlas es a mano, en el CRM.

### ⏳ PENDIENTE DEL CLIENTE — 3 doctores sin especialidad

Están en el listado de Profesionales de Dentidesk, pero **no sabemos qué atienden**, así que el
prompt le PROHÍBE a Carla elegirlos:

- **Dra. Mirleinis Casado**
- **Dra. Monica Vargas**
- **Dr. Roner Capellan**

Y **Dra. Ekaterina Fernandez** se eliminó: estaba en la lista vieja del cliente como
odontopediatría, **no existe en Dentidesk**.

📌 **Regla acordada:** el listado de profesionales es **el de Dentidesk**, no el del cliente. Cada
vez que la clínica cambie un doctor allí, **tiene que avisarnos** para actualizar `agent/prompts.py`.

### 🧪 Cómo verificar un deploy (el `/health` sigue diciendo `commit: desconocido`)

Tamaños **LF** de cada archivo, sacados de git — NO usar `wc -c` del working copy en Windows, que
cuenta CRLF (1 byte de más por línea) y te hace perseguir un fantasma:

```bash
git cat-file -s HEAD:agent/prompts.py     # el número que debe dar el contenedor
```
```sh
# en el contenedor -> Terminal
wc -c /app/agent/prompts.py /app/agent/tool_handlers.py /app/agent/claude.py /app/integrations/dentidesk.py
```
`a2b14a0` = **31365 · 23362 · 19792 · 12735**. Prueba binaria extra: `ls /app/agent/tools.py` debe
decir **No such file** (ese archivo se borró el 2026-08-20).

---

## 🟢 (previo) Sesión 2026-08-20: 2 bugs de agendado arreglados + limpieza del prompt

**En una línea:** Carla agendaba **a nombre de quien escribe** cuando la cita era de un familiar, y
mandaba **ortodoncia a una cirujana**. Las dos cosas arregladas en código + prompt. ⏳ **falta Deploy
de `odontotec`.**

| Qué | Estado |
|---|---|
| Guardrail de cita para un TERCERO (`cita_para_tercero`) | ✅ código + prompt + tests |
| Carla ELIGE el doctor del listado real de Dentidesk (`doctor`) | ✅ código + prompt + tests |
| Fichas genéricas `Dr. Ortodoncia Ortodoncia` / `Dr. General General` | ✅ |
| Alertas del watchdog a 2 correos | ✅ **env var puesta y verificada en el contenedor** |
| `agent/tools.py` (esquema muerto) borrado | ✅ |
| Deploy de `odontotec` | ✅ **VERIFICADO 2026-08-21** |

Suite: **140 pasan** (los 6+2 de respx/py3.14 siguen preexistentes).

### ✅ VERIFICACIÓN DEL DEPLOY (2026-08-21) — cómo se comprobó sin el hash

`/health` sigue diciendo `commit: desconocido` (EasyPanel manda el build sin `.git`). Se verificó por
**tamaño de archivo** en la Terminal del contenedor, y con una prueba binaria: este commit **borró**
`agent/tools.py`, así que si el archivo NO existe, el deploy tomó.

```sh
# en odontotec -> Terminal
ls -la /app/agent/tools.py; wc -c /app/agent/prompts.py /app/agent/tool_handlers.py /app/agent/claude.py /app/integrations/alerts.py
```
Resultado 2026-08-21: `tools.py` **no existe** ✅ y los bytes cuadran exactos —
`prompts.py` 30262 · `tool_handlers.py` 21787 · `claude.py` 19084 · `alerts.py` 4047.

⚠️ **OJO al comparar bytes:** el repo local (Windows) guarda CRLF y el contenedor LF → el archivo
del contenedor pesa **1 byte menos por línea**. El tamaño bueno para comparar es el de git:
`git cat-file -s HEAD:<ruta>`, no `wc -c` del working copy.

`echo $WATCHDOG_ALERT_EMAIL` en el contenedor → los dos correos ✅.

### ✅ `dentidesk-daemon` desplegado y auto-curado (2026-08-21)

`wc -c /app/integrations/dentidesk_playwright.py` → **39142** = commit `0c61ce9`. O sea:
- ✅ **el guard del autocompletar de `#rut` SÍ está desplegado** — ese pendiente del 19-ago queda
  CERRADO.
- ⚠️ le falta el cambio de ayer (HEAD = **39415**): que `_select_doctor_verified` prefiera la
  coincidencia EXACTA sobre la parcial al elegir en el desplegable de doctores.

**RESUELTO el mismo día:** Deploy del daemon hecho → `wc -c` da **39415** ✅ y `/session` devolvió
`logueado: true` **sin intervención humana** — la cadena de auto-cura (reinicio → cookie de `/data`
→ Dentidesk) funcionó otra vez. **Los dos contenedores corren el mismo commit.**

⚠️ Recordatorio para futuros deploys del daemon: reinicia Chrome. Si la cookie hubiera caducado
tocaría loguear por VNC (F5 primero, luego usuario+clave+robot de corrido, <2 min por el reCAPTCHA).

### 🐛 BUG 1 — la cita de un familiar se agendaba a nombre de quien escribía

Caso real (2026-08-19): **Karen Ferreras** escribió para agendarle a un primo. Carla creó la cita
**a nombre de Karen**, no del primo. Ya había pasado antes (el usuario pidiendo cita para su
hermano). Causa: el PASO 2 asumía que el paciente = quien escribe; `get_patient(phone)` devolvía a
Karen y ese nombre se arrastraba hasta Dentidesk. Si además ya estaba registrada, le colgaba otra
cita a SU ficha.

**Fix, 3 capas:**
1. **Prompt — PASO 2B nuevo** (obligatorio, antes de PASO 3): *"¿La cita es para usted o para otra
   persona?"*. Si es de un tercero: pedir nombre + cédula DEL PACIENTE, prohibido usar el nombre de
   WhatsApp / el de `get_patient` / el de un paciente ya registrado, prohibido `save_patient` con
   los datos del tercero (dañaría el expediente de quien escribe). Regla crítica **2c**.
2. **Tool `agendar_cita_dentidesk`: campo `cita_para_tercero` OBLIGATORIO.** El modelo tiene que
   declarar de quién es la cita en cada agendado.
3. **Código — `_datos_del_paciente_real()`**: si dice "es de un tercero" pero manda el nombre o la
   cédula del titular del teléfono → corta con `error: datos_del_titular` ANTES de tocar Playwright.
   De regalo: en cita de tercero ya NO se busca duplicado por el teléfono de quien escribe (antes
   encontraba la cita del titular ese día y abortaba la del familiar).

### 🐛 BUG 2 — ortodoncia agendada con una cirujana

`_DOCTOR_DEFAULTS["ortodoncia"]` era `"Cabrera"` → matcheaba a **Dra. Altemi Cabrera Sime**, que NO
es la ficha de ortodoncia. Y `"general"` era `""` = *no tocar el desplegable* → se quedaba el doctor
que Dentidesk pusiera por defecto.

**En Dentidesk hay fichas dedicadas** (nombres REALES, vistos en el sidebar de Profesionales):
`Dr. Ortodoncia Ortodoncia` · `Dr. General General` · `Dr. Periodoncia Especialistas`.

**Fix — ahora Carla ELIGE el doctor, no un mapeo ciego:**
- Campo `doctor` **obligatorio** en `agendar_cita_dentidesk` (y `nuevo_doctor` opcional al
  reagendar). El prompt lleva el listado real agrupado por especialidad.
- **Candado**: `_DOCTORES_DENTIDESK` + `_doctor_de_la_lista()` en `agent/tool_handlers.py`. Un
  nombre fuera del listado → `doctor_desconocido`, **no toca Dentidesk**. Tolera "Dr./Dra." y tildes.
- Sin elección → cae al default de la especialidad (red de seguridad, no el camino normal).
- Test `test_fichas_genericas_estan_en_el_listado_real`: si un nombre cambia en Dentidesk, revienta
  el test, no la producción con un paciente delante.
- ⚙️ **Mecánica (para no confundirse):** Carla NO escribe el nombre en Dentidesk. Decide → el código
  valida contra el listado → **Playwright SELECCIONA la opción del `<select #dentista_cita>`**, como
  un humano con el cursor, y verifica que quedó puesta antes de Guardar.

### 📋 LISTADO DE PROFESIONALES = LO QUE DIGA DENTIDESK (regla del usuario, 2026-08-20)

No inventamos doctores ni especialidades: **la fuente de verdad es el sidebar de Profesionales de
Dentidesk**. Si el cliente cambia un doctor allí, **tiene que avisarnos** para actualizar el prompt.
Listado completo al 2026-08-20 (17, termina en Roner Capellan):

| Especialidad | Doctores (nombre EXACTO) |
|---|---|
| ORTODONCIA | `Dr. Ortodoncia Ortodoncia` (ficha única) |
| GENERAL | `Dr. General General` (ficha única) |
| PERIODONCIA | `Dr. Periodoncia Especialistas` |
| ENDODONCIA | Aimer Cedano · Anibel Chalas · Edra Vargas |
| CIRUGÍA | Angel Lee · Disiris Santana · **Altemi Cabrera Sime** |
| PRÓTESIS | Adriana Abreu · Jeffray Lora · Julia Montilla · Marcelle Morales |
| ODONTOPEDIATRÍA | Daniela Bastidas |

- **Dra. Ekaterina Fernandez ELIMINADA**: estaba en la lista vieja del cliente, **no existe en
  Dentidesk**.
- ⏳ **FALTA PREGUNTARLE AL CLIENTE la especialidad de 3 doctores** que sí están en Dentidesk:
  **Dra. Mirleinis Casado**, **Dra. Monica Vargas**, **Dr. Roner Capellan**. Mientras tanto el
  prompt le dice a Carla que NO los elija.

### 🧹 Limpieza del prompt (basura que confundía)

- **`agent/tools.py` BORRADO.** Era un segundo esquema de tools que **no importaba nadie** y estaba
  desactualizado (sin `buscar_cita_proxima_dentidesk`, sin el campo `nombre`). ⚠️ **El esquema REAL
  que ve el modelo es `OPENAI_TOOLS` en [agent/claude.py](agent/claude.py) — editar SIEMPRE ahí.**
- **Número oficial**: `+1 809-977-9329` → **`+1 849-410-7913`** (confirmado por el usuario).
- **PASO 3**: mapeo de especialidades explícito, con la línea que faltaba — *"limpieza DE ortodoncia
  es ortodoncia; una limpieza normal NO es ortodoncia"*.
- `dias` de `buscar_cita_proxima_dentidesk` decía "default 30" y el código usa 10. Alineado.

### 📧 Watchdog — mismas alertas, ahora a DOS correos

`WATCHDOG_ALERT_EMAIL` acepta varios destinos separados por coma (`integrations/alerts.py`,
`_destinatarios()`). El watchdog en sí NO se tocó: sigue chequeando cada 5 min, umbral de paciente
esperando **5 min** (`WATCHDOG_STALLED_MIN`), edge-trigger, sin spam.

⏳ **PENDIENTE (EasyPanel → `odontotec` → Environment):**
```
WATCHDOG_ALERT_EMAIL=fondeur28@gmail.com,contactoodontotec@gmail.com
```
Con eso los dos reciben: daemon caído/recuperado, LLM caído, conversación estancada +5 min, y
escalada a humano. ⚠️ La clínica recibirá también los avisos técnicos; si molesta, hay que separar
canales (otra env var + código).

### 🧹 PRIMERA TAREA DE LA PRÓXIMA SESIÓN — limpiar env vars basura de `odontotec`

Visto en la captura del Environment de `odontotec` (2026-08-20), **decidido dejarlo para la próxima
sesión**. NO tocar en caliente:

- **`SMTP_*` DUPLICADO con contraseñas DISTINTAS**: líneas 14-17 (`SMTP_PASS=rclouvjgqdewojvf`) y
  otra vez 28-31 (`SMTP_PASS=luzlnkrpzamfuojd`). En un archivo de entorno **gana la última**, así
  que hoy manda la de la línea 31 — y como los correos del watchdog llegan, ESA es la buena.
  ⚠️ **Trampa:** si alguien borra el bloque de abajo "porque está repetido", las alertas mueren EN
  SILENCIO. Acción: borrar las líneas **14-17** (las tapadas), conservar 28-31, Save + Deploy.
- **`EMAIL_CLINIC=contactoodontotec@gmail.com`** (línea 19): **no lo lee ningún código** (verificado
  por grep). Basura vieja, quitar.
- ℹ️ `WATCHDOG_ALERT_EMAIL` con los dos correos quedó puesto en `odontotec` (línea 32) el 2026-08-20.
  Primero se puso por error en `dentidesk-daemon` — si sigue ahí, quitarla (no hace nada: el
  watchdog corre en `odontotec`).

### ⚠️ Promesa falsa que sigue en el prompt (decisión del usuario pendiente)

GUION A y A2 dicen *"Le recordaremos su cita por teléfono, WhatsApp y por su email"*, pero los
recordatorios (Fase 3) están **PAUSADOS** — hoy no sale ninguno. Es texto del cliente; no se tocó.
Decidir: quitarlo hasta que Fase 3 esté viva, o dejarlo.

---

## 🟢 (previo) ARRANCAR AQUÍ — Sesión 2026-08-19 CERRADA: FASE 2 VIVA + incidente Farmaol resuelto

**En una línea:** Carla ya contesta por el número oficial `849-410-7913`, y en el camino se descubrió
y arregló que **crear el inbox de Chatwoot había dejado mudo a un bot de producción ajeno**.

| Qué | Estado |
|---|---|
| `849-410-7913` → Chatwoot → Carla contesta | ✅ **verificado en vivo** |
| `809-343-1368` → Farmaol/Vocero | ✅ **restaurado** (estuvo caído por nosotros, ver 🚨) |
| Override de webhook a nivel de NÚMERO (no de WABA) | ✅ aplicado y confirmado por API |
| Guard del autocompletar de `#rut` (commit `0c61ce9`, pusheado) | ⏳ **falta Deploy de `dentidesk-daemon`** |
| E2E de agendar/reagendar real | ❌ **BLOQUEADO**: duplicados de cédula corta en Dentidesk |

### ▶️ LO PRIMERO AL RETOMAR

1. **¿Se desplegó `dentidesk-daemon`?** El guard `0c61ce9` está pusheado pero puede que no desplegado.
   Verificar sin depender del hash (`/health` dice `commit: desconocido`): en la Terminal del
   contenedor, `ls -la /app/integrations/dentidesk_playwright.py` → debe dar **39142 bytes**.
   Menos = el deploy no tomó.
2. **¿El cliente borró los registros de cédula incompleta?** Sin eso el e2e de agendar sigue chocando
   (ver el bloque 🩹). El usuario quedó en pedírselo el 2026-08-19.
3. **Si ambas están OK → correr el e2e de agendar** por WhatsApp al 849 con cédula de 11 dígitos, y
   verificar que la cita cae en la ficha correcta y no duplica.

### ⚠️ DOS AVISOS AL CLIENTE QUE SIGUEN PENDIENTES (dependen de personas, no de código)

- **Nadie debe registrar el `849-410-7913` en la app de WhatsApp del celular.** Un número vive en la
  app O en la Cloud API, nunca en ambas: si alguien lo registra, Meta lo da de baja de la Cloud API y
  **se cae toda la integración**. Las conversaciones se atienden desde **Chatwoot**.
- **Que borre los pacientes de cédula incompleta** (`carlaodontotec` no tiene permiso de borrar).

### 🔐 Credenciales expuestas hoy — regenerar

- **Verify Token** del inbox `Cliente Dental - 849` (quedó escrito en un chat).
- **Token permanente de "Karla BOT"** (salió parcial en una captura de pantalla).
- `CHATWOOT_API_TOKEN` del `.env` **local** da **401** (prod está bien — Carla funciona). Higiene.
- `WEBHOOK_SECRET` sigue **MISSING** en `odontotec` → el webhook acepta cualquier origen. Ahora que
  hay número oficial del cliente en producción, esto pesa más que antes.

### Datos del número nuevo (Fase 2)

| Dato | Valor |
|---|---|
| Número | `+18494107913` |
| Phone Number ID | `1242049598998643` |
| WABA ID | `935606856224223` ("Distribuidora Pharma-OL") |
| App ID | `919228577909715` ("Pharma-OL Integration") |
| Token permanente | System User "Karla BOT" (no está en el repo) |
| Inbox Chatwoot | `Cliente Dental - 849` (cuenta 3) |
| Webhook URL | `https://testagente1-chatwoot.oyfspa.easypanel.host/webhooks/whatsapp/+18494107913` |
| Verify Token | NO se guarda acá (credencial). Está en Chatwoot → Settings → Inboxes → `Cliente Dental - 849`. ⚠️ quedó escrito en un chat — regenerar tras el e2e |

### 🚨 EL 849 COMPARTE APP Y WABA CON UN BOT EN PRODUCCIÓN QUE NO ES NUESTRO

**Leer esto ANTES de tocar nada en Meta.** La WABA `935606856224223` tiene DOS números:

| Número | Phone Number ID | Quién lo atiende |
|---|---|---|
| `+1 809-343-1368` | `1240470792481915` | **bot Farmaol EN PRODUCCIÓN**, CRM propio del usuario ("Vocero") → `https://farmaol.sapiensbots.com/webhook`. Calidad High. **NO ROMPER.** |
| `+1 849-410-7913` | `1242049598998643` | Carla (este repo) → Chatwoot. Estado "In Review" al 2026-08-19. |

> 💥 **YA PASÓ UNA VEZ (2026-08-19).** Crear el inbox de Chatwoot para el 849 dejó **mudo al 809 de
> Farmaol** durante horas: el Embedded Signup de Chatwoot escribe el `override_callback_uri`
> **a nivel de WABA**, y como ningún número tenía override propio, se aplicó a los dos.
> Fallo SILENCIOSO — sin error, sin alerta, invisible desde Chatwoot.
> **Se arregló con `POST /935606856224223/subscribed_apps` con CUERPO VACÍO** (🚫 nunca `DELETE`,
> eso desuscribe la app entera). Detalle: [[incidente-chatwoot-embedded-signup-tumbo-farmaol-2026-08-19]].
> **Regla:** tras CUALQUIER cambio de webhook, probar por WhatsApp **todos** los números de la WABA.

⚠️ **NUNCA cambiar el Callback URL en `App → WhatsApp → Configuration → Webhook → Edit`.** Ese campo es
**a nivel de APP**: apunta hoy a `farmaol.sapiensbots.com/webhook` y sirve a los DOS números. Cambiarlo
por la URL de Chatwoot deja **mudo al 809-343-1368** (sus mensajes se irían a Chatwoot, que no sabe
qué hacer con ellos). Esa instrucción estaba escrita mal en este archivo hasta el 2026-08-19 y casi se
ejecuta — el usuario la frenó a tiempo.

**La forma correcta es un `webhook_configuration` override A NIVEL DE NÚMERO.** Precedencia de Meta:
`número → WABA → app`. Poniéndole override solo al 849, el 809 sigue heredando el webhook del App sin
que se toque un solo campo de su configuración.

```bash
# LEER primero (solo lectura, no cambia nada)
curl -s 'https://graph.facebook.com/v25.0/1242049598998643?fields=webhook_configuration' \
  -H 'Authorization: Bearer <TOKEN_KARLA_BOT>'

# ESCRIBIR el override (solo afecta al 849)
curl -X POST 'https://graph.facebook.com/v25.0/1242049598998643' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <TOKEN_KARLA_BOT>' \
  -d '{
    "webhook_configuration": {
      "override_callback_uri": "https://testagente1-chatwoot.oyfspa.easypanel.host/webhooks/whatsapp/+18494107913",
      "verify_token": "<VERIFY_TOKEN_DEL_INBOX_849>"
    }
  }'

# REVERTIR si algo sale mal (vuelve a heredar del App)
#   mismo POST con "override_callback_uri": ""
```

- El **token** sale de Meta → Business Settings → System users → "Karla BOT". No está en el repo ni en `.env`.
- **`messages` NO hay que suscribirlo de nuevo:** el override cambia el DESTINO, no la SUSCRIPCIÓN. Ya
  está suscrito a nivel App (por eso el 809 funciona).
- Si el e2e falla, revisar que el 849 haya salido de **"In Review"** antes de culpar al webhook.
- Doc: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/override/

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

1. ✅ **HECHO 2026-08-19** — override de webhook puesto **en el número 849** (`phone_number` →
   Chatwoot). Verificado por API: el 849 muestra `phone_number` + `application`; el 809 solo
   `application` → farmaol. Farmaol probado por WhatsApp y **contestando**.
2. ✅ **E2E BÁSICO OK 2026-08-19** — WhatsApp al 849 → llega a Chatwoot (inbox `Cliente Dental - 849`)
   → **Carla contesta sola**. Cadena completa verificada en vivo. El 809 de Farmaol probado en
   paralelo y contestando. **PENDIENTE del e2e: agendar/reagendar real** contra Dentidesk.
   - ℹ️ **Rareza esperada, NO es bug:** en el **celular** WhatsApp puede mostrar el chat en modo
     lectura si el 849 no está en los contactos (protección anti-spam del app). Por escritorio va
     normal. Se resuelve guardando el contacto. **A los pacientes no les pasa** — ellos entran por
     el QR / `wa.me/18494107913`, que abre el chat con el campo de escritura habilitado.
   - Recordatorio: **probar SIEMPRE los dos números** tras cualquier cambio de webhook (bloque 🚨).
3. **Solo tras el 100%:** apagar el número viejo de Carla `+1 809-977-9329` (WABA "Promolab
   Laboratorio Promocional", ID `1414517500410070` — otra WABA, no se toca hasta el final). Nunca antes.
4. **Avisarle al cliente:** el 849 ya NO abre en la app de WhatsApp del celular (un número vive en la
   app O en la Cloud API, no en ambas). La ventana para ver las conversaciones es **Chatwoot**. Decirlo
   ANTES de que lo descubran solos y crean que se rompió algo. El QR que muestra Chatwoot es
   `wa.me/18494107913` — para que el PACIENTE lo escanee; no hay emparejamiento por QR en Cloud API.

## 🩹 (2026-08-19) Guard del autocompletar de `#rut` — el 11-dígitos NO cubría este caso

**Descubierto durante el e2e del 849:** el guardrail de 11 dígitos vive en el AGENTE y valida lo que
Carla *escribe*. No cubre lo que el autocompletar de Dentidesk *hace después*.

**Mecanismo (leído en código + confirmado por el historial del 22-jul):** `_type_rut_recognize`
teclea la cédula y presiona Enter para "cerrar la selección". Pero si el dropdown dejó resaltada la
fila de un registro viejo de **cédula corta** (`0310` matchea por prefijo a `03102778092`), el Enter
**la SELECCIONA**: Dentidesk sobrescribe `#rut` con la cédula corta y pone `#id_paciente` en la ficha
EQUIVOCADA. La función devolvía `True` ("paciente reconocido") porque solo miraba `#id_paciente != 0`,
**nunca DE QUIÉN** → la cita se creaba en la ficha de otra persona, sin error visible.

**Fix:** tras el Enter se relee `#rut`; si los dígitos no son los tecleados, se lanza
`CedulaAutocompletarHijack` **antes** de tocar `#btn_guardar_cita`. Comparación solo por dígitos
(Dentidesk reformatea el RUT). Tests: 4 casos (hijack, reformateo, paciente nuevo, placeholder `1-9`).
**Suite 128 pasan** (los 6+2 de respx/py3.14 siguen preexistentes).

⚠️ **Va en el contenedor `dentidesk-daemon`** — la creación de cita corre ahí, no en `odontotec`.

**Lo que el guard NO hace:** no limpia la base ni elige la fila correcta. Solo evita agendar mal.
- **Pendiente del cliente:** borrar los registros de cédula incompleta (`carlaodontotec` no tiene
  permiso de borrar). El usuario quedó en pedírselo el 2026-08-19.
- **Mejora futura (necesita ver el DOM por VNC):** en vez de Enter a ciegas, **clickear la fila cuya
  cédula coincide EXACTA** con los 11 dígitos → enlazaría siempre la ficha correcta aunque existan
  duplicados. No se construyó: falta el selector del dropdown.

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
