"""A provider's API key on this machine only: encrypted for this Windows user (DPAPI) in data/cloud_<provider>.key.
Never in cloud.json, a log or an answer of the API (only "is there one")."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Optional

from .. import config


def _file(provider: str) -> Path:
    return config.DATA_DIR / f"cloud_{provider}.key"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32, _kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    UI_FORBIDDEN = 0x1

    def _dpapi(data: bytes, protect: bool) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        args = (ctypes.byref(src), None, None, None, None, UI_FORBIDDEN, ctypes.byref(out)) if protect else \
               (ctypes.byref(src), None, None, None, None, UI_FORBIDDEN, ctypes.byref(out))
        if not fn(*args):
            raise OSError(ctypes.GetLastError(), "DPAPI failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
else:   # outside Windows (development): a file only its owner reads
    def _dpapi(data: bytes, protect: bool) -> bytes:
        return data


def save(provider: str, key: str) -> None:
    f = _file(provider)
    f.write_bytes(_dpapi(key.strip().encode("utf-8"), True))
    if sys.platform != "win32":
        os.chmod(f, 0o600)


def load(provider: str) -> Optional[str]:
    f = _file(provider)
    if not f.is_file():
        return None
    try:
        return _dpapi(f.read_bytes(), False).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None   # another Windows user's file, or a broken one: as if there were none


def forget(provider: str) -> None:
    _file(provider).unlink(missing_ok=True)


def has(provider: str) -> bool:
    return _file(provider).is_file()
