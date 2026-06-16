import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/odontotec.db")


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                phone TEXT PRIMARY KEY,
                name TEXT,
                cedula TEXT
            )
        """)


def get_patient(phone: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT name, cedula FROM patients WHERE phone = ?", (phone,)
        ).fetchone()
    if not row:
        return None
    patient = {}
    if row[0]:
        patient["name"] = row[0]
    if row[1]:
        patient["cedula"] = row[1]
    return patient


def save_patient(phone: str, name: str = "", cedula: str = "") -> dict:
    existing = get_patient(phone) or {}
    if name:
        existing["name"] = name
    if cedula:
        existing["cedula"] = cedula
    with _conn() as conn:
        conn.execute(
            "INSERT INTO patients (phone, name, cedula) VALUES (?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET name = excluded.name, cedula = excluded.cedula",
            (phone, existing.get("name", ""), existing.get("cedula", "")),
        )
    return existing
