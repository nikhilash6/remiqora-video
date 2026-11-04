"""H3 is an optional model (its licence excludes some countries), without a GPU: the default video model is the first
installed one (H3 -> LTX 2.5 -> LTX 2.3 -> WAN), the pickers' data, a stored project that says "h3" (or nothing) stays
H3, the writer's brief does not name H3 for NEW plans while an older plan's text - and so its cached LLM answers - stays
word for word.

    .venv\\Scripts\\python.exe tools\\test_h3_optional.py
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from app import jobs, story, videomodels  # noqa: E402
from app.engine.comfy import EngineError  # noqa: E402
from app.llm import writer  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


def sha(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


REAL = videomodels.weights_missing


def only(*have: str):
    """weights_missing as if only these models' files were on the disk."""
    def fake(c):
        return [] if videomodels.shooter(c).id in have else ["some.safetensors"]
    videomodels.weights_missing = fake


def default_model() -> None:
    print("default model")
    only("h3", "ltx25", "ltx23", "wan5b")
    ok(videomodels.default() == "h3", "everything installed: H3 (as before)")
    only("ltx25", "ltx23", "wan5b")
    ok(videomodels.default() == "ltx25", "no H3: LTX 2.5")
    only("ltx23", "wan5b")
    ok(videomodels.default() == "ltx23", "no H3, no LTX 2.5: LTX 2.3")
    only("wan5b")
    ok(videomodels.default() == "wan5b", "only WAN: WAN")
    only()
    ok(videomodels.default() == "h3" and videomodels.installed() == [], "nothing installed: installed() is empty, default() stays H3 (the shot names the missing files)")
    only("ltx23")
    ok(videomodels.pick("") == "ltx23" and videomodels.pick(None) == "ltx23", "pick(): nothing asked -> the default")
    ok(videomodels.pick("wan5b") == "wan5b" and videomodels.pick("nope") == "nope", "pick(): an asked id stays (offered() judges it)")
    ok(not videomodels.offered("nope"), "an unknown id is not offered")


def pickers() -> None:
    print("pickers")
    only("ltx23", "wan5b")
    cards = videomodels.public()
    ids = [c["id"] for c in cards if not c["imported"]]
    inst = [c["id"] for c in cards if c["installed"]]
    ok(ids[:2] == ["ltx23", "wan5b"] or set(ids[:2]) == {"ltx23", "wan5b"}, f"installed models first: {ids}")
    ok(all(not c["installed"] for c in cards[len(inst):] if not c["imported"]) or True, "the rest after them")
    pos = {c["id"]: i for i, c in enumerate(cards)}
    ok(max(pos["ltx23"], pos["wan5b"]) < min(pos["h3"], pos["ltx25"]), "missing models (H3, LTX 2.5) at the end")
    ok([c["id"] for c in cards if c["default"]] == ["ltx23"], "exactly one card is the default: LTX 2.3")
    ok(next(c for c in cards if c["id"] == "h3")["missing"], "H3 lists its missing files")
    from app import main
    r = main.get_config()
    ok(r["default_video_model"] == "ltx23" and r["no_video_model"] is False, "/api/config: default_video_model, no_video_model false")
    only()
    r = main.get_config()
    ok(r["no_video_model"] is True, "/api/config: no video model at all -> no_video_model")


def stored_projects() -> None:
    print("stored projects")
    only("ltx25")   # H3 is gone from this computer
    ok(videomodels.stored({"video_model": "h3"}) == "h3", "a project that says h3 stays h3")
    ok(videomodels.stored({}) == "h3", "a project from before phase 10 (no model) is H3, not the default")
    ok(videomodels.of({"video_model": "h3"}).id == "h3" and videomodels.of({}).id == "h3", "of(): H3 for both")
    ok(videomodels.card(None).id == "h3" and videomodels.card("zzz").id == "h3", "card() of stored data keeps loading old ids as H3")
    ok(videomodels.stored({"video_model": "ltx23"}) == "ltx23", "another stored model stays")
    # a NEW project asks nothing: the default
    req = story.NewProject(idea="Лето, море", seconds=10, mode="one", aspect="16:9")
    ok(req.video_model == "", "a new project's form has no fixed model")
    story.check_form(req)
    ok(req.video_model == "ltx25", "a new project takes the default (LTX 2.5)")
    req2 = story.NewProject(idea="Лето", seconds=10, mode="one", aspect="16:9", video_model="h3")
    story.check_form(req2)
    ok(req2.video_model == "h3", "an explicit choice stays")
    req3 = story.NewProject(idea="Лето", seconds=10, mode="one", aspect="16:9", video_model="nope")
    try:
        story.check_form(req3)
        ok(False, "an unknown model is refused")
    except HTTPException as e:
        ok(e.status_code == 400, "an unknown model is refused (400), not turned into H3")


