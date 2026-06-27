# Dentidesk — Runbook del campo de simulación

Estado: **todo integrado, escrituras BLOQUEADAS**. Este documento es el checklist para el día/hora
de la simulación autorizada (simular citas, agendar, mover). Nada de esto se ejecuta sin aprobación.

## Qué ya funciona (lectura, probado contra producción)
- `integrations/dentidesk.py`: `login()`, `get_agenda_day()`, `get_agenda_status()`,
  `find_by_cedula()`, `find_by_phone()`.
- Tool de Carla `buscar_cita_dentidesk` (lectura) — encuentra cita por cédula/teléfono, sin exponer PII.

## Qué está listo pero bloqueado (escritura)
- API: `dentidesk.update_status()` / `confirm_appointment()` → cambia IdStatus (confirmar cita).
- UI Playwright: `dentidesk_playwright.create_appointment()` / `move_appointment()` → crear y mover
  citas (la API no puede). **Selectores aún sin capturar.**
- Tools de Carla: `agendar_cita_dentidesk`, `reagendar_cita_dentidesk`, `confirmar_cita_dentidesk`.
- Candado: todas exigen `DENTIDESK_ALLOW_WRITES=1`. Sin esa env lanzan error antes de tocar nada.

## Pasos el día de la simulación

1. **Capturar selectores Playwright** (una vez):
   ```powershell
   & ".venv\Scripts\Activate.ps1"
   python scripts/dentidesk_codegen.py
   ```
   Iniciar sesión a mano, recorrer "nueva cita" y "reagendar". Copiar los `page.fill/page.click`
   que imprime codegen a los bloques `TODO(codegen)` de `integrations/dentidesk_playwright.py`.
   (Durante la captura, NO pulsar guardar.)

2. **Configurar credenciales web** (login de UI, cuenta staff — distinta de la API):
   En `.env`: `DENTIDESK_WEB_USER=h.geronimo`, `DENTIDESK_WEB_PASS=<clave web>`.

3. **Activar escrituras** (solo en el entorno de simulación, con aprobación):
   `DENTIDESK_ALLOW_WRITES=1`.

4. **Probar en orden, con una cita de prueba**:
   - `confirmar_cita_dentidesk` (API, cambia status) sobre un IdAgenda de prueba.
   - `agendar_cita_dentidesk` (Playwright) crea una cita de prueba.
   - `reagendar_cita_dentidesk` (Playwright) la mueve.
   - Verificar cada resultado leyendo con `get_agenda_status` / `get_agenda_day`.

5. **Al terminar la simulación**: volver a poner `DENTIDESK_ALLOW_WRITES` en `0`/quitarla si no se
   quiere que Carla escriba en producción todavía.

## Notas
- Token de la API = un solo uso; cada llamada hace su propio login (ya manejado).
- `updateAgenda` solo cambia status, NO fecha/hora; mover cita = siempre Playwright.
- `update_status` incluye `IdLocation` por patrón (getAgendaStatus lo exige) — verificar en la
  primera prueba real que updateAgenda lo acepta.
- Airtable quedó retirado del runtime; `integrations/airtable.py` es código muerto (borrar con
  `git rm` cuando se quiera).
