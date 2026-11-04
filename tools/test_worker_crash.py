"""jobs.shoot: a local worker that died mid-scene is started again and the scene is shot once more; a scene's own error
is not retried; a swap of models restarts a worker that still fills the RAM after free(). No GPU, no worker.

    .venv/Scripts/python.exe tools/test_worker_crash.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, jobs, sysinfo  # noqa: E402
from app.engine.comfy import COMFY, EngineError  # noqa: E402

failed = 0


def check(cond, what):
    global failed
    print(f"  {'ok ' if cond else 'FAIL'} {what}")
    failed += not cond


class Proc:
    def __init__(self, alive=True):
        self.alive, self.pid = alive, 4242

    def poll(self):
        return None if self.alive else 1


calls = {"run": 0, "stop": 0, "ensure": 0, "free": 0}


def fake(kind):
    async def f(*a, **k):
        calls[kind] += 1
    return f


async def no_engine(job, model, loras, ref):
    return []


def go(run):
    calls.update(run=0, stop=0, ensure=0, free=0)
    COMFY.run = run
    job = jobs.Job(id="t", kind="scene", params={})
    return asyncio.run(jobs.shoot(job, "p", 64, 64, 9, 1, [], None, "x", model="h3"))


config.ENGINE = "local"
jobs.engine = no_engine
COMFY.free, COMFY.stop, COMFY.ensure = fake("free"), fake("stop"), fake("ensure")


async def crash_once(graph, on_node, on_progress):
    calls["run"] += 1
    if calls["run"] == 1:
        COMFY.proc = Proc(alive=False)
        raise EngineError("Видео-воркер упал. Конец журнала data\\h3worker.log:\nWindows fatal exception: access violation")
    COMFY.proc = Proc()
    return {"9": {"gifs": [{"filename": "a.mp4", "subfolder": "", "type": "output"}]}}


COMFY.proc = Proc()
try:
    mp4, _ = go(crash_once)
    check(calls["run"] == 2 and mp4.endswith("a.mp4"), "a dead worker: the scene is shot once more")
except EngineError as e:
    check(False, f"a dead worker: retried ({e})")


async def scene_error(graph, on_node, on_progress):
    calls["run"] += 1
    raise EngineError("KSampler: bad prompt")


COMFY.proc = Proc()
try:
    go(scene_error)
    check(False, "a scene's own error is raised")
except EngineError:
    check(calls["run"] == 1, "a scene's own error (worker alive) is not retried")


async def always_dies(graph, on_node, on_progress):
    calls["run"] += 1
    COMFY.proc = Proc(alive=False)
    raise EngineError("Видео-воркер упал.")


COMFY.proc = Proc()
try:
    go(always_dies)
    check(False, "a worker that dies twice: the error goes up")
except EngineError:
    check(calls["run"] == 2, "a worker that dies twice: one retry only, then the error")

config.ENGINE = "remote"
COMFY.proc = Proc()
try:
    go(always_dies)
except EngineError:
    check(calls["run"] == 1, "remote engine: no local retry")
config.ENGINE = "local"

# the model swap: a bloated worker after free() is restarted
COMFY.proc, COMFY.work_pid = Proc(), 123
sysinfo.ram = lambda: {"used": 50.0, "total": 62.0}
for held, want in ((44.0, True), (12.0, False)):
    sysinfo.process_ram = lambda pid, h=held: h
    check(jobs._worker_bloated() is want, f"worker holding {held} GB of 62: restart = {want}")
config.ENGINE = "remote"
check(jobs._worker_bloated() is False, "remote engine: never bloated")
config.ENGINE = "local"

# the crash message: a line about low free RAM before the log's tail, only when it is low
from app.engine import worker  # noqa: E402
sysinfo.ram = lambda: {"used": 58.5, "total": 62.0}
msg = worker.crashed()
check(msg.index("3.5") < msg.index("h3worker.log") and ("62" in msg.split("h3worker.log")[0]), "low free RAM (3.5 of 62 GB): the hint before the log")
sysinfo.ram = lambda: {"used": 20.0, "total": 62.0}
check(worker.low_ram() == "" and worker.crashed().split("h3worker.log")[0].count("62") == 0, "42 GB free: no hint")
sysinfo.ram = lambda: None
check(worker.low_ram() == "", "RAM unknown (not Windows): no hint")

print("failed:", failed)
sys.exit(1 if failed else 0)
