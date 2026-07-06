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
# Trae Chromium + libs del SO para Playwright (~400MB extra). NO usar en producción.
#   docker build --target sim -t odontotec-sim .
# PLAYWRIGHT_BROWSERS_PATH fija una ruta compartida y legible para que appuser (no-root)
# encuentre el navegador instalado como root.
FROM base AS sim
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && chmod -R a+rx /ms-playwright
USER appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Producción (default): última etapa => build sin --target la construye. ---
FROM base AS prod
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
