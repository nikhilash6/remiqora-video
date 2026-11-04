"""ENGINE=remote: the worker on another machine (cloud/worker_server.py on a RunPod pod) - the same interface as
worker.Worker (ensure, run, free, interrupt, stop, state, lora_names, object_info), so jobs.py, picture.py and facefix.py
don't care where the GPU is.

A graph's files cross the network both ways: before a run the files its LoadImage / LoadVideo / LoadAudio name are sent
from COMFY_INPUT (only those the server doesn't have yet, by sha256), after it everything the graph saved is fetched
into COMFY_OUTPUT under the same names - so output_files() and the rest of the app find them where they always were.
The events (executing, progress, done...) are h3worker.py's own, relayed over a WebSocket.

The weights and LoRAs live on the server (its volume): lora_names() and the diffusion models' list are the server's.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import quote

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .. import config, i18n
from .errors import EngineError, Interrupted
from .worker import START_TIMEOUT, _worker_message

LOADERS = {"LoadImage": "image", "LoadVideo": "file", "LoadAudio": "audio"}   # node -> its input naming a COMFY_INPUT file
# RunPod's proxy (Cloudflare) answers 403 to Python's own User-Agent ("Python-urllib/3.13"): every request says who it is
CHUNK = 32 << 20   # bytes of a big file per request
USER_AGENT = "RemiqoraVideo (+https://remiqora.com)"
# app/cloud's «start the rented server if it is stopped, and wait for it» - before every connection (a shoot, the writer)
WAKE: Optional[Callable[[Callable[[str], None]], Awaitable[None]]] = None


_PINNED: dict = {"pem": None, "ctx": None}


def pinned_context(cert_pem: str) -> ssl.SSLContext:
    """A context that trusts exactly one certificate - the self-signed one the studio made for a server without an
    https proxy (app/cloud/tls.py). OpenSSL checks the handshake against it before a byte of ours (the token) goes
    out: another certificate on that address, even one with the same name, is refused. No CA, no host name: the
    address is a shared IP and a random port."""
    if _PINNED["pem"] != cert_pem:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=cert_pem)
        ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)   # the pinned leaf is the whole chain
        _PINNED.update(pem=cert_pem, ctx=ctx)
    return _PINNED["ctx"]


def ssl_for(url: str) -> Optional[ssl.SSLContext]:
    """The pinned context when the record has a certificate and the address is https; None = the usual CA check."""
    return pinned_context(config.REMOTE_CERT) if config.REMOTE_CERT and url.lower().startswith(("https:", "wss:")) else None


def urlopen(req: urllib.request.Request, timeout: float, cert_pem: str = ""):
    """urllib with the pinned certificate when there is one (also app/cloud/manager.py's /health asks)."""
    ctx = pinned_context(cert_pem) if cert_pem and req.full_url.lower().startswith("https:") else None
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req, timeout=timeout)


