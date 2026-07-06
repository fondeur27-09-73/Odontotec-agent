# Recordatorios 48h/24h → citas confirmadas (Carla / Odontotec)

**Fecha:** 2026-07-06
**Estado:** Diseño aprobado, en implementación.

## Contexto y objetivo

Meta principal del negocio: **maximizar citas confirmadas 1-2 días antes**. Hoy Carla
(agente WhatsApp sobre Chatwoot) responde conversaciones y agenda en Dentidesk, pero NO
manda recordatorios. El recordatorio nocturno viejo se borró (commit `a0a7998`, solo hacía
`print` contra Cal.com muerto). Este spec reconstruye el sistema de recordatorios contra
Dentidesk + plantilla WhatsApp real.

Flujo objetivo:

```
[Scheduler diario @ REMINDER_HOUR]
  → get_agenda_day(hoy+2d) y get_agenda_day(hoy+1d)   [API Dentidesk, lectura]
  → filtra citas NO confirmadas (IdStatus != 1211)
  → por cada cita: envía PLANTILLA WhatsApp (Cloud API vía Chatwoot)
      variables: nombre / fecha / motivo / hora EXACTA / sucursal
  → registra en SQLite (idempotencia + mapeo telefono→IdAgenda)

[Paciente toca botón en WhatsApp: CONFIRMAR / CANCELAR / REAGENDAR]
  → llega a /webhook como mensaje entrante
  → INTERCEPTOR (antes del LLM), determinista y rápido:
      CONFIRMAR → dentidesk.confirm_appointment(IdAgenda)   [status 1211, API, instantáneo]
      CANCELAR  → dentidesk.update_status(IdAgenda, cancelado_email 1214)  [API]
      REAGENDAR → flujo Carla existente (pide fecha) → Playwright
  → responde confirmación al paciente
```

## Componentes

Reusa lo que ya existe:
- `integrations/dentidesk.py`: `get_agenda_day`, `find_by_phone`, `confirm_appointment`,
  `update_status`, dict `STATUS`. Confirmar/cancelar es **API pura, sin Playwright** → instantáneo.
- `integrations/chatwoot.py`: `send_message`, headers/URL helpers.
- `integrations/db.py`: patrón `_conn()` + `init_db()` (SQLite en `/data`).
- `main.py`: webhook `message_created`, `_process_message`, lock por conversación.

Nuevo a construir:

### 1. SQLite — tabla `recordatorios_enviados`
En `integrations/db.py`. Columnas: `id_agenda TEXT PRIMARY KEY`, `phone TEXT`,
`conversation_id INTEGER`, `fecha_cita TEXT`, `enviado_48h INTEGER DEFAULT 0`,
`enviado_24h INTEGER DEFAULT 0`, `respuesta TEXT`, `updated_at TEXT`.
Funciones: `marcar_recordatorio(id_agenda, tramo, phone, conv_id, fecha)`,
`ya_enviado(id_agenda, tramo)`, `buscar_cita_por_phone(phone)` (para mapear la respuesta del
botón a la cita sin re-consultar Dentidesk). Idempotencia: no reenviar el mismo tramo.

### 2. `integrations/chatwoot.py` — `send_template()`
WhatsApp Cloud API oficial vía Chatwoot. Pasos:
1. Buscar/crear contacto por teléfono (`POST /contacts/search` o `/contacts`).
2. Buscar/crear conversación en el inbox WhatsApp (`inbox_id`, `source_id`=teléfono).
3. Enviar plantilla: `POST /conversations/{id}/messages` con `template_params`
   (`name`, `category: UTILITY`, `language: es`, `processed_params: {"1":..,"5":..}`).
Devuelve `conversation_id` (se guarda en SQLite para mapear la respuesta).
Env nuevos: `CHATWOOT_INBOX_ID`, `WA_TEMPLATE_NAME`.
Hora = hora EXACTA del paciente (`time` de `getAgendaDay`), no el rango de clínica
(obs. #1 de `docs/carla-plantilla-confirmacion.md`).

### 3. `main.py` — interceptor de botones
En el webhook, ANTES de `_process_message`/LLM: si el contenido entrante es un payload de
botón (`CONFIRMAR`/`CANCELAR`/`REAGENDAR`, normalizado mayúsculas/sin tildes), resolver
`IdAgenda` vía `db.buscar_cita_por_phone(phone)` (fallback `dentidesk.find_by_phone`), ejecutar
la acción determinista y responder — sin invocar el modelo. `REAGENDAR` sí entra al flujo Carla.
Respeta `is_bot_off`.

### 4. `scheduler/reminders.py` — job diario
APScheduler (`interval`/`cron` diario @ `REMINDER_HOUR`, `TIMEZONE` ya en env). Recorre
sucursales (`LOCATIONS`), citas de `hoy+1d` y `hoy+2d`, filtra no confirmadas, respeta
idempotencia SQLite, llama `send_template`. Registrado en el `lifespan` de `main.py`
(el scheduler arranca con el proceso, como el init_db).

### Track B — Velocidad (secundaria)
- Confirmar/cancelar ya instantáneo (API). La mayoría de recordatorios terminan en CONFIRMAR.
- Playwright (solo agendar/reagendar): navegador persistente + login reusado entre operaciones,
  y esperas por condición (`wait_for_function` sobre nº de opciones de doctor cargadas) en vez de
  `wait_for_timeout(2000/400/150)`. CUIDADO: esos sleeps existen por races reales verificados en
  vivo (carga async del modal resetea `#dentista_cita`) → cambiar con verificación explícita, no a ciegas.

## Manejo de errores
- Envío de plantilla falla (Meta/Chatwoot) → log + NO marcar enviado (reintenta próximo ciclo).
- `get_agenda_day` falla → log, saltar esa sucursal/tramo, no romper el job.
- Botón sin cita mapeable → responder pidiendo datos / escalar, no reventar.
- Escrituras Dentidesk siguen bajo candado `DENTIDESK_ALLOW_WRITES` (en prod =1, ya asignado).

## Dependencia externa paralela (no bloquea construir)
Someter la plantilla a Meta para aprobación (categoría UTILITY, es). Sin aprobación no se puede
enviar fuera de la ventana 24h, pero todo el código se construye y prueba en paralelo.

## Verificación
1. Unit: tabla SQLite (marcar/idempotencia/buscar), interceptor de botones (mapea y llama la
   acción correcta), `send_template` (payload correcto, mockeando httpx).
2. Integración con claves reales: forzar el job contra un día con cita de prueba → confirmar que
   llega la plantilla al WhatsApp → tocar CONFIRMAR → verificar `IdStatus=1211` en Dentidesk vía
   `get_agenda_status`. Cronometrar (confirmar debe ser <2s, API pura).
