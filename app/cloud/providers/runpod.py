"""RunPod by its REST API v2 (https://docs.runpod.io/api-reference-v2): a network volume in a data center that has the
chosen GPU free, a pod from our image with it at /workspace, port 8188 behind RunPod's https proxy
(https://<pod>-8188.proxy.runpod.net). RUNPOD_API / RUNPOD_PROXY in .env point it elsewhere (tools/fake_runpod.py)."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from ... import i18n
from .base import MIN_RAM_GB, MIN_VRAM_GB, Gpu, Provider, ProviderError

API = os.environ.get("RUNPOD_API", "https://api.runpod.io").rstrip("/")
PROXY = os.environ.get("RUNPOD_PROXY", "https://{id}-8188.proxy.runpod.net")
RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
# no fp8 (Ampere and older): our weights are int8 / fp8 / nvfp4, made for Ada and Blackwell; no MIG slices (a part of a
# shared card)
OLD = re.compile(r"\bMIG\b|\b(A100|A40|A30|A10G?|A6000|A5000|A4500|A4000|3090|3080|V100|T4|P100|RTX 6000\b(?! Ada))", re.I)
# 2026-11-02 measured: 96 GB hold H3 and its text encoder together (no swap per scene), ~1 min 15 s a 1080p scene
RECOMMENDED = {"RTX PRO 6000", "RTX PRO 6000 WK"}
VOLUME_DCS: dict = {"at": 0.0, "ids": None}
CUDA = "13.0"   # torch for CUDA 13 in the image
CONTAINER_GB = 30   # the pod's own disk beyond the weights' fast copy (cloud/worker_server.py FAST_MODELS): the image, work


class RunPod(Provider):
    id = "runpod"
    name = "RunPod"
    key_url = "https://console.runpod.io/user/credentials"
    storage_price = 0.07

    def _req(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 60):
        req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                                              "User-Agent": "RemiqoraVideo (+https://remiqora.com)"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:400]
                if e.code == 429 and attempt < 2:
                    time.sleep(float(e.headers.get("Retry-After") or 3))
                    continue
                code = {401: "key", 403: "key", 402: "balance", 404: "missing", 409: "state", 400: "capacity"}.get(e.code, "other")
                raise ProviderError(code, f"RunPod {e.code}: {text}") from e
            except (urllib.error.URLError, OSError) as e:
                raise ProviderError("net", f"RunPod: {e}") from e

    def account(self) -> dict:
        self._req("GET", "/v2/catalog/gpus")   # a wrong key fails here (401)
        try:   # the balance is only in the GraphQL API; without it the page just doesn't show one
            d = self._req("POST", "/graphql", {"query": "query { myself { clientBalance } }"}, 20)
            return {"balance": float(d["data"]["myself"]["clientBalance"])}
        except (ProviderError, KeyError, TypeError, ValueError):
            return {"balance": None}

    def _volume_dcs(self) -> Optional[set[str]]:
        """The data centers that keep network volumes (only GraphQL tells; the catalog lists the others too). None: unknown."""
        if time.time() - VOLUME_DCS["at"] > 3600:
            try:
                d = self._req("POST", "/graphql", {"query": "query { dataCenters { id storageSupport } }"}, 20)
                VOLUME_DCS.update(at=time.time(), ids={x["id"] for x in d["data"]["dataCenters"] if x.get("storageSupport")})
            except (ProviderError, KeyError, TypeError):
                return VOLUME_DCS["ids"]
        return VOLUME_DCS["ids"]

    def gpus(self) -> list[Gpu]:
        d = self._req("GET", f"/v2/catalog/gpus?include=AVAILABILITY&product=POD&cloud=SECURE&minCudaVersion={CUDA}")
        vol = self._volume_dcs()
        out = []
        for g in d.get("gpus", []):
            if g.get("manufacturer") != "NVIDIA" or (g.get("memory") or 0) < MIN_VRAM_GB or not g.get("secure") or OLD.search(g.get("name", "")):
                continue
            dcs = sorted((x for x in g.get("dataCenters") or [] if RANK.get(x.get("availability"), 0) > 0 and (vol is None or x.get("id") in vol)),
                         key=lambda x: -RANK.get(x.get("availability"), 0))
            out.append(Gpu(id=g["id"], name=g.get("name") or g["id"], memory=g["memory"], price=float((g.get("price") or {}).get("secure") or 0),
                           availability=dcs[0]["availability"].lower() if dcs else "none",
                           datacenters=[{"id": x["id"], "name": x.get("name", x["id"]), "availability": x["availability"].lower()} for x in dcs],
                           recommended=g.get("name") in RECOMMENDED))
        return sorted(out, key=lambda g: (g.availability == "none", g.price))

    def create(self, *, name: str, gpu: Gpu, disk_gb: int, image: str, env: dict,
               on_step: Callable[[str], None] = lambda s: None) -> dict:
        if not gpu.datacenters:
            raise ProviderError("capacity", i18n.L(f"{gpu.name}: сейчас нет свободных ни в одном дата-центре", f"{gpu.name}: none free in any data center now"))
        last: Optional[ProviderError] = None
        for dc in gpu.datacenters[:4]:   # a disk lives in one data center: where the card is free now
            on_step("volume")
            try:   # not every data center keeps network volumes (400): the next one
                vol = self._req("POST", "/v2/network-volumes", {"name": name, "size": disk_gb, "dataCenter": dc["id"]})
            except ProviderError as e:
                if e.code != "capacity":
                    raise
                last = e
                continue
            on_step("pod")
            try:
                pod = self._pod(name, gpu, dc["id"], vol["id"], image, env, disk_gb)
            except ProviderError as e:
                self._delete_volume(vol["id"])
                if e.code != "capacity":
                    raise
                last = e
                continue
            rec = {"id": pod["id"], "volume_id": vol["id"], "datacenter": dc["id"], "gpu_id": gpu.id, "gpu_name": gpu.name,
                   "price": gpu.price, "disk_gb": disk_gb, "vram": gpu.memory}
            rec["url"] = self.url(rec)
            return rec
        raise last or ProviderError("capacity", i18n.L(f"{gpu.name}: не нашлось свободной", f"{gpu.name}: none free"))

    def _pod(self, name: str, gpu: Gpu, dc: str, volume: str, image: str, env: dict, disk_gb: int) -> dict:
        return self._req("POST", "/v2/pods", {
            "name": name, "image": image, "cloud": "SECURE", "dataCenterIds": [dc],
            "gpu": {"id": gpu.id, "count": 1, "minCudaVersion": CUDA, "minRamPerGpu": MIN_RAM_GB},
            "mounts": {"network": [{"volumeId": volume, "path": "/workspace"}]},
            "ports": ["8188/http"], "env": env, "disk": CONTAINER_GB + disk_gb})

    def swap_gpu(self, pod: dict, gpu: Gpu, image: str, env: dict) -> dict:
        if pod["datacenter"] not in [x["id"] for x in gpu.datacenters]:
            raise ProviderError("capacity", i18n.L(f"{gpu.name}: в дата-центре диска ({pod['datacenter']}) сейчас нет свободных",
                                                   f"{gpu.name}: none free in the disk's data center ({pod['datacenter']}) now"))
        try:
            self._action(pod, "terminate")
        except ProviderError as e:
            if e.code != "missing":
                raise
        for attempt in range(10):   # the volume is free once the old pod is gone
            try:
                new = self._pod("remiqora-video", gpu, pod["datacenter"], pod["volume_id"], image, env, int(pod.get("disk_gb") or 0))
                break
            except ProviderError as e:
                if e.code not in ("state", "capacity") or attempt == 9:
                    raise
                time.sleep(3)
        rec = {**pod, "id": new["id"], "gpu_id": gpu.id, "gpu_name": gpu.name, "price": gpu.price, "vram": gpu.memory, "ready": False}
        rec["url"] = self.url(rec)
        return rec

    def status(self, pod: dict) -> str:
        return self.detail(pod)[0]

    def detail(self, pod: dict) -> tuple[str, dict]:
        try:
            d = self._req("GET", f"/v2/pods/{pod['id']}")
        except ProviderError as e:
            if e.code == "missing":
                return "missing", {}
            raise
        st = {"PROVISIONING": "starting", "STARTING": "starting", "RUNNING": "running", "EXITED": "stopped",
              "ERROR": "error", "TERMINATED": "missing"}.get(d.get("status", ""), "starting")
        rt = d.get("runtime") or {}
        g = (rt.get("gpus") or [{}])[0] or {}
        return st, {"uptime": rt.get("uptime"), "gpu_util": g.get("util"), "mem_util": g.get("memoryUtil")}

    def _action(self, pod: dict, action: str) -> None:
        self._req("POST", f"/v2/pods/{pod['id']}/action", {"action": action})

    def start(self, pod: dict) -> None:
        self._action(pod, "start")

    def stop(self, pod: dict) -> None:
        self._action(pod, "stop")

    def _delete_volume(self, vid: str) -> None:
        try:
            self._req("DELETE", f"/v2/network-volumes/{vid}")
        except ProviderError as e:
            if e.code != "missing":
                raise

    def grow(self, pod: dict, disk_gb: int) -> None:
        self._req("PATCH", f"/v2/network-volumes/{pod['volume_id']}", {"size": disk_gb})

    def delete(self, pod: dict) -> None:
        try:
            self._action(pod, "terminate")
        except ProviderError as e:
            if e.code != "missing":
                raise
        if pod.get("volume_id"):
            for _ in range(10):   # the volume can't go while the pod still holds it
                try:
                    self._delete_volume(pod["volume_id"])
                    return
                except ProviderError as e:
                    if e.code not in ("state", "capacity"):
                        raise
                    time.sleep(3)
            self._delete_volume(pod["volume_id"])

    def url(self, pod: dict) -> Optional[str]:
        return PROXY.format(id=pod["id"])
