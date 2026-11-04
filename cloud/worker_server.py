"""The cloud side of ENGINE=remote: our H3 worker (app/engine/h3worker.py, unchanged) behind a small HTTP server, so the
studio on the user's machine shoots on a GPU somewhere else (a RunPod pod). The studio keeps everything else: the
projects, the writer, the montage, ffmpeg, RTX Video, RIFE.

Runs in the engine's python (aiohttp is one of ComfyUI's own packages):
    python cloud/worker_server.py            settings from the environment, see below

  GET  /health                      {"worker", "llm", "version", "busy", "idle", "models"} (no token: the pod's health
                                    check and the studio's wait for a starting pod; "models" - the download's progress)
  GET  /models/files                the weights on the volume: {"files": [{"path": "diffusion_models/x.safetensors",
                                    "size"}]} under the MODELS folders (?sub=loras - one subfolder; anything that
                                    leaves MODELS is refused with 400, symlinks out of it are skipped)
  POST /models/download             {"sets": "h3,krea", "hf_token"?} -> the sets fetched onto the volume (in the background)
  POST /input/missing               {"name": sha256, ...} -> {"missing": [names]}: what the studio has to send first
  PUT  /input/<name>                the file's bytes (X-Sha256: its hash) -> the worker's input folder
  GET  /output/<subfolder/name>     a file the graph saved; ?delete=1 removes it after sending
  GET  /ws                          WebSocket: h3worker.py's own protocol, one JSON per message both ways, plus
                                    {"cmd": "stop"} (the worker ends, the GPU is free) and {"cmd": "restart"} (the
                                    worker reads its weight folders again: a LoRA put on the volume)
  GET  /llm/models                  the writer's GGUFs on the volume (<models>/llm), each with its mmproj if any
  POST /llm/start                   {"model": rel path, "ctx"} -> llama-server up with it (the worker should be freed
                                    first: the studio does that, as on its own machine); POST /llm/stop
  POST /llm/call                    {"path": "/v1/chat/completions", "body"} -> {"id"}: the request runs here, the studio
  GET  /llm/call/<id>               asks for the answer (each ask waits up to 50 s) - RunPod's proxy cuts any request
                                    after 100 s, a long answer must not depend on one
  POST /tools/run                   {"tool": "lyrics", "input": an /input name, "model"?, "language"?, "vocals_only"?}
                                    -> {"id"}: tools/lyrics/lyrics_worker.py on that file (the song's words, a scene's
                                    voice - Demucs, Whisper) in the engine's environment, the GPU to itself (the studio
                                    frees the worker and stops the writer first, as on its own machine)
  GET  /tools/<id>                  {"done", "message", "code", "error", "tail", "files"}: waits up to 50 s for the end
                                    or a new message; "files" are /output paths (fetch them with ?delete=1)
  DELETE /tools/<id>                the run ends (killed when still going) and its folder is gone

Every request but /health carries `Authorization: Bearer <REMOTE_TOKEN>`. One studio at a time: a second WebSocket
takes the worker over from the first. The worker outlives the connection, so the models stay in memory between a
studio's restarts.

Environment:
  REMOTE_TOKEN      required
  REMOTE_HOST/PORT  0.0.0.0 / 8188
  MODELS            the weights folders, separated by os.pathsep (each with diffusion_models, text_encoders, vae,
                    loras, checkpoints, latent_upscale_models - the layout install_models.py makes); /workspace/models
  WORK              the worker's input / output and its log; /workspace/work
  COMFY_DIR         the folder with ComfyUI (the core) in it; engine/ beside this repo
  WORKER_PYTHON     the python with torch for the worker; <COMFY_DIR>/.venv's
  WORKER_SCRIPT     the worker itself; app/engine/h3worker.py (tools/test_remote.py puts a stand-in here)
  LLAMA_BIN         llama-server for the writer; /opt/llama/llama-server
  COMFY_ARGS        more of ComfyUI's options for the worker; "--reserve-vram 1.5"
  IMAGE_VERSION     the image's version (the studio compares it with its own); dev
  MODEL_SETS        the model sets fetched at start (install/manifest.json's ids, separated by commas); HF_TOKEN for
                    the gated ones (LTX 2.5)
  FAST_MODELS       a folder on the pod's own NVMe: the weights are copied there from the network volume after each
                    start and read from it first (the volume reads a 20 GB model in 30 s - 2.5 min, a 24-32 GB card
                    swaps H3 and its text encoder every scene); empty = off
  IDLE_STOP_MIN     the pod stops itself after this many minutes without work (0 = never) - the safety net when the
                    studio is closed; needs RUNPOD_POD_ID and RUNPOD_API_KEY (RunPod sets both; the key acts on this pod only)
  TOOL_SCRIPT       the lyrics tool; tools/lyrics/lyrics_worker.py (tools/test_remote.py puts a stand-in here)
  TOOLS_CACHE       where the tools keep the weights they download (Whisper's 3 GB, Demucs'): <MODELS[0]>/tools - on the
                    volume, so a restarted pod has them
  TLS               1: https / wss instead of plain http - for a provider without an https proxy (Vast.ai: a random
                    port on a shared IP). TLS_CERT / TLS_KEY: the certificate and key as base64 PEM, made by the
                    studio (app/cloud/tls.py) and pinned there - the usual way; without them the server makes its own
                    with openssl into <WORK>/tls (kept across restarts) and /health shows its sha256 fingerprint
                    ("tls"), for a studio that pins it by hand
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import urllib.error
import urllib.request
import uuid

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(os.environ.get("WORKER_SCRIPT", str(ROOT / "app" / "engine" / "h3worker.py")))
TOKEN = os.environ.get("REMOTE_TOKEN", "")
HOST = os.environ.get("REMOTE_HOST", "0.0.0.0")
PORT = int(os.environ.get("REMOTE_PORT", "8188"))
MODELS = [Path(p) for p in os.environ.get("MODELS", "/workspace/models").split(os.pathsep) if p]
WORK = Path(os.environ.get("WORK", "/workspace/work"))
COMFY_DIR = Path(os.environ.get("COMFY_DIR", str(ROOT / "engine")))
INPUT, OUTPUT, LOG = WORK / "input", WORK / "output", WORK / "h3worker.log"
START_TIMEOUT = 300
# the same kinds and sub-folders as app/engine/worker.py
KINDS = {"diffusion_models": ("diffusion_models", "unet"), "text_encoders": ("text_encoders", "clip"),
         "vae": ("vae",), "loras": ("loras",), "checkpoints": ("checkpoints",),
         "latent_upscale_models": ("latent_upscale_models",)}
NAME = re.compile(r"^[\w .()+,=-]{1,200}$")   # an input's name: one file, no folders
LLAMA_BIN = Path(os.environ.get("LLAMA_BIN", "/opt/llama/llama-server"))
LLM_PORT = 8191
VERSION = os.environ.get("IMAGE_VERSION", "dev")
# ComfyUI's own options for the worker: some video memory kept free, so its "does it fit" estimate never runs to the
# edge of a 24 GB card (2026-11-01: an OOM in the sampler on the 5th scene of a run on an RTX 4090)
COMFY_ARGS = os.environ.get("COMFY_ARGS", "--reserve-vram 1.5").split()
IDLE_STOP_MIN = float(os.environ.get("IDLE_STOP_MIN", "0") or 0)
STUCK_MIN = 60   # busy with no request at all this long: the studio is gone, the run is stuck
# the worker and llama-server never see the tokens (a model file or a node can't read them out)
SECRETS = ("REMOTE_TOKEN", "RUNPOD_API_KEY", "SELF_STOP_KEY", "HF_TOKEN", "TLS_KEY")
TLS = os.environ.get("TLS", "") in ("1", "true", "yes")
TLS_FP = ""   # the served certificate's sha256 (hex), for /health


def child_env(**extra: str) -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if k not in SECRETS}, **extra}
TOOL_SCRIPT = Path(os.environ.get("TOOL_SCRIPT", str(ROOT / "tools" / "lyrics" / "lyrics_worker.py")))
TOOLS_CACHE = Path(os.environ.get("TOOLS_CACHE") or (MODELS[0] / "tools"))
TOOL_ID = re.compile(r"^[0-9a-f]{32}$")
# what counts as work: any request with the token, the worker's events, a graph or a writer's call running
ACTIVITY = {"last": time.time(), "runs": 0}


def touch() -> None:
    ACTIVITY["last"] = time.time()


FAST = Path(os.environ["FAST_MODELS"]) if os.environ.get("FAST_MODELS") else None
WEIGHTS = {".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft"}


def roots() -> list[Path]:
    """The weights folders in the order they are read: the fast copy first, the volume behind it (a file not copied
    yet, or that did not fit, is read from the volume)."""
    return ([FAST] if FAST else []) + MODELS


def folders() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    for root in roots():
        for kind, subs in KINDS.items():
            out[kind] += [str(root / s) for s in subs if (root / s).is_dir()]
    return out


class FastCopy:
    """After each start (and each download) the volume's weights go to FAST, the video models first. ComfyUI looks a
    file up in its folders at every load, so a model copied while the worker runs is read from FAST next time."""
    ORDER = ("diffusion_models", "text_encoders", "vae", "latent_upscale_models", "loras", "checkpoints", "llm")
    RESERVE = 4 << 30   # the image, the work folder and the logs live on the same disk

    def __init__(self) -> None:
        self.state: dict = {"state": "off"}
        self.task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if FAST is None or not MODELS or (self.task is not None and not self.task.done()):
            return
        for kind in self.ORDER:   # the folders exist before the worker can start: its list holds them before any copy
            (FAST / KINDS.get(kind, (kind,))[0]).mkdir(parents=True, exist_ok=True)
        self.task = asyncio.create_task(self._run())

    def _plan(self) -> list[tuple[Path, Path, int]]:
        out = []
        for i, kind in enumerate(self.ORDER):
            base = MODELS[0] / kind
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*"), key=lambda p: -p.stat().st_size if p.is_file() else 0):
                if p.is_file() and p.suffix.lower() in WEIGHTS and not p.name.startswith("."):
                    dest = FAST / p.relative_to(MODELS[0])
                    size = p.stat().st_size
                    if not (dest.is_file() and dest.stat().st_size == size):
                        out.append((p, dest, size))
        return out

    def _copy(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f".{dest.name}.part")
        try:
            with open(src, "rb") as f, open(part, "wb") as g:
                while block := f.read(64 << 20):
                    g.write(block)
                    self.state["done"] += len(block)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)

    async def _run(self) -> None:
        try:
            plan = await asyncio.to_thread(self._plan)
            self.state = {"state": "copying", "done": 0, "total": sum(s for _, _, s in plan), "file": None, "skipped": 0}
            for src, dest, size in plan:
                if shutil.disk_usage(FAST).free - size < self.RESERVE:   # does not fit: read from the volume
                    self.state["skipped"] += 1
                    self.state["total"] -= size
                    continue
                self.state["file"] = src.name
                t0 = time.time()
                await asyncio.to_thread(self._copy, src, dest)
                print(f"{time.strftime('%H:%M:%S')} fast copy: {src.name} {size / 2**30:.1f} GB in {time.time() - t0:.0f} s", flush=True)
            self.state.update(state="ready", file=None)
        except Exception as e:  # noqa: BLE001 - /health shows it; the volume still serves every file
            self.state.update(state="failed", error=f"{type(e).__name__}: {e}")


FAST_COPY = FastCopy()


def python() -> Path:
    if os.environ.get("WORKER_PYTHON"):
        return Path(os.environ["WORKER_PYTHON"])
    v = COMFY_DIR / ".venv"
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Worker:
    """h3worker.py as a child process; its events go to whichever WebSocket holds it now."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.ready = False
        self.ws: Optional[web.WebSocketResponse] = None
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    def state(self) -> str:
        if self.proc and self.proc.returncode is None:
            return "ready" if self.ready else "starting"
        return "off"

    async def emit(self, msg: dict) -> None:
        ws = self.ws
        if ws is not None and not ws.closed:
            try:
                await ws.send_str(json.dumps(msg, ensure_ascii=False))
            except (ConnectionError, RuntimeError):
                pass

    async def ensure(self) -> bool:
        """The worker running and ready; True when it was started now (its own "ready" went to the WebSocket)."""
        async with self._lock:
            if self.state() != "off":
                await asyncio.wait_for(self._ready.wait(), START_TIMEOUT)
                return False
            for d in (INPUT, OUTPUT):
                d.mkdir(parents=True, exist_ok=True)
            sfile = WORK / "h3worker.json"
            sfile.write_text(json.dumps({"input": str(INPUT), "output": str(OUTPUT), "folders": folders(), "args": COMFY_ARGS}, indent=1),
                             encoding="utf-8")
            self.ready = False
            self._ready.clear()
            log = open(LOG, "w", encoding="utf-8", errors="replace")
            self.proc = await asyncio.create_subprocess_exec(
                str(python()), "-s", str(SCRIPT), str(sfile), cwd=str(COMFY_DIR / "ComfyUI"),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                env=child_env(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"), limit=1 << 26)
            log.close()
            asyncio.create_task(self._read(self.proc))
            await self.emit({"event": "starting"})
            await asyncio.wait_for(self._ready.wait(), START_TIMEOUT)
            return True

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        while line := await proc.stdout.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "ready":
                self.ready = True
                self._ready.set()
            elif msg.get("event") in ("done", "error", "interrupted"):
                ACTIVITY["runs"] = max(0, ACTIVITY["runs"] - 1)
            touch()
            await self.emit(msg)
        await proc.wait()
        ACTIVITY["runs"] = 0
        if proc is self.proc:
            self.ready = False
            await self.emit({"event": "exit", "log": log_tail()})

    def send(self, msg: dict) -> None:
        if self.state() == "off":
            raise RuntimeError("the worker is not running")
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

    async def stop(self) -> None:
        p, self.proc, self.ready = self.proc, None, False
        if p and p.returncode is None:
            try:
                p.stdin.close()   # the worker leaves its loop on EOF
                await asyncio.wait_for(p.wait(), 10)
            except (OSError, asyncio.TimeoutError):
                p.kill()


def log_tail(n: int = 25) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


WORKER = Worker()


class Llama:
    """The writer: llama-server with a GGUF from the volume, one at a time, started and stopped by the studio."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.model: Optional[str] = None
        self.calls: dict[str, tuple[float, asyncio.Task]] = {}   # id -> (started, the call)

    def state(self) -> str:
        return "ready" if self.proc and self.proc.returncode is None else "off"

    @staticmethod
    def models() -> list[dict]:
        out = []
        for root in MODELS:
            base = root / "llm"
            if not base.is_dir():
                continue
            ggufs = sorted(base.rglob("*.gguf"), key=lambda q: str(q).lower())
            for p in ggufs:
                if "mmproj" in p.name.lower():
                    continue
                proj = next((q for q in ggufs if q.parent == p.parent and "mmproj" in q.name.lower()), None)
                out.append({"rel": p.relative_to(root).as_posix(), "name": p.stem, "bytes": p.stat().st_size,
                            "mmproj": proj.relative_to(root).as_posix() if proj else None})
        return out

    @staticmethod
    def find(rel: str) -> Optional[Path]:
        for root in roots():
            p = (root / rel).resolve()
            if p.is_relative_to(root.resolve()) and p.is_file():
                return p
        return None

    async def start(self, rel: str, ctx: int) -> None:
        if self.state() == "ready" and self.model == rel:
            return
        await self.stop()
        path = self.find(rel)
        if path is None:
            raise web.HTTPNotFound(text=f"no model {rel}")
        m = next((x for x in self.models() if x["rel"] == rel), {})
        cmd = [str(LLAMA_BIN), "-m", str(path), "--host", "127.0.0.1", "--port", str(LLM_PORT), "-c", str(ctx), "-ngl", "99",
               "-np", "1", "--jinja", "--no-webui"]
        if m.get("mmproj"):
            cmd += ["--mmproj", str(self.find(m["mmproj"]))]
        log = open(WORK / "llama-server.log", "w", encoding="utf-8", errors="replace")
        env = child_env(LD_LIBRARY_PATH=os.pathsep.join([str(LLAMA_BIN.parent), os.environ.get("LD_LIBRARY_PATH", "")]))
        self.proc = await asyncio.create_subprocess_exec(*cmd, stdout=log, stderr=log, env=env, cwd=str(LLAMA_BIN.parent))
        log.close()
        self.model = rel
        t0 = time.time()
        async with ClientSession(timeout=ClientTimeout(total=3)) as http:
            while time.time() - t0 < 180:
                if self.proc.returncode is not None:
                    break
                try:
                    async with http.get(f"http://127.0.0.1:{LLM_PORT}/health") as r:
                        if r.status == 200:
                            return
                except Exception:  # noqa: BLE001 - not listening yet
                    pass
                await asyncio.sleep(0.5)
        tail = (WORK / "llama-server.log").read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        await self.stop()
        raise web.HTTPInternalServerError(text="llama-server did not start:\n" + "\n".join(tail))

    async def stop(self) -> None:
        p, self.proc, self.model = self.proc, None, None
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 10)
            except asyncio.TimeoutError:
                p.kill()

    async def call(self, path: str, body: dict) -> dict:
        ACTIVITY["runs"] += 1
        try:
            async with ClientSession(timeout=ClientTimeout(total=1800)) as http:
                async with http.post(f"http://127.0.0.1:{LLM_PORT}{path}", json=body) as r:
                    return {"status": r.status, "body": await r.text()}
        finally:
            ACTIVITY["runs"] = max(0, ACTIVITY["runs"] - 1)
            touch()


LLAMA = Llama()


class Download:
    """tools/install_models.py --json in the background: the volume gets the sets, /health shows how far it is."""

    def __init__(self) -> None:
        self.state = {"state": "off"}
        self.task: Optional[asyncio.Task] = None

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, sets: str, hf_token: str = "") -> None:
        if self.busy() or not sets.strip():
            return
        self.task = asyncio.create_task(self._run(sets, hf_token))

    async def _run(self, sets: str, hf_token: str) -> None:
        self.state = {"state": "downloading", "sets": sets, "done": 0, "total": 0, "file": None, "failed": []}
        args = [sys.executable, "-I", str(ROOT / "tools" / "install_models.py"), "--models-dir", str(MODELS[0]), "--sets", sets,
                "--json", "--lang", "en",
                "--accept-h3"]   # the studio asked for the H3 licence statement before it sent these sets (app/cloud/api.py)
        if len(MODELS) > 1:   # the other weights folders: a file already there by name and size is not downloaded
            args += ["--find", ";".join(str(m) for m in MODELS[1:])]
        tok = None
        if hf_token:
            tok = WORK / ".hf_token"
            tok.write_text(hf_token, encoding="utf-8")
            os.chmod(tok, 0o600)
            args += ["--token-file", str(tok)]
        ACTIVITY["runs"] += 1   # downloading is work: the pod must not stop itself meanwhile
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            while line := await proc.stdout.readline():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("event")
                if kind == "plan":
                    self.state["total"] = ev.get("bytes") or ev.get("total") or self.state["total"]
                elif kind == "progress":
                    self.state.update(done=ev.get("overall", 0), total=ev.get("overallTotal", self.state["total"]), file=ev.get("name"))
                elif kind == "set" and ev.get("status") not in ("running", "done", "present", None):
                    self.state["failed"].append({"set": ev.get("id"), "code": ev.get("code"), "error": ev.get("error")})
                touch()
            await proc.wait()
            FAST_COPY.start()   # before «ready»: the studio starts the worker on it, and the fast folders must exist by then
            self.state.update(state="ready" if not self.state["failed"] else "partial", file=None)
            if WORKER.state() != "off":   # the worker reads its weight folders at start: new files need a restart
                await WORKER.stop()
        except Exception as e:  # noqa: BLE001 - shown by /health
            self.state.update(state="failed", error=f"{type(e).__name__}: {e}")
        finally:
            ACTIVITY["runs"] = max(0, ACTIVITY["runs"] - 1)
            if tok:
                tok.unlink(missing_ok=True)


DOWNLOAD = Download()


def kill_tree(proc: asyncio.subprocess.Process) -> None:
    """The process and its children (Demucs / Whisper spawn none, ffmpeg they do): Linux has no Job Objects or
    taskkill - the tool runs in its own session, so its process group is its tree."""
    if proc.returncode is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def tool_env() -> dict:
    """The tool's environment: its weight caches on the volume; on Linux CUDA 12's cuBLAS (a pip package in the engine's
    venv, for faster-whisper's CTranslate2) and torch's cuDNN where the loader finds them - the tool's own shim does
    that on Windows only (add_dll_directory)."""
    env = {**child_env(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
           "TORCH_HOME": str(TOOLS_CACHE / "torch"), "HF_HOME": str(TOOLS_CACHE / "hf")}
    if os.name != "nt":
        libs = [str(d) for d in python().parent.parent.glob("lib/python3.*/site-packages/nvidia/*/lib") if d.is_dir()]
        env["LD_LIBRARY_PATH"] = os.pathsep.join(libs + [os.environ.get("LD_LIBRARY_PATH", "")]).strip(os.pathsep)
    return env


class Tool:
    """One run of tools/lyrics/lyrics_worker.py (the song's words or a scene's voice): the process, what it said, its
    files in OUTPUT/tools/<id> (served by /output like a graph's)."""

    def __init__(self, tid: str, args: list[str]) -> None:
        self.id, self.args = tid, args
        self.out = OUTPUT / "tools" / tid
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.task: Optional[asyncio.Task] = None
        self.message, self.tail, self.error, self.code = "", [], [], None
        self.changed = asyncio.Event()   # a new message or the end: /tools/<id> wakes up

    def done(self) -> bool:
        return self.task is not None and self.task.done()

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        ACTIVITY["runs"] += 1
        try:
            TOOLS_CACHE.mkdir(parents=True, exist_ok=True)
            self.proc = await asyncio.create_subprocess_exec(
                str(python()), "-u", str(TOOL_SCRIPT), self.args[0], str(self.out), *self.args[1:],   # SONG OUT_DIR [options]
                cwd=str(TOOL_SCRIPT.parent), env=tool_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=os.name != "nt")
            while raw := await self.proc.stdout.readline():
                line = raw.decode("utf-8", errors="replace").rstrip()
                self.tail.append(line)
                del self.tail[:-20]
                touch()
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue   # library chatter
                if isinstance(msg, dict) and msg.get("error"):
                    self.error.append(str(msg["error"]))
                elif isinstance(msg, dict) and msg.get("message"):
                    self.message = str(msg["message"])
                    self.changed.set()
            self.code = await self.proc.wait()
        except Exception as e:  # noqa: BLE001 - the studio shows it
            self.error.append(f"{type(e).__name__}: {e}")
            self.code = -1
        finally:
            ACTIVITY["runs"] = max(0, ACTIVITY["runs"] - 1)
            touch()
            self.changed.set()

    def public(self) -> dict:
        files = sorted(p.relative_to(OUTPUT).as_posix() for p in self.out.rglob("*") if p.is_file()) if self.done() else []
        return {"done": self.done(), "message": self.message, "code": self.code, "error": self.error, "tail": self.tail, "files": files}

    async def drop(self) -> None:
        """Killed when still running, its folder removed."""
        if self.task is not None and not self.task.done():
            if self.proc is not None:
                kill_tree(self.proc)
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - it is going away
                pass
        await asyncio.to_thread(shutil.rmtree, self.out, True)


TOOLS: dict[str, Tool] = {}


def tool_args(d: dict) -> list[str]:
    """lyrics_worker.py's arguments from the studio's request (its files stay inside INPUT and OUTPUT)."""
    if d.get("tool", "lyrics") != "lyrics":
        raise web.HTTPBadRequest(text=f"no tool {d.get('tool')}")
    name = str(d.get("input", ""))
    if not NAME.match(name) or not (INPUT / name).is_file():
        raise web.HTTPNotFound(text=f"no input {name}: send it first")
    args = [str(INPUT / name)]
    if d.get("vocals_only"):
        args.append("--vocals-only")
    for opt in ("model", "language"):
        if v := d.get(opt):
            if not re.fullmatch(r"[\w.-]{1,40}", str(v)):
                raise web.HTTPBadRequest(text=f"bad {opt}")
            args += [f"--{opt}", str(v)]
    return args


@web.middleware
async def auth(request: web.Request, handler):
    if request.path != "/health":
        got = request.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        touch()
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    busy = ACTIVITY["runs"] > 0
    return web.json_response({"worker": WORKER.state(), "llm": LLAMA.state(), "version": VERSION, "busy": busy,
                              "idle": 0 if busy else round(time.time() - ACTIVITY["last"]), "models": DOWNLOAD.state, "fast": FAST_COPY.state,
                              "tools": sum(1 for t in TOOLS.values() if not t.done()), "tls": TLS_FP or None})


def tls_context() -> ssl.SSLContext:
    """TLS=1: the certificate and key from the environment (the studio's, pinned there) into <WORK>/tls; none given -
    the server's own, made once with openssl (ca-certificates brings it into the image) and kept on the volume."""
    global TLS_FP
    d = WORK / "tls"
    d.mkdir(parents=True, exist_ok=True)
    cert, key = d / "cert.pem", d / "key.pem"
    if os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"):
        cert.write_bytes(base64.b64decode(os.environ["TLS_CERT"]))
        key.write_bytes(base64.b64decode(os.environ["TLS_KEY"]))
    elif not (cert.is_file() and key.is_file()):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(key),
                        "-out", str(cert), "-days", "3650", "-subj", "/CN=remiqora-video-worker", "-sha256",
                        "-addext", "basicConstraints=critical,CA:TRUE"], check=True, capture_output=True)
    try:
        key.chmod(0o600)
    except OSError:
        pass
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    TLS_FP = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="utf-8"))).hexdigest()
    return ctx


