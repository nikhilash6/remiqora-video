"""cloud.json keeps tokens sealed (DPAPI on Windows) and still reads an old plain one. No server, no network."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.cloud import state  # noqa: E402

ok = True


def check(c: bool, msg: str) -> None:
    global ok
    print(("ok   " if c else "FAIL ") + msg)
    ok = ok and c


state.FILE = Path(tempfile.mkdtemp()) / "cloud.json"
d = {**state.DEFAULT, "mode": "server", "server": {"url": "https://x", "token": "s3cret-own"},
     "pod": {"url": "https://p", "token": "s3cret-pod"}}
state.save(d)
raw = state.FILE.read_text(encoding="utf-8")
check("s3cret" not in raw or sys.platform != "win32", "no plain token in the file (Windows)")
check(d["server"]["token"] == "s3cret-own", "save keeps the caller's dict plain")
l = state.load()
check(l["server"]["token"] == "s3cret-own" and l["pod"]["token"] == "s3cret-pod", "round trip")
check(state.public(l)["server"]["token_set"] is True, "public still sees a token is set")

old = {"mode": "server", "server": {"url": "https://x", "token": "plainold"}, "pod": None}
state.FILE.write_text(json.dumps(old), encoding="utf-8")
l = state.load()
check(l["server"]["token"] == "plainold", "an old plain token is read")
state.save(l)
check(state.load()["server"]["token"] == "plainold", "migrated by the next save")
if sys.platform == "win32":
    check("plainold" not in state.FILE.read_text(encoding="utf-8"), "sealed after the migration")
state.save({**state.DEFAULT, "server": {"url": "", "token": ""}})
check(state.load()["server"]["token"] == "", "empty token stays empty")
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
