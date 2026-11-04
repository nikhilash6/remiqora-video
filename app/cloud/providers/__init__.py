"""The GPU providers the studio can rent a server from: id -> adapter class (base.Provider)."""
from __future__ import annotations

from .base import Gpu, Provider, ProviderError  # noqa: F401
from .runpod import RunPod

PROVIDERS: dict[str, type[Provider]] = {"runpod": RunPod}
# shown greyed on the page, not selectable: Vast.ai (app/cloud/providers/vast.py) is written from the docs and has not
# run against the live API, and its server cannot stop itself with the studio closed - it comes after a live test
SOON: list[dict] = [{"id": "vast", "name": "Vast.ai"}]