async def tools_run(request: web.Request) -> web.Response:
    d = await request.json()
    if any(not t.done() for t in TOOLS.values()):
        return web.json_response({"error": "a tool is running"}, status=409)
    if not TOOL_SCRIPT.is_file():
        return web.json_response({"error": f"no tool script {TOOL_SCRIPT} in this image"}, status=501)
    tid = uuid.uuid4().hex
    TOOLS[tid] = Tool(tid, tool_args(d))
    TOOLS[tid].start()
    return web.json_response({"id": tid})


async def tools_result(request: web.Request) -> web.Response:
    t = TOOLS.get(request.match_info["id"])
    if t is None:
        return web.json_response({"error": "no such run"}, status=404)
    if not t.done() and not t.changed.is_set():
        try:
            await asyncio.wait_for(t.changed.wait(), 50)
        except asyncio.TimeoutError:
            pass
    t.changed.clear()   # what it said is in the answer; the next ask waits for more
    return web.json_response(t.public())


async def tools_drop(request: web.Request) -> web.Response:
    tid = request.match_info["id"]
    t = TOOLS.pop(tid, None) if TOOL_ID.match(tid) else None
    if t is not None:
        await t.drop()
    return web.json_response({"ok": True})


FILE_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")


def model_files(sub: str = "") -> list[dict]:
    """Weight files under the MODELS folders as {"path": relative with /, "size"}; nothing outside them. The first
    folder wins for a name met twice, as in ComfyUI. Raises ValueError for a `sub` that leaves a folder."""
    out: dict[str, int] = {}
    for root in MODELS:
        base = root.resolve()
        top = (base / sub).resolve() if sub else base
        if top != base and base not in top.parents:
            raise ValueError("outside the models folder")
        if not top.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(top):
            for n in names:
                p = Path(dirpath) / n
                if not n.lower().endswith(FILE_EXT) or n.startswith("."):
                    continue
                try:
                    real = p.resolve()
                    if base not in real.parents:   # a symlink out of the volume is not listed
                        continue
                    out.setdefault(p.relative_to(base).as_posix(), real.stat().st_size)
                except (OSError, ValueError):
                    continue
    return [{"path": k, "size": v} for k, v in sorted(out.items())]


