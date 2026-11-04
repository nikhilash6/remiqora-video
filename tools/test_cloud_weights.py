"""ENGINE=remote without local weights, without a GPU or a server: a model counts as installed by the cloud server's disk
(the list app/cloud/files.py keeps for the Models page), not by this computer's. weights_missing, installed(), default()
(what /api/config sends as default_video_model / no_video_model), the pictures' checks; a server with no list yet is
not second-guessed; ENGINE=local still looks only at this disk.

    .venv\\Scripts\\python.exe tools\\test_cloud_weights.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, models_page, picture, videomodels  # noqa: E402
from app.cloud import files  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


def slot(key: str) -> models_page.Slot:
    return next(s for s in models_page.SLOTS if s.key == key)


def disk(*keys: str) -> dict[str, int]:
    """The server's /models/files for these slots' files: 'kind folder/name' -> bytes."""
    return {f"{models_page.KINDS[slot(k).kind][0]}/{getattr(config, k)}": 1 for k in keys}


def cloud(listing: dict, source: str = "live") -> None:
    files.view = lambda force=False: {"files": listing, "source": source, "at": 0}


REAL_VIEW, REAL_ENGINE, REAL_FIND = files.view, config.ENGINE, models_page.find_file
LTX23 = videomodels.CARDS["ltx23"].keys
try:
    models_page.find_file = lambda key: None   # nothing on this computer
    config.ENGINE = "remote"

    print("the cloud disk holds LTX 2.3 only")
    cloud(disk(*LTX23))
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]) == [], "LTX 2.3: nothing missing")
    ok(videomodels.weights_missing(videomodels.CARDS["h3"]), "H3: its files are missing")
    ok(videomodels.installed() == ["ltx23"], f"installed() = {videomodels.installed()}")
    ok(videomodels.default() == "ltx23", "the default video model is LTX 2.3 (not H3, which is not there)")

    print("the list from the sets chosen at deploy (the pod is stopped, no live list yet)")
    cloud(disk(*LTX23), "manifest")
    ok(videomodels.installed() == ["ltx23"], "the deploy's sets count")

    print("a file in a sub-folder of its kind (loras/ltx23/...)")
    lst = disk(*LTX23)
    lora = f"loras/{config.LTX23_LORA}"
    lst.pop(lora)
    lst[f"loras/ltx23/{config.LTX23_LORA}"] = 1
    cloud(lst)
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]) == [], "found by its name under the kind")

    print("a file of the wrong kind does not count")
    lst = disk(*LTX23)
    lst.pop(f"checkpoints/{config.LTX23_CKPT}")
    lst[f"vae/{config.LTX23_CKPT}"] = 1
    cloud(lst)
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]) == [config.LTX23_CKPT], "the checkpoint is missing")

    print("the cloud disk is empty")
    cloud({})
    ok(videomodels.installed() == [], "nothing installed: /api/config says no_video_model")
    ok(picture.problem() is not None, "Krea 2: the picture says which files are missing")

    print("Krea 2 on the cloud disk")
    cloud(disk("KREA_UNET", "KREA_CLIP", "KREA_VAE"))
    ok(picture.problem() is None, "pictures can be made")

    print("no list at all yet (own server not answering, no deploy sets)")
    cloud({}, "none")
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]) == [], "not second-guessed: the shot goes to the server")

    print("ENGINE=local: only this computer's disk, the cloud list is not asked")
    config.ENGINE = "local"
    files.view = lambda force=False: (_ for _ in ()).throw(AssertionError("asked the cloud in local mode"))
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]), "LTX 2.3 missing here")
    models_page.find_file = lambda key: Path("x")
    ok(videomodels.weights_missing(videomodels.CARDS["ltx23"]) == [], "found here")
finally:
    files.view, config.ENGINE, models_page.find_file = REAL_VIEW, REAL_ENGINE, REAL_FIND

print("all ok" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
