"""Vast.ai by its REST API (https://docs.vast.ai/api-reference/overview, base https://console.vast.ai/api/v0) - written
from the docs (2026-11-02) and not yet run against the live API: every place that needs the first live test says so
with «TODO live». The research notes are kept in the internal cloud plan.

What is different from RunPod and what it means for the studio:
- a marketplace, not a cloud: the "GPU" the user picks is an OFFER (one machine, one price); it goes away when someone
  else rents it. Here Gpu.id = the card's name and Gpu.datacenters = the best offers for it (id = offer id), so
  create() can try the next offer as the RunPod adapter tries the next data center;
- the disk is LOCAL to the machine: a volume lives on the machine it was made on and is mounted only by instances on
  that same machine (https://docs.vast.ai/guides/instances/storage/volumes.md). "Stop, resume later" works only while
  nobody else has rented that GPU: a restart waits in «scheduling» and the docs suggest making a new instance and
  copying the data (https://docs.vast.ai/guides/instances/manage-instances.md) - status() gives up on that after
  SCHEDULING_MAX. swap_gpu() keeps only «the same card on the same machine» («Обновить сервер»); grow() is impossible
  (a volume's size is fixed) - can_grow / can_swap say so to the manager and the page;
- no https proxy: port 8188 is a random external port on the machine's shared public IP
  (https://docs.vast.ai/guides/instances/connect/networking.md). The address is known only once the instance runs
  (`ports` in show-instance) and can change after a restart - status() refreshes pod["url"], the manager saves it.
  The server does TLS itself with the certificate the studio made (tls = True, app/cloud/tls.py), so the token and the
  files never go in the clear;
- the pod has no key of its own (RunPod gives one): the server cannot stop itself, only the studio's auto-stop does;
- rate limits: a minimum interval per endpoint, 429 without Retry-After (https://docs.vast.ai/api-reference/rate-limits-and-errors.md).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from ... import i18n
from .base import MIN_RAM_GB, MIN_VRAM_GB, Gpu, Provider, ProviderError

API = os.environ.get("VAST_API", "https://console.vast.ai/api/v0").rstrip("/")
PORT = 8188
CUDA = 13.0           # torch for CUDA 13 in the image: the host's driver must allow it (cuda_max_good)
COMPUTE_CAP = 890     # Ada (8.9) and newer: our weights are int8 / fp8 / nvfp4; compute_cap is capability x 100
MIN_RELIABILITY = 0.95
MIN_INET_MBPS = 500   # the models (~50 GB) come from Hugging Face through the host's line; Vast hosts are often home machines
MIN_INTERVAL = 1.0    # seconds between our calls: Vast limits each endpoint by a minimum interval (no figure published)
DEPLOY_GB = 65        # what a deploy downloads: the image and the models (live 2026-11-02: ~65 GB), billed as traffic
MAX_TRAFFIC_TB = 10.0 # $/TB of download: above it the machine is dropped (live: one asked $26.67/TB, most $0-5)
OFFERS_PER_GPU = 6    # offers kept per card: create() tries them in turn
CONTAINER_GB = 30     # the instance's own disk: the image and the work files (the models are on the volume)
SCHEDULING_MAX = 90   # seconds in «scheduling» after a start before it means «the card is rented by someone else»
# marketplace names seen in the docs / console: "RTX 4090", "RTX 5090", "RTX 6000Ada", "RTX PRO 6000", "L40S", "H100 SXM"
RECOMMENDED = "RTX PRO 6000"   # as on RunPod: 96 GB hold H3 and its text encoder together (Vast names: Max-Q, S, WS)
MAX_PRICE = 6.0   # $/h: above it a card up to 100 GB is an outlier offer
OLD = re.compile(r"\b(A100|A40|A30|A10G?|A6000|A5000|A4500|A4000|3090|3080|V100|T4|P100)\b|RTX 6000(?!\s*Ada)", re.I)
SCHEDULING: dict[int, float] = {}   # instance id -> when «scheduling» was first seen


class Vast(Provider):
    id = "vast"
    name = "Vast.ai"
    key_url = "https://cloud.vast.ai/manage-keys/"
    storage_price = 0.10   # a guide for the page before an offer is chosen: it is per machine (storage_cost), the record keeps the real one
    can_grow = False
    can_swap = False
    tls = True
    stays = False

    _last_call = 0.0
    _call_lock = threading.Lock()

    # ----- transport -----
    def _req(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 60):
        req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                                              "Accept": "application/json", "User-Agent": "RemiqoraVideo (+https://remiqora.com)"})
        for attempt in range(4):
            with Vast._call_lock:   # the per-endpoint minimum interval, kept crudely for all endpoints at once
                wait = Vast._last_call + MIN_INTERVAL - time.time()
                if wait > 0:
                    time.sleep(wait)
                Vast._last_call = time.time()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read()
                    d = json.loads(raw) if raw else {}
                    # some endpoints answer 200 with {"success": false, "error": ..., "msg": ...}
                    if isinstance(d, dict) and d.get("success") is False:
                        raise ProviderError(_code(200, d.get("error") or "", d.get("msg") or ""), f"Vast.ai: {d.get('msg') or d.get('error')}")
                    return d
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:400]
                if e.code == 429 and attempt < 3:
                    time.sleep(2.0 * (attempt + 1))   # no Retry-After header: our own backoff
                    continue
                try:
                    err = json.loads(text)
                except ValueError:
                    err = {}
                raise ProviderError(_code(e.code, err.get("error") or "", err.get("msg") or text), f"Vast.ai {e.code}: {text}") from e
            except (urllib.error.URLError, OSError) as e:
                raise ProviderError("net", f"Vast.ai: {e}") from e

    # ----- account -----
    def account(self) -> dict:
        # https://docs.vast.ai/api-reference/accounts/show-user.md - {email, balance, id, ...}; a wrong key -> 401
        d = self._req("GET", "/users/current/")
        try:   # live 2026-11-02: the prepaid money is "credit"; "balance" is what is owed (0 on a prepaid account)
            return {"balance": float(d.get("credit") or 0) - float(d.get("balance") or 0)}
        except (TypeError, ValueError):
            return {"balance": None}

    # ----- offers -----
    def _offers(self, gpu_name: Optional[str] = None, machine_id: Optional[int] = None, limit: int = 300) -> list[dict]:
        """https://docs.vast.ai/api-reference/search/search-offers.md - POST /bundles/ with {field: {op: value}}.
        gpu_ram / cpu_ram are in MB in the API (the CLI takes GB)."""
        q: dict = {
            "verified": {"eq": True}, "rentable": {"eq": True}, "rented": {"eq": False}, "external": {"eq": False},
            "num_gpus": {"eq": 1}, "gpu_arch": {"eq": "nvidia"},
            "gpu_ram": {"gte": MIN_VRAM_GB * 1024}, "cpu_ram": {"gte": MIN_RAM_GB * 1024},
            "cuda_max_good": {"gte": CUDA},          # TODO live: confirm the API name (the CLI reference calls it cuda_vers)
            "compute_cap": {"gte": COMPUTE_CAP},
            "reliability": {"gte": MIN_RELIABILITY},  # TODO live: the API may want reliability2
            "direct_port_count": {"gte": 1},         # a port on the host's router: needed to reach 8188 at all
            "disk_space": {"gte": CONTAINER_GB}, "inet_down": {"gte": MIN_INET_MBPS},
            "type": "on-demand", "limit": limit, "order": [["dph_total", "asc"]],
        }
        if gpu_name:
            q["gpu_name"] = {"eq": gpu_name}
        if machine_id:
            q["machine_id"] = {"eq": machine_id}
        d = self._req("POST", "/bundles/", q)
        return [o for o in d.get("offers", []) if not OLD.search(o.get("gpu_name", "")) and _traffic_ok(o)]

    def gpus(self) -> list[Gpu]:
        """One Gpu per card name: the cheapest price, availability by how many offers there are, the best offers as
        «data centers» (id = offer id, name = where it is)."""
        by_name: dict[str, list[dict]] = {}
        for o in self._offers():
            by_name.setdefault(o.get("gpu_name") or "?", []).append(o)
        out = []
        for name, offers in by_name.items():
            offers.sort(key=lambda o: (float(o.get("dph_total") or 0), -float(o.get("reliability2") or o.get("reliability") or 0)))
            # a lone offer far above the market (live 2026-11-02: an «RTX 4090D» at $12.92/h) is a host's mistake, not a card
            if (offers[0].get("gpu_ram") or 0) <= 100 * 1024 and float(offers[0].get("dph_total") or 0) > MAX_PRICE:
                continue
            n = len(offers)
            out.append(Gpu(id=name, name=name, memory=int(round((offers[0].get("gpu_ram") or 0) / 1024)),
                           price=float(offers[0].get("dph_total") or 0), extra=round(_traffic(offers[0]), 2),
                           availability="high" if n >= 10 else "medium" if n >= 3 else "low",
                           datacenters=[_dc(o) for o in offers[:OFFERS_PER_GPU]], recommended=name.startswith(RECOMMENDED)))
        return sorted(out, key=lambda g: g.price)

    # ----- create -----
    def create(self, *, name: str, gpu: Gpu, disk_gb: int, image: str, env: dict,
               on_step: Callable[[str], None] = lambda s: None) -> dict:
        # offers go stale within minutes: fresh ones for this card, the saved list only as a fallback
        try:
            offers = self._offers(gpu.id)[:OFFERS_PER_GPU]
        except ProviderError:
            offers = [{"id": int(x["id"]), "machine_id": x.get("machine_id"), "dph_total": gpu.price, "geolocation": x.get("name")}
                      for x in gpu.datacenters]
        if not offers:
            raise ProviderError("capacity", i18n.L(f"{gpu.name}: сейчас нет подходящих предложений", f"{gpu.name}: no fitting offer now"))
        last: Optional[ProviderError] = None
        for o in offers:
            on_step("volume")
            vol = self._volume(o, disk_gb)   # None: no volume offer on this machine -> the instance's own disk
            on_step("pod")
            try:
                inst = self._instance(name, o, disk_gb, image, env, vol)
            except ProviderError as e:
                if vol:
                    self._delete_volume(vol["id"])
                if e.code not in ("capacity", "state"):
                    raise
                last = e
                continue
            return {"id": inst, "volume_id": vol["id"] if vol else None, "machine_id": o.get("machine_id"),
                    "datacenter": str(o.get("machine_id") or ""), "place": o.get("geolocation") or "", "gpu_id": gpu.id, "gpu_name": gpu.name,
                    "price": float(o.get("dph_total") or gpu.price), "disk_gb": disk_gb, "vram": gpu.memory,
                    "storage_price": float((vol or {}).get("storage_cost") or o.get("storage_cost") or self.storage_price), "url": None}
        raise last or ProviderError("capacity", i18n.L(f"{gpu.name}: не нашлось свободной", f"{gpu.name}: none free"))

    def _volume(self, offer: dict, disk_gb: int) -> Optional[dict]:
        """A local volume on the offer's machine (https://docs.vast.ai/api-reference/volumes/rent-volume.md: PUT /volumes/
        {id: volume offer id, size}) - it survives the instance, so «Обновить сервер» can keep the models.
        TODO live: whether /volumes/search accepts a machine_id filter (only `limit` is documented), the response of
        PUT /volumes/ (documented: {success, volume_name: "V.<id>"} - the id is parsed from the name)."""
        mid = offer.get("machine_id")
        if not mid:
            return None
        try:
            d = self._req("POST", "/volumes/search/", {"machine_id": {"eq": mid}, "disk_space": {"gte": disk_gb}, "limit": 8})
        except ProviderError as e:
            if e.code in ("key", "balance", "net"):
                raise
            return None
        vos = [v for v in d.get("offers", []) if v.get("machine_id") == mid and (v.get("disk_space") or 0) >= disk_gb]
        if not vos:
            return None
        vo = min(vos, key=lambda v: float(v.get("storage_cost") or 0))
        r = self._req("PUT", "/volumes/", {"id": int(vo["id"]), "size": int(disk_gb)})
        m = re.search(r"(\d+)", str(r.get("volume_name") or r.get("id") or ""))
        if not m:
            raise ProviderError("other", f"Vast.ai: volume created but no id in the answer: {r}")
        return {"id": int(m.group(1)), "storage_cost": float(vo.get("storage_cost") or 0)}

    def _instance(self, name: str, offer: dict, disk_gb: int, image: str, env: dict, vol: Optional[dict]) -> int:
        """https://docs.vast.ai/api-reference/instances/create-instance.md - PUT /asks/{offer id}/ ; -> new_contract (the
        instance id). Env and ports go as docker flags in one string. TODO live: PUT vs POST (the page shows both).
        The volume is the machine's own disk already: no fast copy (FAST_MODELS off), the instance disk stays small."""
        env = {**env, "FAST_MODELS": ""}
        flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        flags += f" -e OPEN_BUTTON_PORT={PORT} -p {PORT}:{PORT}"
        body: dict = {"image": image, "label": name, "disk": int(CONTAINER_GB if vol else CONTAINER_GB + disk_gb), "env": flags,
                      "runtype": "args", "args_str": "",   # the image's own ENTRYPOINT/CMD (cloud/start.sh); TODO live: that "args" with an empty args_str keeps CMD
                      "target_state": "running", "cancel_unavail": True}
        if vol:
            body["volume_info"] = {"create_new": False, "volume_id": vol["id"], "mount_path": "/workspace"}
            # TODO live: or the CLI way, "-v V.<id>:/workspace" in env - the docs show both
        d = self._req("PUT", f"/asks/{int(offer['id'])}/", body)
        if not d.get("new_contract"):
            raise ProviderError("capacity", f"Vast.ai: no instance from offer {offer['id']}: {d}")
        return int(d["new_contract"])

    # ----- state -----
    def _show(self, pod: dict) -> dict:
        # https://docs.vast.ai/api-reference/instances/show-instance.md -> {"instances": {...}}
        d = self._req("GET", f"/instances/{int(pod['id'])}/")
        inst = d.get("instances") if isinstance(d, dict) else None
        if isinstance(inst, list):
            inst = inst[0] if inst else None
        if not inst:
            raise ProviderError("missing", "Vast.ai: no such instance")
        return inst

    def status(self, pod: dict) -> str:
        return self.detail(pod)[0]

    def detail(self, pod: dict) -> tuple[str, dict]:
        """actual_status: running | loading | creating | connecting | scheduling | exited | offline | unknown | null
        (https://docs.vast.ai/guides/instances/manage-instances.md, show-instances «Key quirks»). The address is learnt
        here: the external port exists only while the instance runs (the manager saves pod["url"] after each check).
        TODO live: the names of start_date / gpu_util / mem_usage in show-instance."""
        try:
            inst = self._show(pod)
        except ProviderError as e:
            if e.code == "missing":
                return "missing", {}
            raise
        a = (inst.get("actual_status") or "").lower()
        intended = (inst.get("intended_status") or inst.get("next_state") or "").lower()
        rt = {"uptime": None, "gpu_util": inst.get("gpu_util"), "mem_util": inst.get("mem_usage"),
              # while the image is pulled: docker's own lines («…: Downloading», «Pull complete») - the last one is shown
              "msg": ([x for x in str(inst.get("status_msg") or "").splitlines() if x.strip()] or [None])[-1],
              "pull": str(inst.get("status_msg") or "")}
        if a == "running":
            SCHEDULING.pop(int(pod["id"]), None)
            url = _url(inst)
            if url:
                pod["url"] = url
            try:
                rt["uptime"] = max(0, int(time.time() - float(inst["start_date"]))) if inst.get("start_date") else None
            except (TypeError, ValueError):
                pass
            return ("running" if url else "starting"), rt
        if a in ("exited", "stopped"):
            SCHEDULING.pop(int(pod["id"]), None)
            return ("stopped" if intended in ("stopped", "") else "error"), rt
        if a in ("offline", "unknown"):
            return "error", rt   # the machine is away from Vast: the docs say it never comes back to running by itself
        if a == "scheduling" and intended in ("running", ""):
            # a start waits here while the card is free; longer than SCHEDULING_MAX means someone else rents it now
            # (the docs: «longer than ~30 s»), and waiting on would never end
            first = SCHEDULING.setdefault(int(pod["id"]), time.time())
            if time.time() - first > SCHEDULING_MAX:
                raise ProviderError("capacity", i18n.L("Машину с диском занял другой арендатор: сервер не запустится. Удалите его и разверните заново",
                                                       "Another renter took the machine with the disk: the server will not start. Delete it and deploy again"))
        return "starting", rt   # loading / creating / connecting / scheduling / None

    def start(self, pod: dict) -> None:
        # https://docs.vast.ai/api-reference/instances/manage-instance.md - PUT /instances/{id}/ {"state": "running"}
        SCHEDULING.pop(int(pod["id"]), None)
        self._req("PUT", f"/instances/{int(pod['id'])}/", {"state": "running"})

    def stop(self, pod: dict) -> None:
        self._req("PUT", f"/instances/{int(pod['id'])}/", {"state": "stopped"})

    # ----- the disk -----
    def grow(self, pod: dict, disk_gb: int) -> None:
        # a volume's size is fixed after creation (volumes.md); the instance's container disk too (storage/types.md)
        raise ProviderError("state", i18n.L("Vast.ai не умеет увеличивать диск: удалите сервер и разверните его заново с нужными наборами",
                                            "Vast.ai cannot grow a disk: delete the server and deploy it again with the sets you need"))

    def swap_gpu(self, pod: dict, gpu: Gpu, image: str, env: dict) -> dict:
        """Only «Обновить сервер»: the same card on the SAME machine (only there the volume mounts). Another card is
        refused - can_swap = False keeps the page from offering it; the manager says the same."""
        if gpu.id != pod.get("gpu_id"):
            raise ProviderError("state", i18n.L("На Vast.ai карту не сменить: диск живёт на одной машине. Удалите сервер и разверните заново с другой картой",
                                                "On Vast.ai the card cannot be changed: the disk lives on one machine. Delete the server and deploy again with another card"))
        if not pod.get("volume_id") or not pod.get("machine_id"):
            raise ProviderError("state", i18n.L("На Vast.ai диск живёт вместе с машиной: удалите сервер и разверните заново",
                                                "On Vast.ai the disk lives with the machine: delete the server and deploy again"))
        offers = [o for o in self._offers(gpu.id, machine_id=int(pod["machine_id"])) if o.get("machine_id") == pod["machine_id"]]
        if not offers:
            raise ProviderError("capacity", i18n.L(f"{gpu.name}: на машине с диском сейчас нет свободной - машину занял другой арендатор",
                                                   f"{gpu.name}: none free on the disk's machine now - another renter took it"))
        try:
            self._destroy(pod)
        except ProviderError as e:
            if e.code != "missing":
                raise
        last: Optional[ProviderError] = None
        for attempt in range(10):   # the volume is free once the old instance is gone
            try:
                new = self._instance("remiqora-video", offers[0], pod.get("disk_gb") or CONTAINER_GB, image, env, {"id": pod["volume_id"]})
                break
            except ProviderError as e:
                if e.code not in ("state", "capacity") or attempt == 9:
                    raise
                last = e
                time.sleep(3)
        else:
            raise last or ProviderError("state", "Vast.ai: could not re-create the instance")
        return {**pod, "id": new, "gpu_id": gpu.id, "gpu_name": gpu.name, "price": float(offers[0].get("dph_total") or gpu.price),
                "ready": False, "url": None}

    def _destroy(self, pod: dict) -> None:
        # https://docs.vast.ai/api-reference/instances/destroy-instance.md - DELETE /instances/{id}/ (the container disk goes with it)
        self._req("DELETE", f"/instances/{int(pod['id'])}/")

    def _delete_volume(self, vid: int) -> None:
        # https://docs.vast.ai/api-reference/volumes/delete-volume.md - DELETE /volumes/ {"id"}; refused while an instance holds it
        try:
            self._req("DELETE", "/volumes/", {"id": int(vid)})
        except ProviderError as e:
            if e.code != "missing":
                raise

    def delete(self, pod: dict) -> None:
        SCHEDULING.pop(int(pod["id"]), None)
        try:
            self._destroy(pod)
        except ProviderError as e:
            if e.code != "missing":
                raise
        if pod.get("volume_id"):
            for _ in range(10):
                try:
                    self._delete_volume(pod["volume_id"])
                    return
                except ProviderError as e:
                    if e.code not in ("state", "capacity"):
                        raise
                    time.sleep(3)
            self._delete_volume(pod["volume_id"])

    def url(self, pod: dict) -> Optional[str]:
        """Known only while the instance runs (detail() fills it from `ports`): https://<ip>:<port> - the server's own
        certificate, pinned in the record (tls_cert)."""
        return pod.get("url")