async def models_files(request: web.Request) -> web.Response:
    sub = request.query.get("sub", "")
    if "\\" in sub or sub.startswith("/") or ".." in Path(sub).parts or chr(0) in sub:
        return web.json_response({"error": "bad path"}, status=400)
    try:
        files = await asyncio.to_thread(model_files, sub)
    except ValueError:
        return web.json_response({"error": "bad path"}, status=400)
    return web.json_response({"files": files})


async def models_download(request: web.Request) -> web.Response:
    d = await request.json()
    if DOWNLOAD.busy():
        return web.json_response({"error": "a download is running"}, status=409)
    DOWNLOAD.start(str(d.get("sets", "")), str(d.get("hf_token", "")))
    return web.json_response({"ok": True})


async def llm_models(request: web.Request) -> web.Response:
    return web.json_response({"models": await asyncio.to_thread(Llama.models)})


async def llm_start(request: web.Request) -> web.Response:
    d = await request.json()
    await LLAMA.start(str(d["model"]), int(d.get("ctx", 16384)))
    return web.json_response({"ok": True})


async def llm_stop(request: web.Request) -> web.Response:
    await LLAMA.stop()
    return web.json_response({"ok": True})


LLM_PATH = re.compile(r"^/v1/[a-z/_.-]{1,60}$")   # llama-server's own API only: no other host, no ../
CALL_KEEP = 600   # s: a result nobody fetched (the studio gave up) is dropped


