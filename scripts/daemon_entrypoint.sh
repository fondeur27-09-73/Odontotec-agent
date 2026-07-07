#!/bin/sh
# Entrypoint del contenedor "daemon" (Dockerfile stage `daemon`): levanta una pantalla virtual
# (Xvfb) + VNC para que un humano resuelva el reCAPTCHA UNA vez desde el navegador, despues sirve
# la API interna (dentidesk_daemon_api.py) para que el agente pida crear/mover citas.
# Ver memoria dentidesk-pendiente-daemon-produccion para el plan completo del servicio.
set -e

if [ -z "$DAEMON_VNC_PASSWORD" ]; then
  echo "ERROR: DAEMON_VNC_PASSWORD no esta seteado. El VNC da control remoto completo del" >&2
  echo "navegador logueado de Dentidesk -- por seguridad este contenedor NO arranca sin clave." >&2
  echo "Setea DAEMON_VNC_PASSWORD en las env vars de EasyPanel para este servicio y reinicia." >&2
  exit 1
fi

Xvfb :99 -screen 0 1280x800x24 &
sleep 1

x11vnc -display :99 -forever -shared -rfbport 5900 -passwd "$DAEMON_VNC_PASSWORD" &

websockify --web=/usr/share/novnc 6080 localhost:5900 &

export DISPLAY=:99
export DENTIDESK_DAEMON_API=1

exec python scripts/dentidesk_daemon.py