# ----- helpers -----
def _traffic_tb(o: dict) -> float:
    """The machine's download price in $/TB. The API's inet_down_cost is $/GB (the CLI shows it x1000 as $/TB)."""
    try:
        return float(o.get("inet_down_cost") or 0) * 1000
    except (TypeError, ValueError):
        return 0.0


def _traffic(o: dict) -> float:
    """What the deploy's download costs on this machine, USD."""
    return DEPLOY_GB / 1000 * _traffic_tb(o)


def _traffic_ok(o: dict) -> bool:
    """Expensive traffic or a slow line (inet_down, Mbit/s; the query asks for it too, a stale list is checked again)."""
    try:
        slow = float(o.get("inet_down") or 0) < MIN_INET_MBPS
    except (TypeError, ValueError):
        slow = True
    return not slow and _traffic_tb(o) <= MAX_TRAFFIC_TB


def _dc(o: dict) -> dict:
    r = float(o.get("reliability2") or o.get("reliability") or 0)
    return {"id": str(o["id"]), "name": f"{o.get('geolocation') or '?'} · #{o.get('machine_id')} · ${float(o.get('dph_total') or 0):.2f}/h + ${_traffic(o):.2f} " + i18n.L("трафик", "traffic"),
            "availability": "high" if r >= 0.99 else "medium" if r >= 0.97 else "low"}


