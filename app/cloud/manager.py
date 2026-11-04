"""The rented server's life: deploy (disk -> server -> boot -> models -> engine), start it for a shoot, stop it when idle,
update it to the studio's version, delete it with its disk. The steps are shown on the Models page (DEPLOY) and in the
header (status()).

The server stops itself too (cloud/worker_server.py, IDLE_STOP_MIN = the studio's limit + 10 min, with the pod's own
key that RunPod gives it - never the user's): the safety net for a closed studio - a forgotten pod must not run up the
user's bill. On Vast.ai there is no such key: only this studio's watch() stops the server.

A provider without an https proxy (Vast.ai, Provider.tls) gets the studio's own certificate in the server's environment
at creation (app/cloud/tls.py); its address can change with every start, so _sync_url() keeps the record current.
"""
from __future__ import annotations

import asyncio
import json
import re
import math
import os
import secrets
import time
import urllib.request
from typing import Callable, Optional

from .. import config, console, i18n
from ..engine import remote
from ..engine.comfy import COMFY
from ..engine.errors import EngineError
from . import keys, state, tls
from .providers import PROVIDERS, Gpu, Provider, ProviderError

IMAGE_REPO = "ghcr.io/inikolax/remiqora-video-worker"
STEPS = ("volume", "pod", "boot", "models", "engine", "done")
BOOT_TIMEOUT = 20 * 60    # a pod's first start pulls the image (~11 GB)
WAKE_TIMEOUT = 10 * 60
WARN_BEFORE = 120         # seconds: the header warns this long before the auto-stop
WAIT_CARD = 15 * 60       # a deploy waits this long for a free card, trying every WAIT_EVERY s, then gives up
WAIT_EVERY = 30
STALL = 20 * 60           # the models' download with no new bytes this long, or a server that never answers: stopped


def app_version() -> str:
    """The desktop app passes its version (the installed app has no desktop/package.json); a checkout reads the file."""
    if v := os.environ.get("REMIQORA_VIDEO_VERSION", "").strip():
        return v
    try:
        return json.loads((config.ROOT / "desktop" / "package.json").read_text(encoding="utf-8")).get("version", "dev")
    except (OSError, ValueError):
        return "dev"


def image() -> str:
    return os.environ.get("CLOUD_IMAGE") or f"{IMAGE_REPO}:{app_version()}"


def manifest_sets() -> dict:
    m = json.loads((config.ROOT / "install" / "manifest.json").read_text(encoding="utf-8"))
    return {k: {"title": v.get("title", k), "bytes": v.get("bytes", 0), "gated": bool(v.get("gated"))} for k, v in m["sets"].items()}


def disk_gb(sets: list[str]) -> int:
    """The sets' weights with a 5 % margin and 5 GB for the work files, rounded up to 10 GB."""
    ms = manifest_sets()
    total = sum(ms[s]["bytes"] for s in sets if s in ms) / 1e9
    return max(20, int(math.ceil((total * 1.05 + 5) / 10) * 10))


def provider(d: Optional[dict] = None) -> Provider:
    d = d or state.load()
    key = keys.load(d["provider"])
    if not key:
        raise ProviderError("key", i18n.L("Нет ключа провайдера: вставьте его на странице «Модели» → «Облако»",
                                          "No provider key: paste it on the Models page → «Cloud»"))
    return PROVIDERS[d["provider"]](key)


# ----- what the page and the header see -----
DEPLOY: dict = {"running": False, "step": None, "error": None, "code": None, "models": None, "started": None, "tries": 0, "cancel": False}
LIVE: dict = {"status": None, "checked": 0.0, "health": None, "warn": False, "since": None, "dead_since": None, "rt": {}}
_lock = asyncio.Lock()


def _health(pod: Optional[dict], timeout: float = 8) -> Optional[dict]:
    """The server's /health, with its pinned certificate when the record has one; None: no address yet or no answer."""
    if not pod or not pod.get("url"):
        return None
    try:
        req = urllib.request.Request(pod["url"].rstrip("/") + "/health", headers={"User-Agent": remote.USER_AGENT})
        with remote.urlopen(req, timeout, pod.get("tls_cert") or "") as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def _sync_url(prov: Provider, pod: dict) -> None:
    """After a status check: the address the provider gives now into the record on disk and into config, so the next
    request goes there (Vast: a new port after every start; RunPod: the same proxy address, nothing to save)."""
    try:
        url = prov.url(pod)
    except ProviderError:
        return
    if not url:
        return
    pod["url"] = url
    d = state.load()
    if d.get("pod") and d["pod"].get("id") == pod.get("id") and (d["pod"].get("url") != url or config.REMOTE_URL != url.rstrip("/")):
        d["pod"]["url"] = url
        state.save(d)
        state.apply(d)


