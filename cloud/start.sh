#!/usr/bin/env bash
# The pod's start: the worker server. It fetches the weights of MODEL_SETS onto the volume itself, in the background
# (/health shows the progress, later starts only check names and sizes). REMOTE_TOKEN and HF_TOKEN (LTX 2.5 is gated)
# come from the studio when it creates the pod; RUNPOD_API_KEY (the pod-scoped key for the self-stop) from RunPod.
set -euo pipefail
mkdir -p "$MODELS" "$WORK"
exec /opt/rv/engine/.venv/bin/python -I /opt/rv/cloud/worker_server.py