async def llm_call(request: web.Request) -> web.Response:
    d = await request.json()
    if LLAMA.state() != "ready":
        return web.json_response({"error": "the writer is not running"}, status=409)
    path = str(d.get("path", ""))
    if not LLM_PATH.match(path) or ".." in path:
        return web.json_response({"error": "bad path"}, status=400)
    now = time.time()
    for k, (t, task) in list(LLAMA.calls.items()):
        if task.done() and now - t > CALL_KEEP:
            LLAMA.calls.pop(k, None)
    cid = uuid.uuid4().hex
    LLAMA.calls[cid] = (now, asyncio.create_task(LLAMA.call(path, d["body"])))
    return web.json_response({"id": cid})


async def llm_result(request: web.Request) -> web.Response:
    entry = LLAMA.calls.get(request.match_info["id"])
    if entry is None:
        return web.json_response({"error": "no such call"}, status=404)
    task = entry[1]
    done, _ = await asyncio.wait({task}, timeout=50)
    if not done:
        return web.json_response({"done": False})
    LLAMA.calls.pop(request.match_info["id"], None)
    try:
        return web.json_response({"done": True, **task.result()})
    except Exception as e:  # noqa: BLE001 - the studio shows it
        return web.json_response({"done": True, "status": 502, "body": f"{type(e).__name__}: {e}"})


