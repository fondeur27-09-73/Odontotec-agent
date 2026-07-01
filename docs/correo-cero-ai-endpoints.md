# Correo a CERO.AI / Dentidesk — endpoints adicionales

**Para:** contacto@dentidesk.com
**Asunto:** Odonto-Tec — Endpoints adicionales de API (ticket CUS-1701)

---

Estimado equipo DENTIDESK,

Damos seguimiento al ticket CUS-1701 (API de DENTIDESK para Odonto-Tec). Estamos integrando un
asistente que gestiona citas por WhatsApp y necesitamos confirmar lo siguiente sobre la API.

El diccionario que nos compartieron documenta 4 endpoints (authentication, getAgendaDay,
getAgendaStatus, updateAgenda). Con ellos podemos **leer** la agenda y **cambiar el estado** de una
cita, pero nos faltan tres capacidades:

1. **Crear paciente/cliente nuevo.** ¿Existe un endpoint para registrar un paciente (nombre, cédula,
   teléfono, correo)?
2. **Crear una cita nueva.** Detectamos en el servidor `api/agenda/createAgenda.php` (responde, pero
   no está en el diccionario). ¿Está habilitado para Odonto-Tec? ¿Nos pueden enviar su documentación
   (parámetros de request y response)?
3. **Mover/reagendar una cita** (cambiar fecha y hora). `updateAgenda` solo cambia el estado
   (IdStatus), no la fecha/hora. ¿Hay un endpoint para reprogramar?

Si alguno no está disponible vía API, agradecemos confirmarlo para planificar en consecuencia.

Quedamos atentos. Saludos cordiales,
Odonto-Tec
