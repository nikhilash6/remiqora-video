"""What lies on the cloud server's disk, for the Models page in ENGINE=remote: the server's GET /models/files, kept for a
short while, and the last answer kept on disk (data/cloud_files.json) for when the pod is stopped. Never wakes or starts
the server: asking a stopped pod would fail (or, worse, trigger a start) - the page shows the last known list, and
with none yet the sets chosen at deploy, from install/manifest.json. view() never raises."""
from __future__ import annotations

import json
import time
from typing import Optional

from .. import config
from ..engine import remote
from . import state

FILE = config.DATA_DIR / "cloud_files.json"
TTL = 30            # seconds: a fresh answer is reused; a failed ask is not repeated sooner either
ASK_TIMEOUT = 6
_CACHE: dict = {"at": 0.0, "files": None, "source": "none", "url": None, "tried": 0.0}


def _running() -> bool:
    """False only when we know the rented server is not up (don't ask it then). «Свой сервер»: just try."""
    if state.load()["mode"] != "cloud":
        return True
    from . import manager
    return manager.LIVE.get("status") == "running"


def _saved(url: str) -> Optional[dict]:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if d.get("url") == url and isinstance(d.get("files"), dict) else None


def _manifest() -> dict[str, int]:
    """The files of the sets the server was deployed with: 'kind/name' -> bytes (what the disk should hold)."""
    try:
        m = json.loads((config.ROOT / "install" / "manifest.json").read_text(encoding="utf-8"))["sets"]
    except (OSError, ValueError, KeyError):
        return {}
    pod = state.load().get("pod") or {}
    want: list[str] = []
    for s in pod.get("sets", []):
        want += [*(m.get(s, {}).get("needs", [])), s]
    out: dict[str, int] = {}
    for s in dict.fromkeys(want):
        for f in m.get(s, {}).get("files", []):
            out[f"{f['kind']}/{f['path'].rsplit('/', 1)[-1]}"] = int(f.get("bytes", 0))
    return out


def view(force: bool = False) -> dict:
    """{"files": {rel path: bytes}, "source": live | cached | manifest | none, "at": when it was read (epoch) or 0}."""
    url = config.REMOTE_URL
    now = time.time()
    c = _CACHE
    if c["url"] != url:
        c.update(at=0.0, files=None, source="none", url=url, tried=0.0)
    if force or now - c["tried"] > TTL:
        c["tried"] = now
        if _running():
            try:
                got = remote.http_json("GET", "/models/files", timeout=ASK_TIMEOUT)
                files = {f["path"]: int(f["size"]) for f in got["files"]}
                c.update(at=now, files=files, source="live")
                try:
                    FILE.write_text(json.dumps({"url": url, "at": now, "files": files}, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            except Exception:   # a pod that is down, a wrong token, a server without this route: the last list stays
                pass
    if c["files"] is None:
        s = _saved(url)
        if s:
            c.update(at=s.get("at", 0), files=s["files"], source="cached")
    if c["files"] is not None:
        return {"files": c["files"], "source": c["source"] if c["source"] == "live" and now - c["at"] <= 2 * TTL else "cached", "at": c["at"]}
    m = _manifest()
    return {"files": m, "source": "manifest" if m else "none", "at": 0}
