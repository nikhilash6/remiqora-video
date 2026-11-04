"""What the studio needs of a GPU provider - every adapter (runpod.py, later vast.py) does the same few things, the rest
of the app (manager.py, the page) knows only these."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

# H3 offloads part of its weights to the host's memory: a worker on a 24 GB card held 30 GB of RAM in a test
# (2026-11-01, killed by a 30 GB VM) - a host with less than this per GPU is not taken
MIN_RAM_GB = 64
MIN_VRAM_GB = 24


class ProviderError(Exception):
    """code: key (the key is wrong), balance (no money), capacity (no such GPU free now), state (not now), net, other"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Gpu:
    id: str                    # the provider's id, for create()
    name: str                  # shown
    memory: int                # GB
    price: float               # USD an hour
    availability: str          # high | medium | low | none
    datacenters: list[dict] = field(default_factory=list)   # [{id, name, availability}], best first
    recommended: bool = False
    extra: float = 0.0         # USD once, on top of the hour: traffic of the first download (Vast.ai bills it per machine)

    def public(self) -> dict:
        return asdict(self)


class Provider:
    id = ""
    name = ""
    key_url = ""               # where the user gets a key
    storage_price = 0.07       # USD a GB a month for the disk with the models
    # what the manager and the page may offer on this provider (Vast.ai: a disk is fixed in size and lives on one machine)
    can_grow = True            # grow(): the disk with the models made bigger for more sets
    can_swap = True            # swap_gpu(): another card on the same disk
    tls = False                # no https proxy: the server serves the studio's own certificate (app/cloud/tls.py)
    stays = True               # a stopped server can always be started again (Vast: the machine may be rented by then)

    def __init__(self, key: str) -> None:
        self.key = key

    def account(self) -> dict:
        """{"balance": USD or None} - raises ProviderError("key") for a wrong key."""
        raise NotImplementedError

    def gpus(self) -> list[Gpu]:
        raise NotImplementedError

    def create(self, *, name: str, gpu: Gpu, disk_gb: int, image: str, env: dict,
               on_step: Callable[[str], None] = lambda s: None) -> dict:
        """A disk and a server with it; -> the record kept in cloud.json: {id, volume_id, datacenter, url, ...}."""
        raise NotImplementedError

    def status(self, pod: dict) -> str:
        """starting | running | stopped | error | missing"""
        raise NotImplementedError

    def detail(self, pod: dict) -> tuple[str, dict]:
        """The status and what the provider knows of the running server: {"uptime": s, "gpu_util": %, "mem_util": %}."""
        return self.status(pod), {}

    def start(self, pod: dict) -> None:
        raise NotImplementedError

    def stop(self, pod: dict) -> None:
        raise NotImplementedError

    def swap_gpu(self, pod: dict, gpu: Gpu, image: str, env: dict) -> dict:
        """Another card on the same disk (its data center): the server is replaced, the models stay; -> the new record."""
        raise NotImplementedError

    def grow(self, pod: dict, disk_gb: int) -> None:
        """The disk with the models made bigger (for more sets); providers only grow a disk, never shrink it."""
        raise NotImplementedError

    def delete(self, pod: dict) -> None:
        """The server and its disk: nothing of it is paid for after this."""
        raise NotImplementedError

    def url(self, pod: dict) -> Optional[str]:
        """The server's address now; it may change between starts (Vast: a random port on the machine's IP, known only
        while the instance runs) - the manager saves what this gives after each status check."""
        raise NotImplementedError

    @classmethod
    def public(cls) -> dict:
        """The page's view of the provider: id, name, where the key is, the disk's price, what it can."""
        return {"id": cls.id, "name": cls.name, "key_url": cls.key_url, "storage_price": cls.storage_price,
                "can_grow": cls.can_grow, "can_swap": cls.can_swap, "tls": cls.tls, "stays": cls.stays}