def self_stop() -> None:
    """RunPod's API with the pod's own key (RUNPOD_API_KEY, RunPod puts it in every pod and it acts on that pod only): the
    pod stops, its volume stays. The REST action first, the GraphQL podStop (what runpodctl uses) if REST refuses it."""
    pod, key = os.environ["RUNPOD_POD_ID"], os.environ.get("RUNPOD_API_KEY") or os.environ["SELF_STOP_KEY"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "RemiqoraVideo (+https://remiqora.com)"}
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://api.runpod.io/v2/pods/{pod}/action", method="POST", headers=headers,
                                                      data=json.dumps({"action": "stop"}).encode()), timeout=30).close()
        return
    except urllib.error.HTTPError as e:
        print(f"self-stop by REST: HTTP {e.code}, trying GraphQL", flush=True)
    q = {"query": 'mutation($id: String!) { podStop(input: {podId: $id}) { id desiredStatus } }', "variables": {"id": pod}}
    with urllib.request.urlopen(urllib.request.Request("https://api.runpod.io/graphql", method="POST", headers=headers,
                                                       data=json.dumps(q).encode()), timeout=30) as r:
        d = json.loads(r.read())
    if d.get("errors"):
        raise RuntimeError(f"GraphQL: {d['errors']}")


async def idle_watch(_app) -> None:
    async def loop() -> None:
        while True:
            await asyncio.sleep(30)
            idle = time.time() - ACTIVITY["last"]
            # a run that hangs keeps "runs" up forever: an hour without any request from the studio stops it anyway
            if idle > IDLE_STOP_MIN * 60 and (ACTIVITY["runs"] == 0 or idle > max(IDLE_STOP_MIN, STUCK_MIN) * 60):
                print(f"{time.strftime('%H:%M:%S')} idle {idle / 60:.0f} min: the pod stops itself", flush=True)
                try:
                    await asyncio.to_thread(self_stop)
                except Exception as e:  # noqa: BLE001 - try again on the next round
                    print(f"self-stop failed: {e}", flush=True)
    if IDLE_STOP_MIN > 0 and os.environ.get("RUNPOD_POD_ID") and (os.environ.get("RUNPOD_API_KEY") or os.environ.get("SELF_STOP_KEY")):
        asyncio.create_task(loop())