def _tls_env(prov: Provider, rec: dict) -> dict:
    """A provider without an https proxy: a fresh key and certificate for the server (its environment), the
    certificate pinned in the record; nothing for the others."""
    if not prov.tls:
        return {}
    cert, key = tls.make()
    rec.update(tls_cert=cert, tls_fp=tls.fingerprint(cert))
    return tls.env(cert, key)


async def refresh() -> dict:
    """The server's state now (at most every 15 s from the provider; /health every time it is running)."""
    d = state.load()
    pod = d.get("pod")
    if d["mode"] != "cloud" or not pod:
        LIVE.update(status=None, health=None)
        return LIVE
    if time.time() - LIVE["checked"] > 15 or LIVE["status"] is None:
        try:
            prov = provider(d)
            LIVE["status"], LIVE["rt"] = await asyncio.to_thread(prov.detail, pod)
            _sync_url(prov, pod)
        except ProviderError as e:
            LIVE["status"] = "unknown" if e.code == "net" else LIVE["status"] or "unknown"
        LIVE["checked"] = time.time()
        if LIVE["status"] == "running" and not pod.get("vram"):   # a server deployed before the card's memory was kept
            try:
                g = next((g for g in await asyncio.to_thread(provider(d).gpus) if g.id == pod.get("gpu_id")), None)
                if g:
                    pod["vram"] = g.memory
                    state.save(d)
            except ProviderError:
                pass
        if LIVE["status"] == "running" and not LIVE["since"]:
            LIVE["since"] = time.time()
        elif LIVE["status"] != "running":
            LIVE["since"] = None
    LIVE["health"] = await asyncio.to_thread(_health, pod, 5) if LIVE["status"] in ("running", "starting") else None
    # the provider says running as soon as the container starts: until the server answers it is «запускается»
    if LIVE["status"] == "running" and not LIVE["health"]:
        LIVE["status"] = "starting"
    elif LIVE["status"] == "starting" and LIVE["health"]:
        LIVE.update(status="running", since=LIVE["since"] or time.time())
    return LIVE


def _uptime() -> int:
    """Seconds the server has run since its start: the provider's count (a studio restart doesn't reset it)."""
    if LIVE["status"] not in ("running", "starting"):
        return 0
    up = (LIVE.get("rt") or {}).get("uptime")
    return int(up) if up else round(time.time() - LIVE["since"]) if LIVE["since"] else 0


def _cost(pod: Optional[dict]) -> float:
    """What this run of the server has cost so far (the card's hourly price; the disk is billed apart, by the month)."""
    return round(float(pod.get("price") or 0) * _uptime() / 3600, 2) if pod else 0.0


def vram(pod: Optional[dict]) -> Optional[dict]:
    """The rented card's memory for the header's meter, as /api/system gives the local one (GB)."""
    rt = LIVE.get("rt") or {}
    total = float((pod or {}).get("vram") or 0)
    if LIVE["status"] != "running" or not total or rt.get("mem_util") is None:
        return None
    return {"name": pod.get("gpu_name"), "used": round(total * float(rt["mem_util"]) / 100, 1), "total": total,
            "load": rt.get("gpu_util"), "temp": None}


def status() -> dict:
    d = state.load()
    pod = d.get("pod")
    h = LIVE.get("health") or {}
    return {**state.public(d), "has_key": keys.has(d["provider"]), "deploy": DEPLOY,
            "live": {"status": LIVE["status"], "warn": LIVE["warn"], "running_for": _uptime(), "cost": _cost(pod), "vram": vram(pod),
                     "idle": h.get("idle"), "busy": h.get("busy"), "models": h.get("models"), "fast": h.get("fast"), "version": h.get("version"),
                     "outdated": bool(pod and h.get("version") and h.get("version") not in ("dev", app_version()))},
            "image": image(), "app_version": app_version()}


