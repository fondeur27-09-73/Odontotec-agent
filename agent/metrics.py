"""
Contadores de anomalías para el watchdog.

run_agent corre vía asyncio.to_thread (main.py), así que dos conversaciones incrementan en
paralelo desde hilos distintos -> Lock. El watchdog los lee y resetea cada ciclo.
"""

import threading

_lock = threading.Lock()
_counters = {
    "escalate_blocked": 0,
    "iterations_exhausted": 0,
    "agent_failed": 0,
}


def increment(name: str) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + 1


def snapshot_and_reset() -> dict:
    """Lee los contadores y los deja en 0. El watchdog compara contra su umbral por ciclo."""
    with _lock:
        snap = dict(_counters)
        _counters.update({k: 0 for k in _counters})
        return snap


if __name__ == "__main__":
    increment("escalate_blocked")
    increment("escalate_blocked")
    snap = snapshot_and_reset()
    assert snap["escalate_blocked"] == 2, snap
    assert snapshot_and_reset()["escalate_blocked"] == 0, "reset falló"
    print("metrics OK")
