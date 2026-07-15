"""Smoke: save/load de cookies del daemon hace roundtrip sin navegador real."""
import os
import tempfile

import dentidesk_daemon as d


class FakeCtx:
    def __init__(self, cookies=None):
        self._c = cookies or []

    def cookies(self):
        return self._c

    def add_cookies(self, c):
        self._c = c


def test_roundtrip(monkeypatched_path):
    d.COOKIES = monkeypatched_path
    src = FakeCtx([{"name": "PHPSESSID", "value": "abc", "domain": ".dentidesk.com",
                    "path": "/", "expires": -1}])
    d._save_cookies(src)
    dst = FakeCtx()
    d._load_cookies(dst)
    assert dst.cookies() == src.cookies(), "cookies no sobrevivieron el roundtrip"


def test_missing_file_is_noop(monkeypatched_path):
    d.COOKIES = monkeypatched_path + ".nope"
    dst = FakeCtx([{"name": "x"}])
    d._load_cookies(dst)  # archivo no existe -> no revienta, no toca ctx
    assert dst.cookies() == [{"name": "x"}]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cookies.json")
        test_roundtrip(path)
        test_missing_file_is_noop(path)
    print("OK")
