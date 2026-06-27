"""
Dentidesk API probe — SOLO LECTURA.
Descubre el formato de los endpoints sin escribir nada en el CRM.

Endpoints permitidos (lectura):
  - users/authentication.php  
  - agenda/getAgendaDay.php    (leer agenda del dia)
  - agenda/getAgendaStatus.php (leer estado de una cita)

PROHIBIDO: agenda/updateAgenda.php (escribe IdStatus) — este script NUNCA lo llama.

Uso:
  python scripts/dentidesk_probe.py auth        # prueba login (GET y POST, varios nombres de param)
  python scripts/dentidesk_probe.py day [YYYY-MM-DD]   # leer agenda del dia (default: hoy)

Lee credenciales de .env: DENTIDESK_BASE, DENTIDESK_USER, DENTIDESK_PASS, DENTIDESK_LOCATION
La clave NUNCA se imprime.
"""
import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def call(method, url, params, mode="form", headers=None):
    """HTTP call. mode: form | json | none. Devuelve (status, body_text, headers)."""
    if method == "GET":
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(full, method="GET")
    elif mode == "json":
        req = urllib.request.Request(url, data=json.dumps(params).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
    elif mode == "none":
        req = urllib.request.Request(url, method="POST")
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:
        return -1, f"ERROR: {e}", {}


def mask(s):
    return "***" if s else "(vacio)"


def probe_auth(env):
    base = env["DENTIDESK_BASE"].rstrip("/")
    url = f"{base}/users/authentication.php"
    user = env.get("DENTIDESK_USER", "")
    pwd = env.get("DENTIDESK_PASS", "")
    print(f"Login URL: {url}")
    print(f"User: {user or '(vacio)'}  Pass: {mask(pwd)}\n")
    if not user or not pwd:
        print("FALTA DENTIDESK_USER / DENTIDESK_PASS en .env")
        return
    # Primero: GET sin params, a ver si el endpoint dice que espera
    s, b, h = call("GET", url, {})
    print(f"[GET sin params] -> {s} | {b[:300].replace(chr(10),' ')}")
    print(f"   headers: { {k: h.get(k) for k in ('Content-Type','Server') if k in h} }\n")
    # Combos comunes de nombres de param
    param_sets = [
        {"user": user, "password": pwd},
        {"email": user, "password": pwd},
        {"usuario": user, "clave": pwd},
        {"username": user, "password": pwd},
        {"login": user, "pass": pwd},
    ]
    for mode in ("form", "json"):
        for method in ("POST", "GET"):
            if method == "GET" and mode == "json":
                continue
            for ps in param_sets:
                status, body, _ = call(method, url, ps, mode=mode)
                keys = ",".join(ps.keys())
                snippet = body[:300].replace("\n", " ")
                print(f"[{method}/{mode} {keys}] -> {status} | {snippet}")


def login(env):
    """Login (lectura). Devuelve token JWT o None. No imprime el token completo."""
    base = env["DENTIDESK_BASE"].rstrip("/")
    url = f"{base}/users/authentication.php"
    creds = {"email": env.get("DENTIDESK_USER", ""), "password": env.get("DENTIDESK_PASS", "")}
    status, body, _ = call("POST", url, creds, mode="json")
    if status != 200:
        print(f"Login fallo -> {status} | {body[:200]}")
        return None
    try:
        tok = json.loads(body).get("token")
    except Exception:
        tok = None
    print(f"Login OK ({status}). token: {tok[:18] + '...' if tok else 'NO TOKEN'}")
    return tok


def probe_day(env, day):
    base = env["DENTIDESK_BASE"].rstrip("/")
    url = f"{base}/agenda/getAgendaDay.php"
    loc = env.get("DENTIDESK_LOCATION", "214")
    tok = login(env)
    if not tok:
        return
    # CONTRATO DESCUBIERTO (case-sensitive, token va en el BODY como "Token"):
    #   POST agenda/getAgendaDay.php  JSON {"Token": <jwt>, "IdLocation": "214", "Date": "YYYY-MM-DD"}
    # El token es de UN SOLO USO -> hay que loguear antes de cada llamada.
    params = {"Token": tok, "IdLocation": loc, "Date": day}
    status, body, _ = call("POST", url, params, mode="json")
    print(f"\ngetAgendaDay [Token,IdLocation,Date] -> {status}")
    try:
        data = json.loads(body).get("data", [])
        print(f"Citas en agenda: {len(data)}")
        if data:
            sample = dict(data[0])
            PII = ("PatientName", "Patient", "Phone", "Phone2", "Email",
                   "PatientEmail", "Rut", "Cedula", "PatientDocument")
            for k in PII:
                if k in sample:
                    sample[k] = "<REDACTADO PII>"
            print("Campos de una cita (PII redactada):")
            print(json.dumps(sample, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"(no JSON) {body[:300]}  | {e}")


if __name__ == "__main__":
    env = load_env()
    step = sys.argv[1] if len(sys.argv) > 1 else "auth"
    if step == "auth":
        probe_auth(env)
    elif step == "day":
        d = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
        probe_day(env, d)
    else:
        print("Uso: python scripts/dentidesk_probe.py [auth|day] [YYYY-MM-DD]")