# ----- mode -----
async def set_mode(mode: str, server: Optional[dict] = None) -> None:
    if mode not in state.MODES:
        raise ValueError(mode)
    d = state.load()
    from ..llm import client as llm
    await llm.SERVER.stop()
    await COMFY.stop()   # the GPU of the mode we leave is freed (a local worker holds ~20 GB)
    d["mode"] = mode
    if server is not None:
        d["server"] = {"url": server.get("url", "").strip().rstrip("/"), "token": server.get("token") or d["server"].get("token", "")}
    state.save(d)
    state.apply(d)
    LIVE.update(status=None, checked=0.0, health=None, since=None)


# ----- deploy -----
async def deploy(gpu_id: str, sets: list[str], auto_stop: int, hf_token: str = "") -> None:
    async with _lock:
        if DEPLOY["running"]:
            raise ProviderError("state", i18n.L("Развёртывание уже идёт", "A deploy is running already"))
        DEPLOY.update(running=True, step="volume", error=None, code=None, models=None, started=time.time(), tries=0, cancel=False,
                      times={"volume": time.time()}, layers={}, pull=None, note=None)
    asyncio.create_task(_deploy(gpu_id, sets, auto_stop, hf_token))


def cancel_wait() -> None:
    """«Отмена» while a deploy waits for a free card (nothing is created yet, nothing to undo)."""
    if DEPLOY["running"] and DEPLOY["step"] == "wait":
        DEPLOY["cancel"] = True


def _step(s: str) -> None:
    DEPLOY["step"] = s
    DEPLOY.setdefault("times", {}).setdefault(s, time.time())   # when each step began: the page shows how long it took


LAYER = re.compile(r"^([0-9a-f]{12}): (.+)$")


def _pull(text: str) -> None:
    """The image's pull as docker reports it to the provider («<layer>: Downloading», «Pull complete», «Already
    exists»), remembered across checks: {"done": layers ready, "seen": layers met so far} for the page's bar."""
    layers = DEPLOY.setdefault("layers", {})
    for line in text.splitlines():
        m = LAYER.match(line.strip())
        if m:
            layers[m.group(1)] = layers.get(m.group(1)) or m.group(2).startswith(("Pull complete", "Already exists"))
    DEPLOY["pull"] = {"done": sum(1 for x in layers.values() if x), "seen": len(layers)} if layers else None


async def _deploy(gpu_id: str, sets: list[str], auto_stop: int, hf_token: str) -> None:
    try:
        d = state.load()
        prov = provider(d)
        if d.get("pod"):
            raise ProviderError("state", i18n.L("Облачный сервер уже есть: удалите его или обновите", "A cloud server exists already: delete or update it"))
        sets = [s for s in dict.fromkeys([*sets, "writer"]) if s in manifest_sets()]
        token = secrets.token_urlsafe(32)
        env = {"REMOTE_TOKEN": token, "MODEL_SETS": ",".join(sets), "IDLE_STOP_MIN": str(auto_stop + 10 if auto_stop else 0)}
        if hf_token:
            env["HF_TOKEN"] = hf_token
        pinned: dict = {}
        env.update(await asyncio.to_thread(_tls_env, prov, pinned))
        rec = await _create_waiting(prov, gpu_id, sets, env)
        rec.update(token=token, sets=sets, image=image(), created=time.time(), **pinned)
        d.update(mode="cloud", pod=rec, auto_stop=auto_stop)
        state.save(d)
        state.apply(d)
        await _boot(prov, rec, BOOT_TIMEOUT, _step)
        _step("models")
        await _models(rec)
        _step("engine")
        await COMFY.ensure()
        _mark_ready()
        _step("done")
    except (ProviderError, EngineError) as e:
        DEPLOY.update(error=str(e), code=getattr(e, "code", "engine"))
        await _halt()
    except Exception as e:  # noqa: BLE001 - the page shows it
        DEPLOY.update(error=f"{type(e).__name__}: {e}", code="other")
        await _halt()
    finally:
        DEPLOY["running"] = False
        LIVE.update(checked=0.0)


