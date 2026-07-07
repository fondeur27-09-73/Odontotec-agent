# Multi-stage. Producción (default) = agente conversacional, imagen lean SIN navegador.
# Las escrituras a Dentidesk (Playwright) NUNCA corren en prod: el candado
# _require_writes_enabled() en integrations/dentidesk_playwright.py exige DENTIDESK_ALLOW_WRITES=1
# y aborta ANTES de importar/lanzar chromium. Por eso prod no necesita el binario del navegador.

FROM python:3.11-slim AS base
WORKDIR /app
RUN adduser --disabled-password --gecos "" appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R appuser /app

# --- Simulación: SOLO para el campo autorizado (DENTIDESK_ALLOW_WRITES=1). ---
# Trae Chrome (canal real, no el Chromium bundled) + libs del SO para Playwright (~400MB extra).
# NO usar en producción.
#   docker build --target sim -t odontotec-sim .
# PLAYWRIGHT_BROWSERS_PATH fija una ruta compartida y legible para que appuser (no-root)
# encuentre el navegador instalado como root.
# channel="chrome": create_appointment/move_appointment lanzan con channel="chrome" (bug 2026-07-07,
# ver dentidesk-playwright-bugs-2026-07-07) — el Chromium bundled dispara reCAPTCHA en el login real.
# Por eso aqui se instala "chrome" (Chrome estable real), no "chromium".
FROM base AS sim
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chrome && chmod -R a+rx /ms-playwright
USER appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Daemon: navegador Dentidesk persistente para escrituras de producción real. ---
# Corre Chrome real con pantalla virtual (Xvfb) + VNC/noVNC para resolver el login/captcha UNA
# vez a mano; después el navegador se queda abierto y logueado indefinidamente (la sesión de
# Dentidesk no expira — regla del cliente, ver memoria dentidesk-playwright-bugs-2026-07-07).
# Expone SOLO una API HTTP propia (puerto 8100, ver scripts/dentidesk_daemon_api.py) protegida
# por DENTIDESK_DAEMON_SECRET — el CDP del navegador (9222) NUNCA sale de 127.0.0.1 dentro de
# este contenedor, porque ese protocolo no tiene autenticación.
# El servicio "odontotec" (agente) le habla a esto por DENTIDESK_DAEMON_URL, sin necesitar
# Playwright instalado — ver dentidesk-pendiente-daemon-produccion para el plan del servicio.
#   docker build --target daemon -t odontotec-daemon .
FROM base AS daemon
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chrome && chmod -R a+rx /ms-playwright
RUN chmod +x scripts/daemon_entrypoint.sh
USER appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# 9222 (CDP) NO se publica -- solo 6080 (VNC web, temporal, con contraseña) y 8100 (API interna).
EXPOSE 6080 8100
CMD ["scripts/daemon_entrypoint.sh"]

# --- Producción (default): última etapa => build sin --target la construye. ---
FROM base AS prod
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