async def missing(request: web.Request) -> web.Response:
    want: dict = await request.json()
    out = []
    for name, digest in want.items():
        p = INPUT / name
        if not NAME.match(name) or not p.is_file() or await asyncio.to_thread(sha256, p) != digest:
            out.append(name)
    return web.json_response({"missing": out})


async def put_input(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not NAME.match(name) or name.startswith("."):
        return web.json_response({"error": "bad name"}, status=400)
    INPUT.mkdir(parents=True, exist_ok=True)
    part, h = INPUT / f".{name}.part", hashlib.sha256()
    with open(part, "wb") as f:
        async for block in request.content.iter_chunked(1 << 20):
            h.update(block)
            f.write(block)
    want = request.headers.get("X-Sha256")
    if want and want != h.hexdigest():
        part.unlink(missing_ok=True)
        return web.json_response({"error": "hash mismatch"}, status=400)
    os.replace(part, INPUT / name)
    return web.json_response({"ok": True})


LORA_PART = re.compile(r"^[\w][\w .()+,=-]{0,150}$")


def lora_rel(name: str) -> Optional[str]:
    """A LoRA's name as the graph spells it (a path under the loras folder, with / or a backslash): checked part by part, .safetensors only."""
    parts = name.replace("\\", "/").split("/")
    if not name.lower().endswith(".safetensors") or len(parts) > 4 or not all(LORA_PART.match(x) and ".." not in x for x in parts):
        return None
    return "/".join(parts)


def lora_have(rel: str) -> Optional[Path]:
    for root in MODELS:
        p = root / "loras" / rel
        if p.is_file():
            return p
    return None


async def loras_missing(request: web.Request) -> web.Response:
    want: dict = await request.json()   # name -> size in bytes
    out = []
    for name, size in want.items():
        rel = lora_rel(str(name))
        have = lora_have(rel) if rel else None
        if not rel or have is None or have.stat().st_size != size:
            out.append(name)
    return web.json_response({"missing": out})


def lora_part(name: str) -> Optional[tuple[Path, Path]]:
    rel = lora_rel(name)
    if rel is None or not MODELS:
        return None
    dest = MODELS[0] / "loras" / rel
    return dest, dest.with_name(f".{dest.name}.part")


async def lora_size(request: web.Request) -> web.Response:
    """How much of a LoRA being sent in pieces is here (the studio goes on from it after a dropped piece)."""
    paths = lora_part(request.match_info["name"])
    if paths is None:
        return web.json_response({"error": "bad name"}, status=400)
    return web.json_response({"size": paths[1].stat().st_size if paths[1].exists() else 0})


async def put_lora(request: web.Request) -> web.Response:
    """A LoRA in pieces (RunPod's proxy cuts a request at ~100 s; a LoRA can be GBs): ?offset=<bytes so far> writes
    the piece there in the .part file, ?done=1 checks the whole file's X-Sha256 and puts it in place. No query: the whole
    file in one request."""
    paths = lora_part(request.match_info["name"])
    if paths is None:
        return web.json_response({"error": "bad name"}, status=400)
    dest, part = paths
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return web.json_response({"error": "bad offset"}, status=400)
    done = request.query.get("done", "0" if "offset" in request.query else "1") == "1"
    have = part.stat().st_size if part.exists() else 0
    if offset < 0 or offset > have:
        return web.json_response({"error": "offset", "size": have}, status=409)
    with open(part, "r+b" if offset else "wb") as f:
        f.seek(offset)
        f.truncate()
        async for block in request.content.iter_chunked(1 << 20):
            f.write(block)
    if not done:
        return web.json_response({"ok": True, "size": part.stat().st_size})
    want = request.headers.get("X-Sha256")
    if want and want != await asyncio.to_thread(sha256_file, part):
        part.unlink(missing_ok=True)
        return web.json_response({"error": "hash mismatch"}, status=400)
    os.replace(part, dest)
    return web.json_response({"ok": True})


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while block := f.read(1 << 22):
            h.update(block)
    return h.hexdigest()


async def get_output(request: web.Request) -> web.StreamResponse:
    p = (OUTPUT / request.match_info["path"]).resolve()
    if not p.is_relative_to(OUTPUT.resolve()) or not p.is_file():
        return web.json_response({"error": "not found"}, status=404)
    if request.query.get("delete") != "1":
        return web.FileResponse(p)
    resp = web.StreamResponse(headers={"Content-Type": "application/octet-stream", "Content-Length": str(p.stat().st_size)})
    await resp.prepare(request)
    with open(p, "rb") as f:
        while block := f.read(1 << 20):
            await resp.write(block)
    await resp.write_eof()
    p.unlink(missing_ok=True)
    return resp


async def ws(request: web.Request) -> web.WebSocketResponse:
    sock = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 26)
    await sock.prepare(request)
    old, WORKER.ws = WORKER.ws, sock
    if old is not None and not old.closed:
        await old.close(message=b"taken over by another studio")
    try:
        if not await WORKER.ensure():   # a worker already running sends no "ready" of its own: say it here
            await sock.send_str(json.dumps({"event": "ready", "pid": WORKER.proc.pid}))
        async for m in sock:
            if m.type != WSMsgType.TEXT:
                continue
            msg = json.loads(m.data)
            cmd = msg.get("cmd")
            touch()
            if cmd == "run":
                ACTIVITY["runs"] += 1
            if cmd == "stop":
                await WORKER.stop()
                await sock.send_str(json.dumps({"event": "stopped"}))
            elif cmd == "restart":
                await WORKER.stop()
                await WORKER.ensure()   # its "ready" comes from the new worker
            else:
                if WORKER.state() == "off":
                    await WORKER.ensure()
                WORKER.send(msg)
    except asyncio.TimeoutError:
        await sock.send_str(json.dumps({"event": "exit", "log": "the worker did not start in time\n" + log_tail()}))
    finally:
        if WORKER.ws is sock:
            WORKER.ws = None
    return sock


