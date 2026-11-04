"""Fill install/manifest.json's sha256 and bytes of the LTX 2.5 files from Hugging Face's file info (no download).
The repo is gated: the token comes from the usual places (HF_TOKEN, or `hf auth login`), never from this script.
    python tools/ltx25_sha.py"""
import json
from pathlib import Path

from huggingface_hub import HfApi

MANIFEST = Path(__file__).resolve().parent.parent / "install" / "manifest.json"
m = json.loads(MANIFEST.read_text(encoding="utf-8"))
files = m["sets"]["ltx25"]["files"]
info = {x.path: x for x in HfApi().get_paths_info("Lightricks/LTX-2.5", [f["path"] for f in files], expand=True)}
for f in files:
    x = info.get(f["path"])
    if not x or not getattr(x, "lfs", None):
        print("no info:", f["path"])
        continue
    f["sha256"], f["bytes"] = x.lfs.sha256, x.size
    print(f"{x.size / 2**30:6.2f} GiB  {x.lfs.sha256}  {f['path']}")
MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("written:", MANIFEST)