def http(method: str, path: str, data=None, headers: Optional[dict] = None, timeout: float = 600):
    """A request to the cloud server with its token (the worker's files, the writer's calls)."""
    req = urllib.request.Request(config.REMOTE_URL + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {config.REMOTE_TOKEN}", "User-Agent": USER_AGENT, **(headers or {})})
    try:
        return urlopen(req, timeout, config.REMOTE_CERT)
    except (urllib.error.URLError, OSError) as e:
        raise EngineError(i18n.L(f"Облачный сервер: {method} {path} не вышел: {e}", f"The cloud server: {method} {path} failed: {e}")) from e


def http_json(method: str, path: str, body: Optional[dict] = None, timeout: float = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    with http(method, path, data, {"Content-Type": "application/json"} if data else None, timeout) as r:
        return json.loads(r.read())


# ----- the tools (tools/lyrics on the server: the song's words, a scene's voice) -----
def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def upload(path: Path) -> str:
    """A file into the server's input folder, named by its hash (the same song twice goes once); -> that name."""
    digest = sha256_of(path)
    name = f"tool-{digest[:24]}{path.suffix.lower()}"
    if http_json("POST", "/input/missing", {name: digest})["missing"]:
        with open(path, "rb") as f:
            http("PUT", "/input/" + quote(name), f, {"Content-Length": str(path.stat().st_size), "X-Sha256": digest,
                                                     "Content-Type": "application/octet-stream"}).close()
    return name


def fetch(rel: str, dest: Path) -> None:
    """An /output file of the server into dest, removed there."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with http("GET", "/output/" + quote(rel) + "?delete=1") as r, open(part, "wb") as out:
        while block := r.read(1 << 20):
            out.write(block)
    os.replace(part, dest)


async def tool(src: Path, out: Path, opts: dict, on_message: Callable[[str], None]) -> tuple[int, list[str], list[str]]:
    """lyrics_worker.py on the server: src goes there, the run is started and asked about until it ends (each ask is
    held up to 50 s there: RunPod's proxy cuts a request at ~100 s), its files (vocals.wav, lyrics.json) come into
    out. The same answer as song._tool: (exit code, errors, the last lines). Cancelled - the run is killed there."""
    if WAKE is not None:
        await WAKE(on_message)
    on_message(i18n.L("Отправляю файл в облако…", "Sending the file to the cloud…"))
    name = await asyncio.to_thread(upload, src)
    tid = (await asyncio.to_thread(http_json, "POST", "/tools/run", {"tool": "lyrics", "input": name, **opts}))["id"]
    try:
        last = ""
        while True:
            r = await asyncio.to_thread(http_json, "GET", f"/tools/{tid}", None, 120)
            if r.get("message") and r["message"] != last:
                last = r["message"]
                on_message(last)
            if r.get("done"):
                break
        out.mkdir(parents=True, exist_ok=True)
        for rel in r.get("files") or []:
            name = Path(str(rel).replace("\\", "/")).name   # only a plain name from the server lands in out
            if name in ("", ".", ".."):
                raise EngineError(i18n.L(f"Сервер вернул файл с недопустимым именем: {rel}", f"The server returned a file with a bad name: {rel}"))
            await asyncio.to_thread(fetch, rel, out / name)
        return int(r.get("code") if r.get("code") is not None else -1), list(r.get("error") or []), list(r.get("tail") or [])
    finally:   # done, failed or cancelled (the job stopped): the run and its folder are gone from the server
        await asyncio.shield(asyncio.to_thread(_drop, tid))


def _drop(tid: str) -> None:
    try:
        http("DELETE", f"/tools/{tid}", timeout=30).close()
    except EngineError:
        pass


class _Dropped(EngineError):
    """The link closed before the server answered anything about a run: nothing ran, it can be sent again."""


class RemoteWorker:
    def __init__(self) -> None:
        self.ws: Optional[ClientConnection] = None
        self._lock = asyncio.Lock()     # connect / stop
        self._talk = asyncio.Lock()     # one request at a time
        self._events: Optional[asyncio.Queue] = None
        self._reader: Optional[asyncio.Task] = None
        self._hashes: dict[tuple, str] = {}   # (path, size, mtime) -> sha256: a scene's frames are sent once
        self.started_at: Optional[float] = None
        self.remote_pid: Optional[int] = None
        self.warm = False
        self.model = "h3"
        self.folders = None   # the server reads its own folders: jobs.py never restarts us for a local change
        self.stats_pid = None   # no local process holds the models: the header's meters show none

    # ----- state -----
    @property
    def pid(self) -> Optional[int]:
        """Truthy while the remote worker is running (jobs.py asks `COMFY.pid` for "is it up")."""
        return self.remote_pid if self.ws is not None and self.started_at else None

    def problems(self) -> list[str]:
        out = []
        if not config.REMOTE_URL:
            out.append(i18n.L("ENGINE=remote, но не задан REMOTE_URL (адрес облачного воркера) в .env", "ENGINE=remote, but REMOTE_URL (the cloud worker's address) is not set in .env"))
        if not config.REMOTE_TOKEN:
            out.append(i18n.L("ENGINE=remote, но не задан REMOTE_TOKEN в .env", "ENGINE=remote, but REMOTE_TOKEN is not set in .env"))
        return out

    def state(self) -> str:
        """off | starting | ready"""
        if self.ws is None:
            return "off"
        return "ready" if self.started_at else "starting"

    # ----- the connection -----
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {config.REMOTE_TOKEN}"}

    async def _read(self, ws: ClientConnection, q: asyncio.Queue) -> None:
        reason = ""
        try:
            async for m in ws:
                try:
                    q.put_nowait(json.loads(m))
                except ValueError:
                    continue
        except WebSocketException as e:
            reason = str(e)
        q.put_nowait({"event": "exit", "log": reason or (ws.close_reason or "")})

    async def _send(self, msg: dict) -> None:
        if self.ws is None:
            raise EngineError(i18n.L("Облачный воркер не подключён", "The cloud worker is not connected"))
        try:
            await self.ws.send(json.dumps(msg, ensure_ascii=False))
        except WebSocketException as e:
            raise EngineError(i18n.L(f"Связь с облачным воркером оборвалась: {e}", f"The connection to the cloud worker broke: {e}")) from e

    async def _next(self, timeout: Optional[float] = None) -> dict:
        try:
            msg = await asyncio.wait_for(self._events.get(), timeout)
        except asyncio.TimeoutError as e:
            raise EngineError(i18n.L("Облачный воркер не ответил", "The cloud worker did not answer")) from e
        if msg.get("event") == "exit":
            self.ws, self.started_at, self.warm = None, None, False
            raise EngineError(i18n.L("Облачный воркер отключился. ", "The cloud worker went away. ") + str(msg.get("log") or "").strip())
        return msg

    # ----- start / stop -----
    def _dropped(self) -> bool:
        """The link is gone though we think we are connected (RunPod's proxy drops a quiet WebSocket): the worker on the
        server lives on with its weights - a new connection finds it."""
        return self.ws is not None and self._reader is not None and self._reader.done()

    async def ensure(self, on_status: Callable[[str], None] = lambda s: None) -> None:
        async with self._lock:
            if self.ws is not None and self.started_at and not self._dropped():
                return
            if issues := self.problems():
                raise EngineError("; ".join(issues))
            await self._close()
            if WAKE is not None:
                await WAKE(on_status)
            on_status(i18n.L("Подключаюсь к облачному H3…", "Connecting to the cloud H3…"))
            url = "ws" + config.REMOTE_URL[4:] + "/ws" if config.REMOTE_URL.startswith("http") else config.REMOTE_URL + "/ws"
            try:
                self.ws = await connect(url, additional_headers=self._headers(), user_agent_header=USER_AGENT, max_size=1 << 26, open_timeout=30,
                                        ssl=ssl_for(url))
            except (OSError, WebSocketException, asyncio.TimeoutError) as e:
                self.ws = None
                raise EngineError(i18n.L(f"Не подключиться к облачному воркеру {config.REMOTE_URL}: {e}", f"Cannot connect to the cloud worker {config.REMOTE_URL}: {e}")) from e
            self._events = asyncio.Queue()
            self._reader = asyncio.create_task(self._read(self.ws, self._events))
            t0 = time.time()
            while True:
                msg = await self._next(max(1.0, START_TIMEOUT + 60 - (time.time() - t0)))
                if msg.get("event") == "starting":
                    on_status(i18n.L("Запускаю H3 в облаке…", "Starting H3 in the cloud…"))
                elif msg.get("event") == "ready":
                    self.started_at, self.remote_pid = time.time(), msg.get("pid") or 1
                    return

    async def _close(self) -> None:
        ws, self.ws, self.started_at, self.warm = self.ws, None, None, False
        if ws is not None:
            try:
                await ws.close()
            except WebSocketException:
                pass

    async def stop(self) -> None:
        """The remote worker ends (the pod's GPU is free; the next ensure() starts it again and it re-reads its folders)."""
        if self.ws is not None:
            try:
                async with self._talk:
                    await self._send({"cmd": "stop"})
                    while (await self._next(30)).get("event") != "stopped":
                        pass
            except EngineError:
                pass
        await self._close()

    async def free(self) -> None:
        self.warm = False
        if not (self.ws is not None and self.started_at):
            return
        for attempt in (1, 2):
            if self._dropped():   # the weights on the server are still in its memory: connect again to free them
                await self.ensure()
            try:
                async with self._talk:
                    await self._send({"cmd": "free"})
                    while (await self._next(120)).get("event") != "freed":
                        pass
                return
            except EngineError:
                if attempt == 2 or self.ws is not None:   # not a dropped link: a real error
                    raise
                await self.ensure()   # RunPod's proxy closed the link (1002): once more on a new one

    async def interrupt(self) -> None:
        if self.ws is not None:
            try:
                await self._send({"cmd": "interrupt"})
            except EngineError:
                pass

    async def _list(self, cmd: dict) -> list[str]:
        await self.ensure()
        async with self._talk:
            await self._send(cmd)
            while True:
                msg = await self._next(60)
                if msg.get("event") == "list":
                    return list(msg.get("names") or [])

    async def lora_names(self) -> list[str]:
        return await self._list({"cmd": "loras"})

    async def object_info(self, node: str) -> dict:
        if node != "UNETLoader":
            return {}
        names = await self._list({"cmd": "models", "kind": "diffusion_models"})
        return {"input": {"required": {"unet_name": [names]}}}

    # ----- files -----
    def _sha(self, p: Path) -> str:
        st = p.stat()
        key = (str(p), st.st_size, st.st_mtime_ns)
        if key not in self._hashes:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            self._hashes[key] = h.hexdigest()
        return self._hashes[key]

    def _http(self, method: str, path: str, data=None, headers: Optional[dict] = None, timeout: float = 600):
        return http(method, path, data, headers, timeout)

    def _send_inputs(self, graph: dict) -> None:
        names = {n["inputs"][k] for n in graph.values() if (k := LOADERS.get(n.get("class_type")))
                 and isinstance(n.get("inputs", {}).get(k), str)}
        files = {n: config.COMFY_INPUT / n for n in names if (config.COMFY_INPUT / n).is_file()}
        if not files:
            return
        want = {n: self._sha(p) for n, p in files.items()}
        with self._http("POST", "/input/missing", json.dumps(want).encode(), {"Content-Type": "application/json"}, 120) as r:
            missing = json.loads(r.read())["missing"]
        for n in missing:
            p = files[n]
            with open(p, "rb") as f:
                self._http("PUT", "/input/" + quote(n), f, {"Content-Length": str(p.stat().st_size), "X-Sha256": want[n],
                                                           "Content-Type": "application/octet-stream"}).close()

    @staticmethod
    def _lora_files(graph: dict) -> dict[str, Path]:
        """The LoRA files the graph names (LoraLoader / LoraLoaderModelOnly ...) that lie in a local LoRA folder."""
        dirs = [d for k in ("LORA_DIRS", "LTX23_LORA_DIRS", "LTX25_LORA_DIRS", "WAN_LORA_DIRS", "KREA_LORA_DIRS", "FLUX2_LORA_DIRS")
                for d in getattr(config, k, [])]
        out: dict[str, Path] = {}
        for n in graph.values():
            name = n.get("inputs", {}).get("lora_name") if "Lora" in str(n.get("class_type")) else None
            if not isinstance(name, str) or not name.lower().endswith(".safetensors"):
                continue
            rel = name.replace("\\", "/")
            if rel not in out and (p := next((d / rel for d in dirs if (d / rel).is_file()), None)):
                out[rel] = p
        return out

    def _send_loras(self, graph: dict, on_status: Callable[[str], None] = lambda s: None) -> None:
        """The user's own LoRA the server lacks (by name and size) go up, one streamed PUT each."""
        files = self._lora_files(graph)
        if not files:
            return
        want = {n: p.stat().st_size for n, p in files.items()}
        with self._http("POST", "/loras/missing", json.dumps(want).encode(), {"Content-Type": "application/json"}, 120) as r:
            missing = json.loads(r.read())["missing"]
        for i, n in enumerate(missing, 1):
            p = files[n]
            on_status(i18n.L(f"Отправляю LoRA в облако ({i}/{len(missing)}): {p.name}", f"Sending the LoRA to the cloud ({i}/{len(missing)}): {p.name}"))
            self._put_chunks("/loras/" + quote(n), p)

    def _put_chunks(self, path: str, p: Path) -> None:
        """A big file in CHUNK pieces, each its own short request (RunPod's proxy cuts one at ~100 s). After a failed
        piece the server says how much it has, and the sending goes on from there (5 failures in a row: an error)."""
        size, sha, off, fails = p.stat().st_size, self._sha(p), 0, 0
        with open(p, "rb") as f:
            while True:
                f.seek(off)
                block = f.read(CHUNK)
                last = off + len(block) >= size
                try:
                    self._http("PUT", f"{path}?offset={off}" + ("&done=1" if last else ""), block,
                               {"Content-Length": str(len(block)), "X-Sha256": sha, "Content-Type": "application/octet-stream"}, 300).close()
                except EngineError:
                    fails += 1
                    if fails >= 5:
                        raise
                    time.sleep(2 * fails)
                    with self._http("GET", path) as r:
                        off = min(int(json.loads(r.read())["size"]), off)   # a piece may have landed: never skip ahead
                    continue
                if last:
                    return
                off += len(block)
                fails = 0

    def _fetch_outputs(self, outputs: dict) -> None:
        for v in (x for node in outputs.values() for x in node.values()):
            for f in v if isinstance(v, list) else []:
                if not (isinstance(f, dict) and f.get("filename") and f.get("type", "output") == "output"):
                    continue
                rel = "/".join(x for x in (f.get("subfolder", ""), f["filename"]) if x)
                dest = (config.COMFY_OUTPUT / rel).resolve()
                # the server names the file: it must land inside our output folder, whatever the server says
                if "\\" in rel or ":" in rel or not dest.is_relative_to(config.COMFY_OUTPUT.resolve()):
                    raise EngineError(i18n.L(f"Сервер вернул файл с недопустимым путём: {rel}", f"The server returned a file with a bad path: {rel}"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(dest.name + ".part")
                with self._http("GET", "/output/" + quote(rel) + "?delete=1") as r, open(part, "wb") as out:
                    while block := r.read(1 << 20):
                        out.write(block)
                os.replace(part, dest)

    # ----- running a graph -----
    async def run(self, graph: dict, on_node: Callable[[str], None], on_progress: Callable[[int, int, str], None]) -> dict:
        try:
            return await self._run_once(graph, on_node, on_progress)
        except _Dropped:   # the link went before the server took the graph: a new link, the graph again
            return await self._run_once(graph, on_node, on_progress)

    async def _run_once(self, graph: dict, on_node: Callable[[str], None], on_progress: Callable[[int, int, str], None]) -> dict:
        await self.ensure()
        rid = uuid.uuid4().hex
        async with self._talk:
            await asyncio.to_thread(self._send_inputs, graph)
            await asyncio.to_thread(self._send_loras, graph)
            await self._send({"cmd": "run", "id": rid, "graph": graph})
            heard = False
            while True:
                try:
                    msg = await self._next()
                except EngineError as e:
                    if not heard and self.ws is None:
                        raise _Dropped(str(e)) from e
                    raise
                heard = True
                ev = msg.get("event")
                if ev == "executing":
                    on_node(str(msg.get("node")))
                elif ev == "progress":
                    on_progress(int(msg.get("value", 0)), int(msg.get("max", 0)), str(msg.get("node")))
                elif msg.get("id") not in (None, rid):
                    continue
                elif ev == "done":
                    outputs = msg.get("outputs") or {}
                    await asyncio.to_thread(self._fetch_outputs, outputs)
                    return outputs
                elif ev == "interrupted":
                    raise Interrupted(i18n.L("остановлено", "stopped"))
                elif ev == "error":
                    raise EngineError(_worker_message(msg.get("message")) or i18n.L("ошибка облачного воркера", "cloud worker error"))
