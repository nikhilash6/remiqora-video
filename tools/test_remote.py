"""ENGINE=remote without a GPU: cloud/worker_server.py on loopback with a stand-in worker (it speaks h3worker.py's
protocol and "renders" by copying the input picture), driven through app/engine/remote.py the way jobs.py drives it.

    .venv\\Scripts\\python.exe tools\\test_remote.py [--server-python engine\\.venv\\Scripts\\python.exe]

Checks: connect + ready, input sent once (by hash), output fetched and removed on the server, progress events, a
worker error, free, lora list, own LoRA uploaded once (bad names refused), stop + start again, interrupt, a wrong
token refused, GET /models/files (the weights on the volume; a path out of MODELS refused; no token refused) and the
Models page's view of it (app/cloud/files.py); then the tools (/tools/*, a stand-in lyrics_worker.py): the song's
words and a scene's voice through app/song.py come back, messages relayed, the song sent once, an error raised, a
cancelled job killed on the server, a bad id. Then TLS (a provider without an https proxy, Vast.ai): a second server
with the studio's own certificate (app/cloud/tls.py) - https and wss with the pinned certificate work, the manager's
/health too, another certificate on that address is refused, the usual CA check refuses the self-signed one, and a
server left to make its own certificate shows its fingerprint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STUB = r'''
import json, os, shutil, sys, threading, time
settings = json.loads(open(sys.argv[1], encoding="utf-8").read())
proto = sys.stdout
stop = threading.Event()
def emit(ev, **d):
    proto.write(json.dumps({"event": ev, **d}) + "\n"); proto.flush()
def run(msg):
    g, rid = msg["graph"], msg["id"]
    src = next((n["inputs"]["image"] for n in g.values() if n["class_type"] == "LoadImage"), None)
    if src is None:
        emit("error", id=rid, message="ComfyUI не принял граф:\nno LoadImage"); return
    for i in range(1, 4):
        if stop.is_set():
            emit("interrupted", id=rid); return
        emit("executing", node="3"); emit("progress", value=i, max=3, node="3")
        time.sleep(float(g.get("_slow", {}).get("inputs", {}).get("s", 0.05)))
    name = "stub_%05d_.png" % int(time.time() * 1000 % 100000)
    os.makedirs(os.path.join(settings["output"], "sub"), exist_ok=True)
    shutil.copyfile(os.path.join(settings["input"], src), os.path.join(settings["output"], "sub", name))
    emit("done", id=rid, outputs={"9": {"images": [{"filename": name, "subfolder": "sub", "type": "output"}]}})
work = []
cv = threading.Condition()
def reader():
    for line in sys.stdin:
        m = json.loads(line)
        if m["cmd"] == "interrupt":
            stop.set()
        else:
            with cv: work.append(m); cv.notify()
    with cv: work.append({"cmd": "exit"}); cv.notify()
threading.Thread(target=reader, daemon=True).start()
emit("ready", pid=os.getpid())
while True:
    with cv:
        while not work: cv.wait()
        m = work.pop(0)
    if m["cmd"] == "exit": break
    stop.clear()
    if m["cmd"] == "run": run(m)
    elif m["cmd"] == "free": emit("freed")
    elif m["cmd"] == "loras": emit("list", names=["style_a.safetensors"])
    elif m["cmd"] == "models": emit("list", names=["h3.safetensors"])
'''

# lyrics_worker.py's stand-in: the same arguments and JSON lines, "vocals" = a copy of the input, three fixed words
TOOL_STUB = r'''
import argparse, json, shutil, sys, time
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("song", type=Path); ap.add_argument("out", type=Path)
ap.add_argument("--model", default="large-v3"); ap.add_argument("--language"); ap.add_argument("--vocals-only", action="store_true")
a = ap.parse_args()
def say(**kw):   # a pause like the real tool's: the server relays the latest message to each poll
    print(json.dumps(kw), flush=True)
    time.sleep(0.5)
if a.language == "xx":
    say(error="RuntimeError: no such language"); sys.exit(1)
a.out.mkdir(parents=True, exist_ok=True)
say(stage="separate", message="Demucs: separating the vocals")
if a.model == "slow":
    time.sleep(30)
shutil.copyfile(a.song, a.out / "vocals.wav")
if a.vocals_only:
    say(stage="done", message="vocals"); sys.exit(0)
say(stage="transcribe", message="Whisper: pass 1 of 2")
words = [{"start": i, "end": i + 1, "word": "la", "p": 1} for i in range(3)]
(a.out / "lyrics.json").write_text(json.dumps({"version": 1, "model": a.model, "language": a.language or "en",
                                               "lines": [{"start": 0, "end": 3, "text": "la la la", "words": words}]}))
say(stage="done", message="1 lines")
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-python", default=str(ROOT.parents[1] / "engine" / ".venv" / "Scripts" / "python.exe")
                    if (ROOT.parents[1] / "engine").is_dir() else str(ROOT / "engine" / ".venv" / "Scripts" / "python.exe"))
    ap.add_argument("--port", type=int, default=8189)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="rv-remote-"))
    (tmp / "comfy" / "ComfyUI" / "comfy").mkdir(parents=True)
    (tmp / "stub.py").write_text(STUB, encoding="utf-8")
    for rel, n in (("diffusion_models/h3.safetensors", 1234), ("loras/sub/style_a.safetensors", 77), ("llm/w.gguf", 5), ("vae/notes.txt", 9)):
        (tmp / "models" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "models" / rel).write_bytes(b"x" * n)
    (tmp / "secret.safetensors").write_bytes(b"outside")   # beside the models folder, not in it
    (tmp / "tool_stub.py").write_text(TOOL_STUB, encoding="utf-8")
    token = secrets.token_hex(16)
    env = {**os.environ, "REMOTE_TOKEN": token, "REMOTE_HOST": "127.0.0.1", "REMOTE_PORT": str(a.port),
           "MODELS": str(tmp / "models"), "WORK": str(tmp / "work"), "COMFY_DIR": str(tmp / "comfy"),
           "WORKER_PYTHON": sys.executable, "WORKER_SCRIPT": str(tmp / "stub.py"), "TOOL_SCRIPT": str(tmp / "tool_stub.py"),
           "FAST_MODELS": str(tmp / "fast")}
    server = subprocess.Popen([a.server_python, str(ROOT / "cloud" / "worker_server.py")], env=env)
    url = f"http://127.0.0.1:{a.port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/health", timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)

    # the studio's side, set up as ENGINE=remote in .env would
    os.environ.update({"ENGINE": "remote", "REMOTE_URL": url, "REMOTE_TOKEN": token,
                       "COMFY_INPUT": str(tmp / "local_in"), "COMFY_OUTPUT": str(tmp / "local_out")})
    sys.path.insert(0, str(ROOT))
    from app.engine.comfy import COMFY, output_files
    from app.engine.errors import EngineError, Interrupted
    from app.engine.remote import RemoteWorker
    from app import config
    from app.cloud import state as cloud_state
    cloud_state.FILE = tmp / "cloud.json"   # not the studio's: a live pod there must not change what is tested

    ok = True

    def get(path: str, tok: str = token):
        req = urllib.request.Request(url + path, headers={"Authorization": f"Bearer {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def check(cond: bool, what: str) -> None:
        nonlocal ok
        ok &= bool(cond)
        print(("ok   " if cond else "FAIL ") + what, flush=True)

    async def go() -> None:
        check(isinstance(COMFY.current(), RemoteWorker), "ENGINE=remote picks RemoteWorker")
        frame = config.COMFY_INPUT / "frame one.png"
        frame.write_bytes(os.urandom(300_000))
        graph = {"1": {"class_type": "LoadImage", "inputs": {"image": frame.name}}, "9": {"class_type": "SaveImage", "inputs": {}}}
        nodes, prog = [], []
        await COMFY.ensure()
        check(COMFY.state() == "ready" and COMFY.pid, "connected, worker ready")
        files = output_files(await COMFY.run(graph, nodes.append, lambda v, m, n: prog.append((v, m))))
        check(len(files) == 1 and Path(files[0]).read_bytes() == frame.read_bytes(), "input sent, output fetched byte for byte")
        check(prog[-1] == (3, 3) and nodes, "progress and node events relayed")
        check(not any((tmp / "work" / "output" / "sub").iterdir()), "output removed on the server after fetching")
        t = os.stat(tmp / "work" / "input" / frame.name).st_mtime_ns
        await COMFY.run(graph, nodes.append, lambda v, m, n: None)
        check(os.stat(tmp / "work" / "input" / frame.name).st_mtime_ns == t, "unchanged input not sent again")
        try:
            await COMFY.run({"9": {"class_type": "SaveImage", "inputs": {}}}, nodes.append, lambda v, m, n: None)
            check(False, "worker error raised")
        except EngineError as e:
            check("rejected" in str(e) or "не принял" in str(e), "worker error raised as EngineError")
        check(await COMFY.lora_names() == ["style_a.safetensors"], "LoRA list is the server's")
        check((await COMFY.object_info("UNETLoader"))["input"]["required"]["unet_name"][0] == ["h3.safetensors"], "UNET list")
        # a local LoRA the server lacks goes up once; a second run sends nothing; bad names are refused
        lora_dir = tmp / "local_loras"
        (lora_dir / "sub").mkdir(parents=True)
        (lora_dir / "mine.safetensors").write_bytes(os.urandom(2_500_000))
        (lora_dir / "sub" / "deep.safetensors").write_bytes(os.urandom(1000))
        config.LORA_DIRS = [lora_dir]
        import app.engine.remote as remote_mod
        remote_mod.CHUNK = 1_000_000   # mine.safetensors goes in 3 pieces
        lg = {**graph, "l0": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "mine.safetensors", "strength_model": 1}},
              "l1": {"class_type": "LoraLoader", "inputs": {"lora_name": "sub" + chr(92) + "deep.safetensors"}},
              "l2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "not_here.safetensors"}}}
        dst = tmp / "models" / "loras"
        await COMFY.run(lg, nodes.append, lambda v, m, n: None)
        check((dst / "mine.safetensors").read_bytes() == (lora_dir / "mine.safetensors").read_bytes()
              and (dst / "sub" / "deep.safetensors").is_file(), "missing LoRA uploaded (also from a subfolder)")
        check(not (dst / "not_here.safetensors").exists() and not list(dst.rglob(".*.part")), "unknown LoRA skipped, no temp files left")
        t = os.stat(dst / "mine.safetensors").st_mtime_ns
        await COMFY.run(lg, nodes.append, lambda v, m, n: None)
        check(os.stat(dst / "mine.safetensors").st_mtime_ns == t, "second run uploads nothing")
        hdr = {"Authorization": f"Bearer {token}"}
        for bad in ("evil.txt", "..%2Fx.safetensors", "a/../../x.safetensors", ".hidden.safetensors", "%2Fetc%2Fpasswd.safetensors"):
            try:
                urllib.request.urlopen(urllib.request.Request(url + "/loras/" + bad, data=b"x", method="PUT", headers=hdr)).close()
                check(False, f"bad LoRA name refused: {bad}")
            except urllib.error.HTTPError as e:
                check(e.code in (400, 404), f"bad LoRA name refused: {bad}")
        check(not (tmp / "models" / "x.safetensors").exists() and not (tmp / "x.safetensors").exists(), "nothing written outside loras")
        await COMFY.free()
        check(COMFY.state() == "ready", "free keeps the worker")
        slow = {**graph, "_slow": {"class_type": "X", "inputs": {"s": 1.0}}}
        task = asyncio.create_task(COMFY.run(slow, lambda n: None, lambda v, m, n: None))
        await asyncio.sleep(0.5)
        await COMFY.interrupt()
        try:
            await task
            check(False, "interrupt")
        except Interrupted:
            check(True, "interrupt stops a running graph")
        await COMFY.stop()
        check(COMFY.state() == "off" and json.loads(urllib.request.urlopen(url + "/health").read())["worker"] == "off", "stop ends the remote worker")
        await COMFY.run(graph, lambda n: None, lambda v, m, n: None)
        check(COMFY.state() == "ready", "next run starts it again")
        await COMFY.stop()
        code, body = get("/models/files")
        got = {f["path"]: f["size"] for f in body["files"]} if body else {}
        # beside the seeded weights: the LoRAs the upload check above sent (all in loras/)
        # the fast copy: the volume's weights on the pod's own disk, notes and the like left behind
        h = json.loads(urllib.request.urlopen(url + "/health", timeout=10).read())
        check(h.get("fast", {}).get("state") == "ready" and (tmp / "fast" / "diffusion_models" / "h3.safetensors").stat().st_size == 1234
              and (tmp / "fast" / "loras" / "sub" / "style_a.safetensors").is_file() and not (tmp / "fast" / "vae" / "notes.txt").exists(),
              "weights copied to the fast disk (no other files)")
        seeded = {k: v for k, v in got.items() if not k.startswith("loras/") or k == "loras/sub/style_a.safetensors"}
        check(code == 200 and seeded == {"diffusion_models/h3.safetensors": 1234, "loras/sub/style_a.safetensors": 77, "llm/w.gguf": 5},
              "/models/files lists the weights with sizes (relative paths, no other files)")
        code, body = get("/models/files?sub=loras")
        paths = [f["path"] for f in body["files"]] if body else []
        check(code == 200 and "loras/sub/style_a.safetensors" in paths and all(x.startswith("loras/") for x in paths),
              "/models/files?sub= narrows to a subfolder")
        for bad in ("..", "../", "loras/../..", "%2e%2e%2f", "..%2f..%2f", "/etc", "..%5c..", "C:%5cWindows", "loras/%2e%2e/%2e%2e"):
            code, body = get("/models/files?sub=" + bad)
            check(code == 400 and not (body or {}).get("files"), f"/models/files?sub={bad} refused")
        check(get("/models/files", "wrong")[0] == 401, "/models/files without the token refused")
        # the Models page's view (app/cloud/files.py): live, then the last known one when the server is not asked
        from app.cloud import files as cfiles
        cfiles.FILE = tmp / "cloud_files.json"
        v = cfiles.view(force=True)
        check(v["source"] == "live" and v["files"].get("diffusion_models/h3.safetensors") == 1234, "page view: live list from the server")
        real_http_json = cfiles.remote.http_json
        def down(*a, **k):
            raise EngineError("pod is down")
        cfiles.remote.http_json = down   # the same server, no longer answering
        cfiles._CACHE.update(tried=0.0)
        v = cfiles.view()
        check(v["source"] in ("live", "cached") and "diffusion_models/h3.safetensors" in v["files"], "page view: the pod down - the last known list stays")
        cfiles._CACHE.update(tried=0.0, files=None)   # ... and after a studio restart: the list saved on disk
        v = cfiles.view()
        check(v["source"] == "cached" and "diffusion_models/h3.safetensors" in v["files"], "page view: restarted with the pod down - the saved list")
        cfiles.remote.http_json = real_http_json
        config.REMOTE_URL = "http://127.0.0.1:1"   # another server that is down, nothing known of it
        v = cfiles.view(force=True)
        check(v["source"] in ("manifest", "none") and "diffusion_models/h3.safetensors" not in v["files"], "page view: another server, nothing known - manifest or none")
        config.REMOTE_URL = url
        await tools()
        config.REMOTE_TOKEN = "wrong"
        try:
            await RemoteWorker().ensure()
            check(False, "wrong token refused")
        except EngineError:
            check(True, "wrong token refused")
        config.REMOTE_TOKEN = token
        await tls_checks()

    async def tls_checks() -> None:
        """The studio's certificate on the server, pinned on this side (Vast.ai's way)."""
        import ssl
        from app.cloud import manager, tls
        from app.engine import remote as remote_mod
        cert, key = tls.make()
        other, _ = tls.make()
        fp = tls.fingerprint(cert)
        check(cert.startswith("-----BEGIN CERTIFICATE-----") and len(fp) == 64 and fp != tls.fingerprint(other), "studio makes a certificate and its fingerprint")
        p2 = a.port + 1
        srv = subprocess.Popen([a.server_python, str(ROOT / "cloud" / "worker_server.py")],
                               env={**env, "REMOTE_PORT": str(p2), "WORK": str(tmp / "work_tls"), **tls.env(cert, key)})
        url2 = f"https://127.0.0.1:{p2}"
        pod = {"id": "x", "url": url2, "tls_cert": cert}
        try:
            for _ in range(50):
                if manager._health(pod, 1):
                    break
                time.sleep(0.2)
            config.REMOTE_URL, config.REMOTE_CERT = url2, cert
            h = remote_mod.http_json("GET", "/health")
            check(h.get("tls") == fp, "https with the pinned certificate: /health shows the same fingerprint")
            check((manager._health(pod, 5) or {}).get("tls") == fp, "manager's /health with the pinned certificate")
            w = RemoteWorker()
            await w.ensure()
            check(w.state() == "ready" and (await w.lora_names()) == ["style_a.safetensors"], "wss with the pinned certificate: worker ready")
            await w.stop()
            try:
                urllib.request.urlopen(url2 + "/health", timeout=5).close()
                check(False, "self-signed certificate refused by the usual CA check")
            except (urllib.error.URLError, ssl.SSLError):
                check(True, "self-signed certificate refused by the usual CA check")
            config.REMOTE_CERT = other   # another certificate pinned: that server must be refused before the token goes out
            try:
                remote_mod.http_json("GET", "/health")
                check(False, "wrong pinned certificate: https refused")
            except EngineError:
                check(True, "wrong pinned certificate: https refused")
            try:
                await RemoteWorker().ensure()
                check(False, "wrong pinned certificate: wss refused")
            except EngineError:
                check(True, "wrong pinned certificate: wss refused")
            check(manager._health({**pod, "tls_cert": other}, 5) is None, "wrong pinned certificate: manager's /health refused")
        finally:
            srv.terminate()
            srv.wait(10)
        config.REMOTE_URL, config.REMOTE_CERT = url, ""
        if shutil.which("openssl"):   # no certificate given: the server makes its own and tells its fingerprint
            p3 = a.port + 2
            srv = subprocess.Popen([a.server_python, str(ROOT / "cloud" / "worker_server.py")],
                                   env={**env, "REMOTE_PORT": str(p3), "WORK": str(tmp / "work_tls2"), "TLS": "1"})
            try:
                lax = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                lax.check_hostname, lax.verify_mode = False, ssl.CERT_NONE
                h3 = None
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(f"https://127.0.0.1:{p3}/health", timeout=1, context=lax) as r:
                            h3 = json.loads(r.read())
                        break
                    except (OSError, ValueError):
                        time.sleep(0.2)
                own = (tmp / "work_tls2" / "tls" / "cert.pem")
                check(h3 and len(h3.get("tls") or "") == 64 and own.is_file() and h3["tls"] == tls.fingerprint(own.read_text()),
                      "server's own certificate: /health shows its fingerprint, kept in WORK/tls")
            finally:
                srv.terminate()
                srv.wait(10)

    async def tools() -> None:
        """The song's words and a scene's voice through app/song.py with the tool on the server."""
        from app import song
        song.SONGS = tmp / "songs"
        sid = "0123456789ab"
        (song.SONGS / sid).mkdir(parents=True)
        (song.SONGS / sid / "song.mp3").write_bytes(os.urandom(200_000))
        msgs: list[str] = []
        rec = await song.recognise(sid, msgs.append, None)
        check(rec["lines"][0]["text"] == "la la la" and song.lines(sid)[0]["end"] == 3, "words recognised on the server, lyrics.json back")
        check(song.vocals(sid) and song.vocals(sid).read_bytes() == (song.SONGS / sid / "song.mp3").read_bytes(), "vocals.wav back byte for byte")
        check(any("Demucs" in m for m in msgs) and any("Whisper" in m for m in msgs), "the tool's messages relayed")
        check(not list((tmp / "work" / "output" / "tools").iterdir()), "the run's folder removed on the server")
        sent = [p for p in (tmp / "work" / "input").iterdir() if p.name.startswith("tool-")]
        check(len(sent) == 1 and sent[0].suffix == ".mp3", "the song sent under its hash")
        t = sent[0].stat().st_mtime_ns
        await song.recognise(sid, msgs.append, "ru")
        check(sent[0].stat().st_mtime_ns == t, "the same song not sent again")
        wav = tmp / "voice" / "voice.wav"
        await song.separate(sent[0], wav, msgs.append)
        check(wav.read_bytes() == sent[0].read_bytes() and not (tmp / "voice" / "_voice").exists(), "a scene's voice separated on the server")
        try:
            await song.recognise(sid, msgs.append, "xx")
            check(False, "a tool error raised")
        except EngineError as e:
            check("no such language" in str(e), "a tool error raised as EngineError")
        config.LYRICS_MODEL = "slow"
        task = asyncio.create_task(song.recognise(sid, msgs.append, None))
        await asyncio.sleep(1.5)
        health = json.loads(urllib.request.urlopen(url + "/health").read())
        check(health["tools"] == 1 and health["busy"], "a running tool is work for /health")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.5)
        health = json.loads(urllib.request.urlopen(url + "/health").read())
        check(health["tools"] == 0 and not list((tmp / "work" / "output" / "tools").iterdir()), "a cancelled job kills the run on the server")
        req = urllib.request.Request(url + "/tools/" + "0" * 32, headers={"Authorization": f"Bearer {token}"})
        try:
            urllib.request.urlopen(req).close()
            check(False, "a bad id is 404")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "a bad id is 404")

    def vast_offers() -> None:
        """Vast.ai offers from a recorded-shape stub (no network): dear traffic and a slow line are dropped, the price
        of the deploy's download is shown."""
        from app.cloud.providers import vast
        v = vast.Vast("k")
        base = {"gpu_name": "RTX 5090", "gpu_ram": 32768, "reliability": 0.99, "geolocation": "DE", "inet_down": 900}
        recorded = [
            {**base, "id": 1, "machine_id": 11, "dph_total": 0.60, "inet_down_cost": 0.0},       # free traffic
            {**base, "id": 2, "machine_id": 12, "dph_total": 0.40, "inet_down_cost": 0.02667},   # $26.67/TB: out
            {**base, "id": 3, "machine_id": 13, "dph_total": 0.30, "inet_down_cost": 0.003, "inet_down": 120},   # slow: out
            {**base, "id": 4, "machine_id": 14, "dph_total": 0.70, "inet_down_cost": 0.005},     # $5/TB
        ]
        v._req = lambda method, path, body=None, timeout=60: {"offers": recorded}
        gpus = v.gpus()
        ids = [x["id"] for x in gpus[0].datacenters] if gpus else []
        check(ids == ["1", "4"], "Vast: an expensive-traffic offer and a slow line are dropped")
        check(gpus and abs(gpus[0].price - 0.60) < 1e-9 and gpus[0].extra == 0.0, "Vast: the cheapest fitting offer, traffic $0")
        check(gpus and "$0.33" in gpus[0].datacenters[1]["name"], "Vast: the offer's name shows the traffic cost (65 GB x $5/TB)")
        recorded.pop(0)
        gpus = v.gpus()
        check(gpus and abs(gpus[0].extra - 0.33) < 1e-9, "Vast: Gpu.extra is the first download's traffic")

    try:
        asyncio.run(go())
        vast_offers()   # after go(): importing the cloud package must not touch the state the run above set up
    finally:
        server.terminate()
        server.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL OK" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