async def _create_waiting(prov: Provider, gpu_id: str, sets: list[str], env: dict) -> dict:
    """The server on the card gpu_id. A card the catalog shows can be gone a second later («There are no longer any
    instances available»): the deploy then waits for one (step «wait», a try every WAIT_EVERY s, WAIT_CARD at most)."""
    t0 = time.time()
    while True:
        DEPLOY["tries"] += 1
        try:
            gpu: Optional[Gpu] = next((g for g in await asyncio.to_thread(prov.gpus) if g.id == gpu_id), None)
            if gpu is None:
                raise ProviderError("capacity", i18n.L("Этой видеокарты сейчас нет у провайдера", "The provider has no such card now"))
            return await asyncio.to_thread(lambda: prov.create(name="remiqora-video", gpu=gpu, disk_gb=disk_gb(sets), image=image(),
                                                               env=env, on_step=_step))
        except ProviderError as e:
            if e.code != "capacity" or time.time() - t0 > WAIT_CARD:
                raise
        _step("wait")
        for _ in range(WAIT_EVERY):
            if DEPLOY["cancel"]:
                raise ProviderError("cancelled", i18n.L("Ожидание видеокарты отменено", "Waiting for a card cancelled"))
            await asyncio.sleep(1)


def _mark_ready() -> None:
    d = state.load()
    if d.get("pod"):
        d["pod"]["ready"] = True
        state.save(d)


async def _resume() -> None:
    """A deploy the studio's restart cut (the pod exists, never reached «Готово»): on from waiting for the server."""
    try:
        d = state.load()
        prov, pod = provider(d), d["pod"]
        await _boot(prov, pod, BOOT_TIMEOUT, _step)
        _step("models")
        await _models(pod)
        _step("engine")
        await COMFY.ensure()
        _mark_ready()
        _step("done")
    except (ProviderError, EngineError) as e:
        DEPLOY.update(error=str(e), code=getattr(e, "code", "engine"))
        await _halt()
    except Exception as e:  # noqa: BLE001 - the page shows it
        DEPLOY.update(error=f"{type(e).__name__}: {e}", code="other")
        await _halt()
    finally:
        DEPLOY["running"] = False


async def _halt() -> None:
    """A deploy that failed with the server made: it is stopped, not left running (and billing) until someone looks.
    The disk and the record stay - «Удалить и начать заново» or a start later."""
    d = state.load()
    if d.get("pod"):
        try:
            await asyncio.to_thread(provider(d).stop, d["pod"])
            LIVE.update(status="stopped", checked=time.time(), since=None)
        except ProviderError:
            pass


async def _boot(prov: Provider, pod: dict, timeout: float, on_step: Callable[[str], None]) -> None:
    """Until the provider says running and the server answers /health."""
    on_step("boot")
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, rt = await asyncio.to_thread(prov.detail, pod)
        if st == "missing":
            raise ProviderError("missing", i18n.L("Сервер пропал у провайдера", "The server is gone at the provider"))
        DEPLOY["note"] = (rt or {}).get("msg")   # what the provider says while the server starts (Vast: the image's pull)
        if (rt or {}).get("pull"):
            _pull(rt["pull"])
        _sync_url(prov, pod)
        if st == "running" and await asyncio.to_thread(_health, pod, 5):
            LIVE.update(status="running", checked=time.time(), since=LIVE["since"] or time.time())
            return
        await asyncio.sleep(5)
    raise ProviderError("other", i18n.L("Сервер не поднялся вовремя", "The server did not come up in time"))


async def _models(pod: dict) -> None:
    seen, moved = None, time.time()
    while True:
        h = await asyncio.to_thread(_health, pod, 8) or {}
        m = h.get("models") or {}
        DEPLOY["models"] = m
        if m.get("state") in ("ready", "partial", "off"):
            return
        if m.get("state") == "failed":
            raise ProviderError("other", i18n.L("Модели не скачались: ", "The models did not download: ") + str(m.get("error", "")))
        now = (m.get("done"), m.get("file"), bool(h))
        if now != seen:
            seen, moved = now, time.time()
        elif time.time() - moved > STALL:   # no byte and no answer for 20 min: a hung download must not bill on
            raise ProviderError("other", i18n.L("Скачивание моделей встало", "The models' download stalled"))
        await asyncio.sleep(3)