def _url(inst: dict) -> Optional[str]:
    """https://<public ip>:<external port of 8188/tcp> from show-instance's `ports`
    ({"8188/tcp": [{"HostIp": "0.0.0.0", "HostPort": "33526"}]}); TODO live: the exact shape and whether the port changes
    after stop/start."""
    ip = inst.get("public_ipaddr")
    ports = inst.get("ports") or {}
    m = ports.get(f"{PORT}/tcp") if isinstance(ports, dict) else None
    if ip and m:
        try:
            hp = (m[0] if isinstance(m, list) else m).get("HostPort")
            return f"https://{ip}:{int(hp)}" if hp else None
        except (AttributeError, TypeError, ValueError):
            return None
    return None


def _code(http: int, error: str, msg: str) -> str:
    """ProviderError.code from Vast's {error, msg} (https://docs.vast.ai/api-reference/rate-limits-and-errors.md)."""
    text = f"{error} {msg}".lower()
    if http in (401, 403) or "api key" in text or "unauthorized" in text:
        return "key"
    if http == 402 or "insufficient" in text or "balance" in text or "credit" in text:
        return "balance"
    if http == 404 or "not_found" in error or "no_such" in error or "not found" in text:
        return "missing"
    if "unavailable" in text or "no longer" in text or "already rented" in text or "capacity" in text:
        return "capacity"
    if http in (409, 423) or "invalid state" in text or "still" in text:
        return "state"
    if http == 400:
        return "capacity"   # as in runpod.py: a bad offer most often means it went away
    return "other"
