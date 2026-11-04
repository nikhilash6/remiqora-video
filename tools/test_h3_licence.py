"""MiniMax H3's licence statement is required everywhere H3 is installed, without a GPU or a download: the studio's
download API, the cloud deploy/add-models API and tools/install_models.py all refuse H3 without the acceptance flag, and
let it through with it. A model of another licence needs no flag.

    .venv\\Scripts\\python.exe tools\\test_h3_licence.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from app import h3_licence, main, models_page  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


def refused(fn) -> bool:
    try:
        fn()
    except HTTPException as e:
        return e.status_code == 400 and "MiniMax H3" in str(e.detail)
    return False


print("the studio's download API")
slot = next(s for s in models_page.SLOTS if s.key == "H3_UNET")
repo, path = slot.sources[0]
ok(refused(lambda: main.models_download(main.DownloadRequest(repo=repo, path=path, slot=slot.key))), "H3 without the flag: 400")
ok(refused(lambda: main.models_download(main.DownloadRequest(repo=repo, path=path, slot="H3_REF_UNET", accept_h3=False))), "Ref2VA without the flag: 400")

print("the cloud API")
ok(refused(lambda: h3_licence.require(["writer", "h3"], False)), "a deploy with H3, no flag: 400")
ok(refused(lambda: h3_licence.require(["ref2va"], False)), "more sets with Ref2VA, no flag: 400")
ok(not refused(lambda: h3_licence.require(["h3", "ref2va"], True)), "with the flag it passes")
ok(not refused(lambda: h3_licence.require(["writer", "ltx23", "krea"], False)), "other sets need no flag")

print("tools/install_models.py")
manifest = json.loads((ROOT / "install" / "manifest.json").read_text(encoding="utf-8"))
ok(all(s in manifest["sets"] for s in h3_licence.SETS), "the licence module names real manifest sets")
with tempfile.TemporaryDirectory() as d:
    def run(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "install_models.py"), "--models-dir", d, "--json", "--plan", *extra],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
    r = run("--sets", "h3")
    ok(r.returncode == 1 and '"licence"' in r.stdout, "--sets h3 without --accept-h3: refused (code licence)")
    r = run("--sets", "ref2va")
    ok(r.returncode == 1 and '"licence"' in r.stdout, "--sets ref2va without --accept-h3: refused")
    r = run("--profile", "all")
    ok(r.returncode == 1 and '"licence"' in r.stdout, "--profile all without --accept-h3: refused")
    r = run("--sets", "h3", "--accept-h3")
    ok(r.returncode == 0 and '"licence"' not in r.stdout, "--sets h3 --accept-h3: planned")
    r = run("--sets", "writer")
    ok(r.returncode == 0, "--sets writer: no flag needed")

print("FAILED" if failed else "all passed")
sys.exit(1 if failed else 0)