# ----- start / stop / delete -----
async def wake(on_status: Callable[[str], None] = lambda s: None) -> None:
    """remote.WAKE: the rented server running before a shoot or the writer (nothing for «Свой сервер» / local)."""
    d = state.load()
    pod = d.get("pod")
    if d["mode"] != "cloud" or not pod:
        return
    LIVE["warn"] = False
    if await asyncio.to_thread(_health, pod, 5):
        return
    prov = provider(d)
    st = await asyncio.to_thread(prov.status, pod)
    _sync_url(prov, pod)
    if st == "missing":
        raise EngineError(i18n.L("Облачного сервера больше нет у провайдера: разверните его заново («Модели» → «Облако»)",
                                 "The cloud server is gone at the provider: deploy it again (Models → Cloud)"))
    if st in ("stopped", "error"):
        on_status(i18n.L("Запускаю облачный сервер…", "Starting the cloud server…"))
        try:
            await asyncio.to_thread(prov.start, pod)
        except ProviderError as e:
            raise EngineError(i18n.L(f"Облачный сервер не запустился: {e}", f"The cloud server did not start: {e}")) from e
    on_status(i18n.L("Жду облачный сервер…", "Waiting for the cloud server…"))
    try:
        await _boot(prov, pod, WAKE_TIMEOUT, lambda s: None)
    except ProviderError as e:
        raise EngineError(str(e)) from e


async def stop() -> None:
    d = state.load()
    if not d.get("pod"):
        return
    if config.ENGINE == "remote":
        await COMFY.current().stop()
    from ..llm import client as llm
    await llm.SERVER.stop()
    await asyncio.to_thread(provider(d).stop, d["pod"])
    LIVE.update(status="stopped", checked=time.time(), since=None, warn=False)


async def start() -> None:
    """«Запустить»: the provider is told at once, the wait for the server runs in the background - the page and the
    header show «запускается» meanwhile instead of a button that hangs for a minute."""
    d = state.load()
    pod = d.get("pod")
    if not pod:
        return
    prov = provider(d)
    if await asyncio.to_thread(prov.status, pod) in ("stopped", "error"):
        await asyncio.to_thread(prov.start, pod)
    _sync_url(prov, pod)
    LIVE.update(status="starting", checked=time.time(), since=None, warn=False)

    async def wait() -> None:
        try:
            await _boot(prov, pod, WAKE_TIMEOUT, lambda s: None)
        except ProviderError:
            LIVE.update(status="error", checked=time.time())
    asyncio.create_task(wait())


async def delete() -> None:
    d = state.load()
    pod = d.get("pod")
    if not pod:
        return
    if config.ENGINE == "remote":
        await COMFY.current().stop()
    await asyncio.to_thread(provider(d).delete, pod)
    d["pod"] = None
    if d["mode"] == "cloud":
        d["mode"] = "local"
    state.save(d)
    state.apply(d)
    LIVE.update(status=None, checked=0.0, health=None, since=None, warn=False)
    DEPLOY.update(step=None, error=None, code=None, models=None)


async def move(gpu_id: str, hf_token: str = "") -> None:
    """«Развернуть в другом дата-центре»: the server and its disk are deleted and a new deploy starts with the same sets
    and auto-stop, on gpu_id wherever it is free (the models download again, ~10 min). For a disk whose data center has
    no card left (the swap, the update and even a start can't run there)."""
    d = state.load()
    pod = d.get("pod")
    if not pod:
        raise ProviderError("missing", i18n.L("Облачного сервера нет", "There is no cloud server"))
    if DEPLOY["running"]:
        raise ProviderError("state", i18n.L("Развёртывание уже идёт", "A deploy is running already"))
    sets, auto_stop = [s for s in pod.get("sets", []) if s != "writer"], int(d.get("auto_stop") or 0)
    await delete()
    await deploy(gpu_id, sets, auto_stop, hf_token)


async def add_models(sets: list[str], hf_token: str = "") -> None:
    """More sets onto the existing server's disk (the disk must have room: RunPod grows a volume, not shrinks)."""
    d = state.load()
    pod = d.get("pod")
    if not pod:
        return
    want = [s for s in dict.fromkeys([*pod.get("sets", []), *sets]) if s in manifest_sets()]
    need = disk_gb(want)
    if need > int(pod.get("disk_gb") or 0):
        prov = provider(d)
        if not prov.can_grow:
            raise ProviderError("state", i18n.L(f"{prov.name} не умеет увеличивать диск: удалите сервер и разверните его заново с нужными наборами",
                                                f"{prov.name} cannot grow a disk: delete the server and deploy it again with the sets you need"))
        await asyncio.to_thread(prov.grow, pod, need)
        pod["disk_gb"] = need
        state.save(d)
    await wake()
    await asyncio.to_thread(remote.http_json, "POST", "/models/download", {"sets": ",".join(want), "hf_token": hf_token})
    pod["sets"] = want
    state.save(d)


async def swap_gpu(gpu_id: str) -> None:
    """«Сменить видеокарту»: the server is replaced by one with another card on the same disk, then it boots and
    connects as after a deploy (the image is pulled again: a newer one of the same tag comes with it)."""
    await _replace(gpu_id)


