"""data/cloud.json: which mode, the user's own server, the provider, the rented server's record. Applying it sets
config.ENGINE / REMOTE_URL / REMOTE_TOKEN, which the engine and the writer read at every call."""
from __future__ import annotations

import base64
import json
from typing import Optional

from .. import config
from . import keys

FILE = config.DATA_DIR / "cloud.json"
MODES = ("local", "cloud", "server")
DEFAULT = {"mode": "local", "provider": "runpod", "auto_stop": 15, "server": {"url": "", "token": ""}, "pod": None}
_ENV_ENGINE = config.ENGINE   # ENGINE=remote in .env (development, «Свой сервер» by hand) wins until a mode is chosen


SEAL = "dpapi:"   # a stored token starts with it; anything else is an old plain token (sealed on the next save)


def _tokens(d: dict):
    """The two places a token lives in: the own server's and the rented pod's."""
    for rec in (d.get("server"), d.get("pod")):
        if isinstance(rec, dict) and isinstance(rec.get("token"), str):
            yield rec


def seal(token: str) -> str:
    if not token or token.startswith(SEAL):
        return token
    return SEAL + base64.b64encode(keys._dpapi(token.encode("utf-8"), True)).decode("ascii")


def unseal(token: str) -> str:
    if not token.startswith(SEAL):
        return token   # an old plain token
    try:
        return keys._dpapi(base64.b64decode(token[len(SEAL):]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""   # another Windows user's file, or a broken one: as if there were none


def load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        for rec in _tokens(d):
            rec["token"] = unseal(rec["token"])
    except (OSError, ValueError):
        d = {}
        if _ENV_ENGINE == "remote":
            d = {"mode": "server", "server": {"url": config.REMOTE_URL, "token": config.REMOTE_TOKEN}}
    return {**DEFAULT, **d}


def save(d: dict) -> None:
    d = json.loads(json.dumps(d))   # a copy: the caller keeps its plain tokens
    for rec in _tokens(d):
        rec["token"] = seal(rec["token"])
    FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def apply(d: Optional[dict] = None) -> None:
    d = d or load()
    pod = d.get("pod") or {}
    if d["mode"] == "cloud" and pod.get("url"):
        config.ENGINE, config.REMOTE_URL, config.REMOTE_TOKEN = "remote", pod["url"].rstrip("/"), pod["token"]
        config.REMOTE_CERT = pod.get("tls_cert") or ""   # Vast.ai: the server's own certificate, pinned
    elif d["mode"] == "server" and d["server"].get("url"):
        config.ENGINE, config.REMOTE_URL, config.REMOTE_TOKEN = "remote", d["server"]["url"].rstrip("/"), d["server"].get("token", "")
        config.REMOTE_CERT = ""
    else:
        config.ENGINE = "local"


def public(d: dict) -> dict:
    """What the page may see: no tokens."""
    pod = d.get("pod")
    return {"mode": d["mode"], "provider": d["provider"], "auto_stop": d["auto_stop"],
            "server": {"url": d["server"].get("url", ""), "token_set": bool(d["server"].get("token"))},
            "pod": {k: v for k, v in pod.items() if k not in ("token",)} if pod else None}
