# Dentidesk (Odonto-Tec) — qué es, para qué sirve cada pieza, y reglas

Este documento explica QUÉ implica que Carla use Dentidesk como CRM: qué hace la API, qué hace
Playwright, por qué existen ambos, y las reglas de seguridad que NUNCA se rompen. Es la referencia
de arquitectura; el checklist operativo del día de simulación está en `docs/dentidesk-simulacion.md`.

## Por qué dos caminos (API + Playwright) en vez de uno solo

Dentidesk expone una API oficial muy limitada: **solo 4 endpoints**, y de esos, **3 son de
lectura**. No existe API pública para crear cliente, crear cita, ni mover (reagendar) una cita.
Por eso el sistema se divide en dos capas con responsabilidades distintas:

| Capa | Hace | No hace | Por qué |
|---|---|---|---|
| **API** (`integrations/dentidesk.py`) | Leer agenda, leer status, cambiar status (confirmar/cancelar) | Crear cliente, crear cita, mover cita | Son los únicos endpoints que el proveedor documenta y expone |
| **Playwright** (`integrations/dentidesk_playwright.py`) | Crear cita, mover cita (clickeando la UI web real) | Nada que la API ya cubra | La API no tiene endpoint — el único "botón" que existe es el de la interfaz web |

**Regla:** si una acción se puede hacer por API, se hace por API (más rápido, sin browser, sin
reCAPTCHA, sin selectores que se rompan con un rediseño de UI). Playwright es el último recurso,
solo para lo que la API no ofrece.

## Qué resuelve la API en la práctica

- `login()` — cuenta de servicio del proveedor (`produccion@cero.ai`, ticket CUS-1701), token JWT
  de **un solo uso** (login antes de cada llamada).
- `get_agenda_day(fecha, sucursal)` — devuelve TODAS las citas del día: nombre, cédula, teléfono,
  status, profesional. Con esto se puede indexar cédula→paciente y teléfono(WhatsApp)→paciente sin
  abrir un navegador.
- `get_agenda_status(id_agenda, sucursal)` — status puntual de una cita.
- `update_status(id_agenda, id_status, sucursal)` (= `confirm_appointment`) — cambia el status
  (ej. confirmar). **Solo status, nunca fecha/hora.** Codeado, bajo candado, **nunca ejecutado en
  vivo todavía** (pendiente de la simulación).

Esto es lo que usa la tool `buscar_cita_dentidesk` de Carla: búsqueda instantánea, sin tocar la UI,
sin PII expuesta de más, sin riesgo de click accidental.

## Qué resuelve Playwright (y por qué es más delicado)

`create_appointment()` y `move_appointment()` automatizan la UI web (`app.dentidesk.com`) porque
no hay otro camino. Implicaciones de hacerlo así:

- **reCAPTCHA en el login web.** Por eso el modelo de producción es un **browser persistente
  único**, logueado una vez por un humano, reutilizado siempre — nunca relanzar browser nuevo por
  cada acción (eso pegaría con el captcha en cada llamada).
- **Selectores reales de la UI**, capturados navegando en vivo (no inventados): `#nombre_norden`/
  `#nombre` (duplicado, hay que llenar ambos), `#rut`, `#dentista_cita`, `#diacita/#mescita/#aniocita`,
  `#horac/#minutos`, `#btn_guardar_cita`. Cualquier rediseño de Dentidesk puede romperlos.
- **Apertura de "nueva cita"** vía `window.open_modal_cita(...)` (función JS global), NO clickeando
  la celda del calendario — clickear la celda dispara un pre-check que puede tirar "Horario no
  disponible" sin abrir nada.
- **Navegación a una fecha arbitraria** (para reagendar) vía el minicalendario jQuery UI Datepicker
  (`#datepicker`, `.ui-datepicker-prev/next` + click en el día) — **NO** `fullCalendar('gotoDate',...)`,
  que se probó roto (manda a 1-Enero-1970). Bug real encontrado y corregido 2026-06-28.
- **Bloqueos por doctor** ("No agendar Dra. X") son notas reales en BD, visibles como bloques de
  color en el calendario — el servidor los valida al guardar (`cupo_disp=0`), no hay endpoint para
  leerlos por API todavía.
- **Botón "Cerrar" tiene un duplicado invisible en el DOM** — `click_text`/`Escape` fallan por eso;
  hay que filtrar por elemento visible (`offsetParent !== null`) y clickear ese.

## Flujo de negocio (cómo está organizado Dentidesk, y por qué seguirlo en ese orden)

1. **Pacientes primero.** Si es la primera vez que el paciente contacta (o no se sabe si ya es
   cliente), se busca/crea en `pacientes.php` (`#buscar_paciente*`, botón `#btn_paciente`). Esto
   evita duplicar fichas.
2. **Agenda organizada por profesional**, no por especialidad. El selector de doctor (`#dentista_cita`
   en el modal de cita) lista NOMBRES, no especialidades — mapear especialidad→doctor disponible es
   decisión de negocio pendiente, no técnica.
3. **Estados de cita** (panel "Clasificación de Estados de Cita", 9 en total): los 3 visibles
   siempre son 🔴 No confirmado, 🟢 Confirmado, ❌ Hora Cancelada; los otros 6 están bajo "Ver más"
   (Confirmado por e-mail, Cancelado por e-mail, Atendido, Llamada Realizada No Contestada, Recado,
   RECIBIDO).
4. **Al terminar de trabajar una cita** (crear, reagendar, o confirmar vía UI), el código vuelve a
   `home.php` (Agenda/Hoy) — no se queda parado en la última cita abierta. Así el siguiente paciente
   arranca desde un estado conocido.

## Reglas de seguridad (no negociables)

- **`DENTIDESK_ALLOW_WRITES`** — candado global. Sin `=1`, las 3 funciones de escritura (Playwright)
  y `update_status`/`confirm_appointment` (API) lanzan error ANTES de tocar nada.
- **Cero escrituras reales sin aprobación caso por caso.** El único momento autorizado para probar
  escrituras de verdad es el campo de simulación, con fecha/hora de prueba.
- **Nunca pulsar Guardar/Eliminar fuera de simulación**, ni siquiera para "solo verificar" — abrir
  un formulario para leer/confirmar selectores está bien; guardar no.
- **PII** (cédula, teléfono, email de pacientes reales) — evitar volcarla en bulto a logs/texto;
  es dato de la clínica, no un leak, pero se minimiza exposición innecesaria igual.

## Estado (2026-06-28)

Lectura: probada contra producción (149+ citas reales leídas). Escritura: codeada, selectores
capturados y verificados hasta "formulario listo para guardar", **CERO clicks reales en Guardar**.
Próxima prueba real: simulación autorizada (ver `docs/dentidesk-simulacion.md`).
