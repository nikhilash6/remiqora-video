# The cloud worker for ENGINE=remote (cloud/worker_server.py): the engine only - ComfyUI's core at the pinned commit,
# its venv by the same uv.lock as on Windows, our h3worker.py, and the lyrics tool (the song's words, a scene's voice:
# Demucs and faster-whisper are in that same lock). No studio, no frontend, no weights (they live on the pod's volume,
# start.sh fetches them on the first start; Whisper's and Demucs' the tool downloads there on its first run).
#   docker build -f cloud/Dockerfile -t remiqora-video-worker .        (from the repo's root)
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy
# ffmpeg: the lyrics tool decodes the song / the scene with it (Ubuntu's build, ~250 MB with its libraries)
# openssl: the server's own certificate when TLS=1 comes without one (worker_server.py tls_context)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl git libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

# the writer: llama.cpp's official CUDA build, the same version as install/manifest.json's Windows one, and its CUDA runtime
ADD --checksum=sha256:6c823fc011a1e98313dc7586119ea7d40725bb2076438b08b1d00ed0d827c435 \
    https://github.com/ggml-org/llama.cpp/releases/download/b11896/llama-b11896-bin-ubuntu-cuda-13.4-x64.tar.gz /tmp/llama.tgz
ADD --checksum=sha256:85f23440396e0f11765856e35275b7de8157e40f4d6cbd009a76af309c136523 \
    https://github.com/ggml-org/llama.cpp/releases/download/b11896/cudart-llama-b11896-bin-ubuntu-cuda-13.4-x64.tar.gz /tmp/cudart.tgz
RUN set -e; mkdir -p /tmp/l /opt/llama; tar -xzf /tmp/llama.tgz -C /tmp/l; tar -xzf /tmp/cudart.tgz -C /tmp/l; \
    bin=$(dirname "$(find /tmp/l -name llama-server -type f | head -1)"); cp -a "$bin"/. /opt/llama/; \
    find /tmp/l -name 'libcu*' -exec cp -a {} /opt/llama/ \; ; rm -rf /tmp/l /tmp/llama.tgz /tmp/cudart.tgz; \
    test -x /opt/llama/llama-server

WORKDIR /opt/rv
COPY engine/pyproject.toml engine/uv.lock engine/core.txt engine/
# the core: one commit, no history (core.txt: the repo, then the commit)
RUN set -e; repo=$(sed -n 1p engine/core.txt | tr -d '\r'); rev=$(sed -n 2p engine/core.txt | tr -d '\r'); \
    git init -q engine/ComfyUI && git -C engine/ComfyUI fetch -q --depth 1 "$repo" "$rev" \
    && git -C engine/ComfyUI checkout -q FETCH_HEAD && rm -rf engine/ComfyUI/.git
RUN cd engine && uv sync --frozen --no-install-project && uv cache clean

COPY app/engine/h3worker.py app/engine/h3nodes.py app/engine/
COPY tools/install_models.py tools/
COPY tools/lyrics/lyrics_worker.py tools/lyrics/
COPY install/manifest.json install/
COPY cloud/worker_server.py cloud/start.sh cloud/
RUN sed -i 's/\r$//' cloud/start.sh   # built from a Windows checkout (autocrlf), bash can't run CRLF

ARG IMAGE_VERSION=dev
ENV COMFY_DIR=/opt/rv/engine MODELS=/workspace/models WORK=/workspace/work REMOTE_PORT=8188 MODEL_SETS=h3,writer \
    LLAMA_BIN=/opt/llama/llama-server TOOLS_CACHE=/workspace/models/tools FAST_MODELS=/fast IMAGE_VERSION=$IMAGE_VERSION
EXPOSE 8188
CMD ["bash", "/opt/rv/cloud/start.sh"]
