"""A stand-in for RunPod's REST API v2 (the part app/cloud/providers/runpod.py uses) on this machine: a "pod" is a
Docker container of the worker image with this PC's GPU, a "network volume" a Docker volume. The studio is pointed at it
with RUNPOD_API=http://127.0.0.1:8833 and RUNPOD_PROXY=http://127.0.0.1:18188 - the whole deploy (disk, server, boot,
models, engine), the start, the stop and the delete run for real, without an account or money.

    .venv\\Scripts\\python.exe tools\\fake_runpod.py [--models E:\\models\\comfyui] [--port 8833]

--models: a weights folder mounted read-only into the pod (the server finds the sets there by name and size instead of
downloading 45 GB). Any API key works except "bad" (401).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8833)
ap.add_argument("--models", default="")
ap.add_argument("--pod-port", type=int, default=18188)
ARGS = ap.parse_args()

app = FastAPI()
PODS: dict[str, dict] = {}
VOLUMES: dict[str, dict] = {}
GPUS = [
    {"id": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "manufacturer": "NVIDIA", "memory": 24, "secure": True, "community": True,
     "price": {"secure": 0.69, "community": 0.34}, "availability": "HIGH",
     "dataCenters": [{"id": "LOCAL-1", "name": "This PC", "availability": "HIGH"}]},
    {"id": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "manufacturer": "NVIDIA", "memory": 32, "secure": True, "community": True,
     "price": {"secure": 0.94, "community": 0.69}, "availability": "MEDIUM",
     "dataCenters": [{"id": "LOCAL-1", "name": "This PC", "availability": "MEDIUM"}]},
    {"id": "NVIDIA L40S", "name": "L40S", "manufacturer": "NVIDIA", "memory": 48, "secure": True, "community": False,
     "price": {"secure": 0.86, "community": 0}, "availability": "LOW",
     "dataCenters": [{"id": "LOCAL-1", "name": "This PC", "availability": "LOW"}]},
    {"id": "NVIDIA A100 80GB PCIe", "name": "A100 PCIe", "manufacturer": "NVIDIA", "memory": 80, "secure": True, "community": True,
     "price": {"secure": 1.64, "community": 1.19}, "availability": "HIGH",
     "dataCenters": [{"id": "LOCAL-1", "name": "This PC", "availability": "HIGH"}]},
]


def auth(request: Request) -> None:
    key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not key or key == "bad":
        raise HTTPException(401, "unauthorized")


def docker(*args: str, check: bool = True) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise HTTPException(500, f"docker {args[0]}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def status(pid: str) -> str:
    if pid not in PODS:
        return "TERMINATED"
    st = docker("inspect", "-f", "{{.State.Status}}", f"fakepod-{pid}", check=False)
    return {"running": "RUNNING", "created": "STARTING", "restarting": "STARTING", "exited": "EXITED"}.get(st, "ERROR")


def pod_json(pid: str) -> dict:
    p = PODS[pid]
    return {"id": pid, "name": p["name"], "status": status(pid), "image": p["image"], "dataCenterId": "LOCAL-1",
            "gpu": {"id": p["gpu"], "count": 1}, "env": {k: "***" for k in p["env"]}, "ports": ["8188/http"]}


@app.get("/v2/catalog/gpus")
def gpus(request: Request):
    auth(request)
    return {"gpus": GPUS}


@app.post("/graphql")
async def graphql(request: Request):
    auth(request)
    return {"data": {"myself": {"clientBalance": 24.10}}}


@app.post("/v2/network-volumes", status_code=201)
async def create_volume(request: Request):
    auth(request)
    d = await request.json()
    vid = "vol" + uuid.uuid4().hex[:8]
    docker("volume", "create", f"fakevol-{vid}")
    VOLUMES[vid] = d
    return {"id": vid, "name": d["name"], "size": d["size"], "dataCenter": d["dataCenter"], "type": "STANDARD"}


@app.delete("/v2/network-volumes/{vid}", status_code=204)
def delete_volume(vid: str, request: Request):
    auth(request)
    if vid not in VOLUMES:
        raise HTTPException(404, "no volume")
    if any(p["volume"] == vid for p in PODS.values()):
        raise HTTPException(409, "the volume is in use")
    docker("volume", "rm", f"fakevol-{vid}", check=False)
    VOLUMES.pop(vid)
    return Response(status_code=204)


@app.post("/v2/pods", status_code=201)
async def create_pod(request: Request):
    auth(request)
    d = await request.json()
    if any(status(p) in ("RUNNING", "STARTING") for p in PODS):
        raise HTTPException(400, "this fake runs one pod at a time")
    pid = uuid.uuid4().hex[:12]
    vid = d["mounts"]["network"][0]["volumeId"]
    env = dict(d.get("env") or {})
    env["RUNPOD_POD_ID"] = pid
    args = ["run", "-d", "--name", f"fakepod-{pid}", "--gpus", "all", "-p", f"127.0.0.1:{ARGS.pod_port}:8188",
            "-v", f"fakevol-{vid}:/workspace"]
    if ARGS.models:
        args += ["-v", f"{ARGS.models}:/models-ro:ro"]
        env["MODELS"] = "/workspace/models:/models-ro"
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    docker(*args, d["image"])
    PODS[pid] = {"name": d["name"], "image": d["image"], "gpu": d["gpu"]["id"], "env": env, "volume": vid}
    return pod_json(pid)


@app.get("/v2/pods/{pid}")
def get_pod(pid: str, request: Request):
    auth(request)
    if pid not in PODS:
        raise HTTPException(404, "no pod")
    return pod_json(pid)


@app.post("/v2/pods/{pid}/action")
async def action(pid: str, request: Request):
    auth(request)
    if pid not in PODS:
        raise HTTPException(404, "no pod")
    a = (await request.json())["action"]
    name = f"fakepod-{pid}"
    if a == "stop":
        docker("stop", name)
    elif a == "start":
        docker("start", name)
    elif a == "restart":
        docker("restart", name)
    elif a == "terminate":
        docker("rm", "-f", name, check=False)
        PODS.pop(pid)
        return Response(status_code=204)
    else:
        raise HTTPException(422, a)
    return pod_json(pid)


if __name__ == "__main__":
    print(f"fake RunPod on 127.0.0.1:{ARGS.port}; pods on 127.0.0.1:{ARGS.pod_port}" + (f", models {ARGS.models}" if ARGS.models else ""), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")