def main() -> None:
    if not TOKEN:
        sys.exit("REMOTE_TOKEN is not set: the worker server does not run without a token")
    if not SCRIPT.is_file() or not (COMFY_DIR / "ComfyUI" / "comfy").is_dir() or not python().exists():
        sys.exit(f"no worker: {SCRIPT}, {COMFY_DIR / 'ComfyUI'}, {python()}")
    WORK.mkdir(parents=True, exist_ok=True)
    app = web.Application(middlewares=[auth], client_max_size=0)
    app.add_routes([web.get("/health", health), web.post("/input/missing", missing), web.put("/input/{name}", put_input),
                    web.post("/loras/missing", loras_missing), web.put("/loras/{name:.+}", put_lora), web.get("/loras/{name:.+}", lora_size),
                    web.get("/output/{path:.+}", get_output), web.get("/ws", ws),
                    web.get("/llm/models", llm_models), web.post("/llm/start", llm_start), web.post("/llm/stop", llm_stop),
                    web.post("/llm/call", llm_call), web.get("/llm/call/{id}", llm_result),
                    web.post("/tools/run", tools_run), web.get("/tools/{id}", tools_result), web.delete("/tools/{id}", tools_drop),
                    web.post("/models/download", models_download), web.get("/models/files", models_files)])
    app.on_startup.append(idle_watch)

    async def first_download(_app) -> None:
        DOWNLOAD.start(os.environ.get("MODEL_SETS", ""), os.environ.get("HF_TOKEN", ""))
        if not DOWNLOAD.busy():   # nothing to fetch: straight to the fast copy (after a download it starts by itself)
            FAST_COPY.start()
    app.on_startup.append(first_download)

    async def cleanup(_app) -> None:
        await WORKER.stop()
        await LLAMA.stop()
        for t in list(TOOLS.values()):
            await t.drop()
    app.on_cleanup.append(cleanup)
    ctx = tls_context() if TLS else None
    print(f"{time.strftime('%H:%M:%S')} worker server {VERSION} on {HOST}:{PORT}{' (https, cert sha256 ' + TLS_FP + ')' if ctx else ''}, "
          f"models {[str(m) for m in MODELS]}, work {WORK}", flush=True)
    web.run_app(app, host=HOST, port=PORT, print=None, ssl_context=ctx)


if __name__ == "__main__":
    main()