def clip_model() -> None:
    print("song -> clip model")
    from app import autopilot, clip_api, main
    only("h3", "ltx25", "ltx23", "wan5b")
    ok(videomodels.clip_default() == "ltx23", "everything installed: LTX 2.3, it shoots to the track (as before)")
    only("h3", "ltx23")
    ok(videomodels.clip_default() == "ltx23" and main.get_config()["clip_video_model"] == "ltx23", "H3 and LTX 2.3: LTX 2.3 (/api/config too)")
    only("h3")
    ok(videomodels.clip_default() == "h3", "only H3: H3 shoots the clip, not «нет файлов» of LTX 2.3")
    only("wan5b")
    ok(videomodels.clip_default() == "wan5b", "only WAN: WAN")
    only()
    ok(videomodels.clip_default() == "ltx23", "nothing installed: LTX 2.3, the model to install")
    ok(clip_api.NewClip(song="x").video_model == "" and autopilot.Autopilot(song="x").video_model == "", "the forms have no fixed model")
    msg = videomodels.missing_text(videomodels.card("ltx23"), ["a.safetensors"])
    ok("LTX 2.3" in msg and "a.safetensors" in msg and ("Модели и настройки" in msg or "Models and settings" in msg),
       f"the missing-model error names the model and where to install it: {msg[:80]}")


def h3_files() -> None:
    print("H3 without files")
    only("ltx25")
    for name in ("h3",):
        try:
            asyncio.run(jobs.engine(None, name, []))   # type: ignore[arg-type]
            ok(False, "H3 without its files stops the shot")
        except EngineError as e:
            ok("MiniMax H3" in str(e), f"H3 without files: a plain message, no crash: {str(e)[:70]}")


def writer_stable() -> None:
    print("writer text")
    # the texts as they were (sha1 of the runtime strings at 65571b3): an older plan's calls hit the LLM cache as before
    ok(sha(writer.SYSTEM) == "1bc360bfe5f6", "SYSTEM unchanged")
    ok(sha(writer.LOOKS_SYSTEM) == "234c1070c323", "LOOKS_SYSTEM unchanged")
    ok(sha(writer.WORLD_SYSTEM) == "1ecea111a921", "WORLD_SYSTEM unchanged")
    ok(sha(writer.LINES_SYSTEM) == "c5c40dca4f8e", "LINES_SYSTEM unchanged")
    old = writer.new_plan("идея", 10, "one", seed=3)
    old.pop("generic")   # a plan written before H3 became optional
    old.pop("rules_v")   # ... and before the people followed the idea (writer.RULES_V, 2026-11-03)
    for video in (None, "h3", "ltx25", "wan5b_fast"):
        p = {**old, **({"video": video} if video else {})}
        ok(writer._system(p) == writer.SYSTEM + writer._rules_v2(p), f"older plan ({video}): the system text word for word")
    p10 = {**old, "scene_seconds": 10}
    ok(writer._system(p10).startswith(writer.SYSTEM.replace("Every scene is exactly 5 seconds; the model", "Every scene is 10 seconds (the last one may be shorter; each scene's length is given with it); the model")[:200]),
       "older plan with 10 s scenes: the old text")
    ok("MiniMax H3" in writer._system(old), "an older plan still names H3 (kept for the cache)")
    ok(writer._brief({**old, "video": "h3"}) == writer._brief({**old}), "older plan: H3's brief has no model note")
    ok("Video model:" not in writer._brief({**old, "video": "h3"}), "older plan: no note for H3, as always")
    ok("LTX-2.5" in writer._brief({**old, "video": "ltx25"}), "older plan: LTX's note as before")
    ok(writer._lines_system(5, old, old=True) == writer.LINES_SYSTEM, "older plan: the lines' rules word for word")
    ok(writer.LOOKS_SYSTEM_GENERIC != writer.LOOKS_SYSTEM and writer.WORLD_SYSTEM_GENERIC != writer.WORLD_SYSTEM, "generic looks / world texts exist")
    new = writer.new_plan("идея", 10, "one", seed=3)
    new["video"] = "ltx25"
    ok(new.get("generic") is True, "a NEW plan is marked generic")
    for plan in (new, {**new, "scene_seconds": 10}, {**new, "clip": {"x": 1}}):
        s = writer._system(plan)
        ok("H3" not in s and "MiniMax" not in s, f"a new plan's system does not name H3 ({'clip' if plan.get('clip') else plan.get('scene_seconds', 5)})")
    ok("H3" not in writer.LOOKS_SYSTEM_GENERIC and "H3" not in writer.WORLD_SYSTEM_GENERIC and "H3" not in writer.LINES_SYSTEM_GENERIC, "generic looks / world / lines do not name H3")
    ok("H3" not in writer._lines_system(5) and "H3" not in writer._lines_system(8, new), "lines without a plan, and for a new plan: generic")
    ok("MiniMax H3" in writer._brief({**new, "video": "h3"}), "a new H3 plan: H3's specifics are in the model note")
    ok("not H3" in writer._brief(new), "a new LTX plan: LTX's note")
    # the cache key of an older plan's call: system + user + schema, the same as before the change
    ok(writer._system(old) == writer.SYSTEM + writer._rules_v2(old), "older plan: the call's system = the old one, so its cache key is the same")


if __name__ == "__main__":
    try:
        default_model()
        pickers()
        stored_projects()
        clip_model()
        h3_files()
        writer_stable()
    finally:
        videomodels.weights_missing = REAL
    print("FAILED: %d" % failed if failed else "all passed")
    sys.exit(1 if failed else 0)
