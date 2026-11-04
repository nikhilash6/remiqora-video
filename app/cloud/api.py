"""/api/cloud: the Models page's «Где работают модели» section and the header's cloud chip."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import h3_licence, i18n, jobs
from ..engine import remote
from ..engine.errors import EngineError
from . import keys, manager, state
from .providers import PROVIDERS, SOON, ProviderError

router = APIRouter(prefix="/api/cloud")


def _fail(e: Exception) -> HTTPException:
    code = getattr(e, "code", "other")
    return HTTPException({"key": 401, "balance": 402, "state": 409, "missing": 404}.get(code, 400), str(e))


def _idle() -> None:
    if jobs.busy():
        raise HTTPException(409, i18n.L("Сейчас идёт съёмка или задача сценариста: дождитесь конца", "A shoot or a writer's job is running: wait for it to end"))


@router.get("")
async def get_cloud():
    await manager.refresh()
    return {**manager.status(), "providers": [p.public() for p in PROVIDERS.values()], "soon": SOON}


class ProviderReq(BaseModel):
    provider: str


@router.put("/provider")
def put_provider(req: ProviderReq):
    """The provider chosen on the page (before its key is pasted); not while a rented server exists."""
    if req.provider not in PROVIDERS:
        raise HTTPException(400, i18n.L("Нет такого провайдера", "No such provider"))
    d = state.load()
    if d.get("pod") and d["provider"] != req.provider:
        raise HTTPException(409, i18n.L("Сначала удалите облачный сервер у текущего провайдера", "Delete the cloud server at the current provider first"))
    d["provider"] = req.provider
    state.save(d)
    return manager.status()


class ModeReq(BaseModel):
    mode: str
    url: Optional[str] = None
    token: Optional[str] = None
    stop_pod: bool = False   # to «Этот компьютер» from the header: the rented server is stopped too (its disk stays)


def _safe_url(url: str) -> bool:
    """https anywhere; plain http only to this computer or a private network address."""
    u = urlparse(url.strip())
    if u.scheme == "https":
        return True
    if u.scheme != "http" or not u.hostname:
        return False
    if u.hostname == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


@router.put("/mode")
async def put_mode(req: ModeReq):
    _idle()
    if any(j.status == "queued" for j in jobs.JOBS.values()):   # a queued shoot would start on the other side
        raise HTTPException(409, i18n.L("В очереди есть задачи: дождитесь их или уберите из очереди", "There are queued jobs: wait for them or take them out of the queue"))
    if req.mode not in state.MODES:
        raise HTTPException(400, req.mode)
    server = {"url": req.url or "", "token": req.token} if req.mode == "server" else None
    if server and server["url"] and not _safe_url(server["url"]):
        raise HTTPException(400, i18n.L("Адрес сервера должен начинаться с https:// (http:// — только для этого компьютера и локальной сети): "
                                        "иначе токен и файлы идут открытым текстом",
                                        "The server address must start with https:// (http:// only for this computer and the local "
                                        "network): otherwise the token and the files go in the clear"))
    await manager.set_mode(req.mode, server)
    if req.mode == "local" and req.stop_pod:
        try:
            await manager.stop()
        except ProviderError as e:
            raise _fail(e) from e
    return manager.status()


@router.post("/server/check")
async def check_server():
    """«Свой сервер»: the address answers and takes the token."""
    try:
        h = await asyncio.to_thread(remote.http_json, "GET", "/health", None, 10)
        await asyncio.to_thread(remote.http_json, "GET", "/llm/models", None, 10)
    except EngineError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "version": h.get("version"), "worker": h.get("worker")}


class KeyReq(BaseModel):
    provider: str
    key: str


@router.put("/key")
async def put_key(req: KeyReq):
    if req.provider not in PROVIDERS or not req.key.strip():
        raise HTTPException(400, i18n.L("Нет такого провайдера или пустой ключ", "No such provider or an empty key"))
    try:
        acc = await asyncio.to_thread(PROVIDERS[req.provider](req.key.strip()).account)
    except ProviderError as e:
        raise _fail(e) from e
    keys.save(req.provider, req.key)
    d = state.load()
    d["provider"] = req.provider
    state.save(d)
    return acc


@router.delete("/key")
async def delete_key():
    d = state.load()
    keys.forget(d["provider"])
    return {"ok": True}


@router.get("/account")
async def account():
    try:
        return await asyncio.to_thread(manager.provider().account)
    except ProviderError as e:
        raise _fail(e) from e


@router.get("/gpus")
async def gpus():
    try:
        return {"gpus": [g.public() for g in await asyncio.to_thread(manager.provider().gpus)]}
    except ProviderError as e:
        raise _fail(e) from e


@router.get("/sets")
def sets():
    d = state.load()
    return {"sets": manager.manifest_sets(), "storage_price": PROVIDERS[d["provider"]].storage_price}


class DeployReq(BaseModel):
    gpu_id: str
    sets: list[str]
    auto_stop: int = 15
    hf_token: str = ""
    accept_h3: bool = False   # the MiniMax H3 licence statement, ticked in the panel


@router.post("/deploy")
async def deploy(req: DeployReq):
    _idle()
    h3_licence.require(req.sets, req.accept_h3)
    try:
        await manager.deploy(req.gpu_id, req.sets, max(0, req.auto_stop), req.hf_token.strip())
    except ProviderError as e:
        raise _fail(e) from e
    return manager.status()


async def _do(fn) -> dict:
    try:
        await fn()
    except (ProviderError, EngineError) as e:
        raise _fail(e) from e
    await manager.refresh()
    return manager.status()


@router.post("/start")
async def start():
    return await _do(manager.start)


@router.post("/stop")
async def stop():
    _idle()
    return await _do(manager.stop)


@router.post("/delete")
async def delete():
    _idle()
    return await _do(manager.delete)


class GpuReq(BaseModel):
    gpu_id: str


@router.post("/gpu")
async def swap_gpu(req: GpuReq):
    _idle()
    return await _do(lambda: manager.swap_gpu(req.gpu_id))


@router.post("/update")
async def update():
    _idle()
    return await _do(manager.update)


@router.post("/keepalive")
async def keepalive():
    return await _do(manager.keepalive)


class AutoStopReq(BaseModel):
    minutes: int


@router.post("/deploy/cancel")
def deploy_cancel():
    manager.cancel_wait()
    return manager.status()


class MoveReq(BaseModel):
    gpu_id: str
    hf_token: str = ""


@router.post("/move")
async def move(req: MoveReq):
    _idle()
    try:
        await manager.move(req.gpu_id, req.hf_token.strip())
    except ProviderError as e:
        raise _fail(e) from e
    return manager.status()


@router.put("/auto_stop")
def auto_stop(req: AutoStopReq):
    d = state.load()
    d["auto_stop"] = max(0, req.minutes)
    state.save(d)
    return manager.status()


class ModelsReq(BaseModel):
    sets: list[str]
    hf_token: str = ""
    accept_h3: bool = False


@router.post("/models")
async def add_models(req: ModelsReq):
    h3_licence.require(req.sets, req.accept_h3)
    return await _do(lambda: manager.add_models(req.sets, req.hf_token.strip()))