async def update() -> None:
    """«Обновить сервер»: the same card, the current image (manager.image()); the disk with the models stays."""
    pod = state.load().get("pod")
    if not pod or not pod.get("gpu_id"):
        raise ProviderError("missing", i18n.L("Облачного сервера нет", "There is no cloud server"))
    await _replace(pod["gpu_id"])


async def _replace(gpu_id: str) -> None:
    """The one path of «Сменить видеокарту» and «Обновить сервер»: a new server on the same disk with the card gpu_id."""
    async with _lock:
        if DEPLOY["running"]:
            raise ProviderError("state", i18n.L("Развёртывание уже идёт", "A deploy is running already"))
        DEPLOY.update(running=True, step="pod", error=None, code=None, models=None, started=time.time())
    try:
        d = state.load()
        pod, prov = d["pod"], provider(d)
        if gpu_id != pod.get("gpu_id") and not prov.can_swap:
            raise ProviderError("state", i18n.L(f"На {prov.name} карту не сменить: диск живёт на одной машине. Удалите сервер и разверните заново с другой картой",
                                                f"On {prov.name} the card cannot be changed: the disk lives on one machine. Delete the server and deploy again with another card"))
        gpu = next((g for g in await asyncio.to_thread(prov.gpus) if g.id == gpu_id), None)
        if gpu is None:
            raise ProviderError("capacity", i18n.L("Этой видеокарты сейчас нет у провайдера", "The provider has no such card now"))
        if config.ENGINE == "remote":
            await COMFY.current().stop()
        from ..llm import client as llm
        await llm.SERVER.stop()
        env = {"REMOTE_TOKEN": pod["token"], "MODEL_SETS": ",".join(pod.get("sets", [])),
               "IDLE_STOP_MIN": str(int(d.get("auto_stop") or 0) + 10 if d.get("auto_stop") else 0)}
        pinned: dict = {}   # a new server gets a new certificate (the old key went with the old one)
        env.update(await asyncio.to_thread(_tls_env, prov, pinned))
        rec = await asyncio.to_thread(prov.swap_gpu, pod, gpu, image(), env)
        rec.update(image=image(), **pinned)
        d["pod"] = rec
        state.save(d)
        state.apply(d)
        LIVE.update(status="starting", checked=time.time(), since=None, health=None)
    except Exception:
        DEPLOY["running"] = False
        raise
    asyncio.create_task(_resume())


async def keepalive() -> None:
    LIVE["warn"] = False
    if config.ENGINE == "remote":
        await asyncio.to_thread(remote.http_json, "GET", "/llm/models")   # any request with the token is activity


# ----- the auto-stop -----
async def watch() -> None:
    """Every 30 s: an idle rented server is warned about, then stopped (the user's limit, «Модели» → «Облако»)."""
    while True:
        await asyncio.sleep(30)
        try:
            d = state.load()
            if d["mode"] != "cloud" or not d.get("pod") or DEPLOY["running"]:
                continue
            await refresh()
            h = LIVE.get("health")
            if LIVE["status"] == "starting" and not h:
                LIVE["dead_since"] = LIVE.get("dead_since") or time.time()
                if time.time() - LIVE["dead_since"] > STALL:
                    LIVE["dead_since"] = None
                    await stop()
                    console.cloud_auto_stop(0, stalled=True)
                continue
            LIVE["dead_since"] = None
            limit = int(d.get("auto_stop") or 0) * 60
            if LIVE["status"] != "running" or not h or not limit or h.get("busy"):
                LIVE["warn"] = False
                continue
            idle = h.get("idle") or 0
            LIVE["warn"] = idle >= limit - WARN_BEFORE
            if idle >= limit:
                await stop()
                console.cloud_auto_stop(limit // 60)
        except Exception:  # noqa: BLE001 - the next round tries again
            pass


def install() -> None:
    """At the studio's start: the mode from cloud.json, the wake hook for the engine and the writer; a deploy cut by
    the last restart goes on."""
    d = state.load()
    state.apply(d)
    remote.WAKE = wake
    if d["mode"] == "cloud" and d.get("pod") and not d["pod"].get("ready"):
        DEPLOY.update(running=True, step="boot", error=None, code=None, models=None, started=time.time())
        asyncio.get_running_loop().create_task(_resume())
