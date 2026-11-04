"""The graph's «Песня → клип» (app/graph.py: «Песня», «Сценарист клипа», «Развернуть в сцены», «Склейка по тактам»)
without a GPU or an LLM: a made-up song (its bars and words written by hand), the clip writer with a stand-in for the
LLM, the button's plan and keys against the graph's, the expansion, a second expansion over the user's changes, and
the cuts of the join on the song's beats.

    .venv\\Scripts\\python.exe tools\\test_graph_song.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.main as main  # noqa: E402,F401 - the routes, as the server has them
from app import autopilot as ap, clip_api, edit, graph, graph_io, jobs, looks, projects, render, song, videomodels  # noqa: E402
from app.llm import client as llm, writer  # noqa: E402
from app.planner import model_prompts, prompts  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


class Patch:
    """Stand-ins put on modules for one test, taken off after it."""

    def __init__(self):
        self.saved = []

    def __call__(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)
        self.saved.clear()


MODEL = SimpleNamespace(id="stand-in.gguf", name="stand-in", mmproj="mm.gguf")
SID = "5e5e00000001"     # the made-up song: 100 BPM (a beat 0.6 s, a bar 2.4 s), 40 s, words in its loud middle
BPM, DOWN, DUR = 100.0, 0.3, 40.0
BEAT = 60 / BPM
FPS = edit.FPS


def make_song() -> None:
    d = song.SONGS / SID
    shutil.rmtree(d, ignore_errors=True)
    (d / "lyrics").mkdir(parents=True)
    with wave.open(str(d / "song.wav"), "wb") as f:   # silence: nothing reads its sound here
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(b"\0\0" * int(8000 * DUR))
    beats = [round(DOWN + k * BEAT, 4) for k in range(int((DUR - DOWN) / BEAT) + 1)]
    bars = beats[::4]
    energy = [0.3 if t < 10 or t > 30 else 0.9 for t in bars]

    def sec(kind, b0, b1, e):
        return {"kind": kind, "start": bars[b0], "end": bars[b1] if b1 < len(bars) else DUR, "bar0": b0, "bar1": b1, "energy": e}
    a = {"duration": DUR, "bpm": BPM, "beat_s": BEAT, "bar_s": 4 * BEAT, "downbeat": DOWN, "beats": beats, "bars": bars,
         "bar_energy": energy, "bar_rms": energy, "sections": [sec("intro", 0, 4, 0.3), sec("drop", 4, 12, 0.9), sec("outro", 12, len(bars), 0.3)]}
    (d / "analysis.json").write_text(json.dumps(a), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"name": "Тестовая песня.wav"}, ensure_ascii=False), encoding="utf-8")
    lines = [{"start": 10.5 + 4 * k, "end": 13.5 + 4 * k, "text": t} for k, t in enumerate(["la la love", "dance all night", "la la love", "dance all night"])]
    (d / "lyrics" / "lyrics.json").write_text(json.dumps({"lines": lines, "language": "en", "words": []}), encoding="utf-8")


async def fake_write_clip(plan, photo=None, folder=None):
    """The clip writer's answer, always the same: a hero, a look, every scene written."""
    p = json.loads(json.dumps(plan))
    p.update(title_ru="Тест", style_en="live-action music video, neon light.", format_en="performance")
    if p.get("mode") != "none" and not p.get("cast"):
        p["cast"] = [{"gender": "female", "age": 25, "en": "a 25-year-old woman with long dark hair", "outfit_en": "a silver jacket",
                      "ru": "", "outfit_ru": ""}]
    for s in p["scenes"]:
        s.update(ru=f"Сцена {s['index'] + 1}.", beats_en=[f"The heroine moves to the beat, moment {s['index'] + 1}."],
                 place_en="A neon-lit warehouse.", framing="medium", camera="static", seed=1000 + s["index"])
        if s.get("shot") == "insert":
            s.update(beats_en=["A spinning disco ball throws light on the empty floor."], who=[])
    looks.apply(p)   # as the writer does: style_en from «Вид» first (look_style)
    return p


def _job(kind: str) -> jobs.Job:
    j = jobs.Job(id=f"t-{kind}", params={}, kind=kind, status="running", steps=0)
    j.started = __import__("time").time()
    return j


def _graph() -> tuple[str, dict]:
    """A graph project from the «Песня → клип» template, its song set as the page sets it, LTX 2.3 its model."""
    t = next(x for x in graph_io.templates() if x["id"] == "song-clip")
    from app import graph_api
    proj = graph_io.new_project("Песня → клип", "16:9", g=graph_api._fresh_ids({"nodes": t["nodes"], "edges": t["edges"]}))
    proj["settings"].update(video_model="ltx23", delivery="cinema", look="live_cinema", look_own="")
    projects.save(proj)
    pid = proj["id"]
    got = graph_api.nodes_song(pid, graph_api.SongPick(song=SID))
    g = graph.load(pid)
    ids = {n["type"]: n["id"] for n in g["nodes"]}

    def put(gg):
        graph.node(gg, ids["song"])["data"].update(song=SID, file=got["file"], name=got["name"], length="whole")
        graph.node(gg, ids["idea"])["data"]["text"] = "ночной клуб"
    graph.update(pid, put)
    return pid, ids


def run() -> None:
    print("the made-up song, the template")
    make_song()
    ok(song.analysis(SID).beat_s == BEAT and len(song.lines(SID)) == 4, "the song: its bars and words are known")
    ok(any(t["id"] == "song-clip" for t in graph_io.templates()), "the template «Песня → клип» is listed")
    pt = Patch()
    captured: list = []

    async def capture(plan, photo=None, folder=None):
        captured.append(json.loads(json.dumps(plan)))
        return await fake_write_clip(plan, photo, folder)
    pt(writer, "write_clip", capture)
    pt(llm, "find", lambda m=None: MODEL)
    from app import picture
    pt(picture, "edit_problem", lambda: "no FLUX.2 Klein")   # no places here (the button skips them too): catchup() has them
    pids = []
    try:
        pid, ids = _graph()
        pids.append(pid)
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        sa = graph.evaluate_node(c, ids["song"])
        ok(sa.state == "ready" and sa.song == SID and sa.piece == [0.0, DUR], "«Песня»: ready, the whole song")
        ok(graph.evaluate_node(c, ids["clip_writer"]).state == "empty", "«Сценарист клипа»: not written yet")
        st = graph.state(projects.load(pid), graph.load(pid))
        ok(st["nodes"][ids["song"]].get("piece") == [0.0, DUR], "the state shows the song's piece")

        print("the button's plan and the graph's")
        # the button: «Песня → клип»'s script stage (autopilot._write -> clip_api.start_clip) with the same song and idea
        req = ap.Autopilot(song=SID, idea="ночной клуб", aspect="16:9", video_model="ltx23", model=MODEL.id, delivery="cinema", look="live_cinema")
        bid, write, _, _ = clip_api.start_clip(ap._clip_req(req, 0.0, DUR))
        pids.append(bid)
        asyncio.run(write(_job("write")))
        button = projects.load(bid)
        ap.sing_cameras(button["plan"])
        button["plan"] = __import__("app.planner.check", fromlist=["plan"]).plan(button["plan"])
        projects.save(button)
        g = graph.load(pid)
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(seed=button["plan"]["seed"]))
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        data = asyncio.run(graph.write_clip(SimpleNamespace(model=MODEL), projects.load(pid), c, graph.node(c.g, ids["clip_writer"])))
        ok(len(captured) == 2 and captured[0] == captured[1], "the writer gets the same plan from the button and from the graph")
        bp, gp = button["plan"], data["plan"]
        ok([s["seconds"] for s in bp["scenes"]] == [s["seconds"] for s in gp["scenes"]], f"the same scenes on the bars ({len(gp['scenes'])})")
        ok([s.get("shot") for s in bp["scenes"]] == [s.get("shot") for s in gp["scenes"]]
           and any(s.get("shot") == "sing" for s in gp["scenes"]) and any(s.get("shot") == "insert" for s in gp["scenes"]),
           "the same singing scenes and inserts")
        ok([s.get("camera") for s in bp["scenes"]] == [s.get("camera") for s in gp["scenes"]], "the singers' cameras as the button's")
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(data))
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        ok(graph.evaluate_node(c, ids["clip_writer"]).state == "ready", "«Сценарист клипа»: written")
        graph.update(pid, lambda gg: graph.node(gg, ids["idea"])["data"].update(text="ночной клуб, дождь"))
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        ok(graph.evaluate_node(c, ids["clip_writer"]).state == "stale", "another idea: the plan is stale")
        graph.update(pid, lambda gg: graph.node(gg, ids["idea"])["data"].update(text="ночной клуб"))

        print("«Развернуть в сцены»")
        g = graph.expand(pid, ids["clip_writer"])
        n_sc = len(gp["scenes"])
        mine = [n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"]]
        prompts_ = sorted((n for n in mine if n["type"] == "prompt"), key=lambda n: n["data"]["scene"])
        scenes = sorted((n for n in mine if n["type"] == "scene"), key=lambda n: n["data"]["scene"])
        ok(len(prompts_) == n_sc and len(scenes) == n_sc, f"{n_sc} chains «Промт → Сцена»")
        ok(all(abs(n["data"]["seconds"] - s["seconds"]) < 1e-6 for n, s in zip(scenes, gp["scenes"])), "each scene as long as its bars")
        ok(all(abs(n["data"]["cut"]["start"] - s["start"]) < 1e-6 and abs(n["data"]["cut"]["end"] - s["end"]) < 1e-6
               for n, s in zip(scenes, gp["scenes"])), "each scene knows its place in the song (`cut`)")
        edges = g["edges"]
        ok(all(any(e["source"] == p["id"] and e["target"] == s["id"] and e["targetHandle"] == "prompt" for e in edges)
               for p, s in zip(prompts_, scenes)), "each prompt feeds its scene")
        ok(all(p["position"]["x"] < s["position"]["x"] and p["position"]["y"] == s["position"]["y"] for p, s in zip(prompts_, scenes)),
           "the prompt left of its scene")
        pos = [(n["position"]["x"], n["position"]["y"]) for n in mine]
        ok(len(set(pos)) == len(pos), "no two nodes on one place")
        rows = {}
        for s, n in zip(gp["scenes"], scenes):
            rows.setdefault(s.get("chapter"), set()).add(n["position"]["y"])
        ok(all(len(v) >= 1 for v in rows.values()) and len({min(v) for v in rows.values()}) == len(rows), "a part of the song starts its own row")
        mon = next(n for n in mine if n["type"] == "montage")
        ok(mon["data"].get("beats") and mon["data"]["order"] == [n["id"] for n in scenes], "«Склейка по тактам»: every scene, in order")
        ok(any(e["source"] == ids["song"] and e["target"] == mon["id"] and e["targetHandle"] == "track" for e in edges), "the song is its track")
        ok(any(e["source"] == mon["id"] and graph.node(g, e["target"])["type"] == "output" for e in edges), "… into an «Выход»")
        ok(all(any(e["source"] == ids["song"] and e["target"] == n["id"] and e["targetHandle"] == "voice" for e in edges) for n in scenes),
           "LTX 2.3: every scene shot to the song («голос»)")
        portrait = next((n for n in mine if n["type"] == "picture" and n["data"].get("scene") is None), None)
        frames_ = [n for n in mine if n["type"] == "picture" and n["data"].get("scene") is not None]
        ok(portrait is not None and frames_ and all(any(e["source"] == portrait["id"] and e["target"] == f["id"] and e["targetHandle"] == "ref"
                                                       for e in edges) for f in frames_), "no hero's photo: a portrait, the frames drawn from it")
        inserts = [s["index"] for s in gp["scenes"] if s.get("shot") == "insert"]
        ok(not any(f["data"]["scene"] in inserts for f in frames_), "an insert without a face: no frame, the scene from text")

        print("the graph's keys are the button's")
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        pv = render.preview(projects.load(bid))
        same = []
        for s in gp["scenes"]:
            if s["index"] not in inserts:
                continue
            sn = scenes[s["index"]]
            pnode = prompts_[s["index"]]
            p_ok = pnode["data"]["text"] == model_prompts.for_model("ltx23", bp, bp["scenes"][s["index"]])
            k_ok = graph.evaluate_node(c, sn["id"]).key == pv["keys"][s["index"]]
            if not (p_ok and k_ok):
                print("      scene", s["index"], "prompt", p_ok, "key", k_ok, "\n      graph:  ", pnode["data"]["text"],
                      "\n      button: ", model_prompts.for_model("ltx23", bp, bp["scenes"][s["index"]]))
            same.append(p_ok and k_ok)
        ok(same and all(same), f"a scene from text: the button's prompt and cache key ({len(same)} scenes)")
        # the button's clip itself: every scene's key as render always made it - the LTX paragraph, the scene's seed,
        # its bars' frames, the song from its bar (nothing of the refactor reaches them)
        bproj = projects.load(bid)
        tr = projects.folder(bid) / bproj["settings"]["track"]["file"]
        w, h = render.size(bproj["settings"])
        want = [render.scene_key(model_prompts.for_model("ltx23", bp, s), int(s["seed"]), w, h,
                                 videomodels.frames(videomodels.card("ltx23"), float(s["seconds"])), [], None, [], "ltx23", tr,
                                 round(float(s["start"]), 4)) for s in bp["scenes"]]
        ok(pv["keys"] == want, f"the button's clip: its keys as before ({len(want)} scenes)")

        print("again over the user's changes")
        g = graph.load(pid)
        intro_p = {"id": "uintro_p", "type": "prompt", "position": {"x": -900, "y": 0}, "data": {"title": "Моё вступление", "text": "A city at night, slow aerial shot."}}
        intro_s = {"id": "uintro_s", "type": "scene", "position": {"x": -600, "y": 0}, "data": {"title": "Вступление", "seconds": 5, "takes": [7], "take": 0}}
        lora = {"id": "ulora", "type": "lora", "position": {"x": -600, "y": 400}, "data": {"items": []}}

        def mine_edit(gg):
            gg["nodes"] += [intro_p, intro_s, lora]
            gg["edges"] += [{"id": "u1", "source": "uintro_p", "sourceHandle": "text", "target": "uintro_s", "targetHandle": "prompt"},
                            {"id": "u2", "source": "uintro_s", "sourceHandle": "clip", "target": mon["id"], "targetHandle": "clips"},
                            {"id": "u3", "source": "ulora", "sourceHandle": "lora", "target": scenes[1]["id"], "targetHandle": "lora"}]
            m = graph.node(gg, mon["id"])
            m["data"]["order"] = ["uintro_s"] + m["data"]["order"]
            graph.node(gg, prompts_[0]["id"])["data"]["text"] = "My own first scene."
        graph.update(pid, mine_edit)
        try:
            graph.expand(pid, ids["clip_writer"])
            ok(False, "an edited node: the page is asked")
        except graph.Edited as e:
            ok(e.names in (["Сцена 1"], ["Scene 1"]), f"an edited node: the page is asked (only that one named: {e.names})")
        g = graph.expand(pid, ids["clip_writer"], "keep")
        ok(graph.node(g, prompts_[0]["id"])["data"]["text"] == "My own first scene.", "«keep»: the user's text stays")
        ok(all(graph.node(g, x) for x in ("uintro_p", "uintro_s", "ulora")), "the user's nodes stay")
        ok(any(e["source"] == "ulora" and e["target"] == scenes[1]["id"] for e in g["edges"]), "the user's wire into a scene stays")
        mo = graph.node(g, mon["id"])["data"]["order"]
        ok(mo[0] == "uintro_s" and mo[1:] == [n["id"] for n in scenes], "the intro stays before the clip")
        ok(len([n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"] and n["type"] == "scene"]) == n_sc, "no chain twice")
        g = graph.expand(pid, ids["clip_writer"], "replace")
        ok(graph.node(g, prompts_[0]["id"])["data"]["text"] == prompts_[0]["data"]["text"], "«replace»: the generated text again")
        # a rewrite with a scene less: its chain goes, the user's nodes stay
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"]["plan"]["scenes"].pop())
        g = graph.expand(pid, ids["clip_writer"])
        ok(len([n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"] and n["type"] == "scene"]) == n_sc - 1
           and not graph.node(g, scenes[-1]["id"]) and graph.node(g, "uintro_s"), "a scene less: its chain gone, the intro stays")
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(data))
        g = graph.expand(pid, ids["clip_writer"])

        print("«Склейка по тактам»: the cuts on the beats")
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        ed = graph.montage_of(c, mon["id"])
        track = graph.montage_track(c, mon["id"], ed)
        ok(track and track["scene_volume"] == 0 and track["volume"] == 1.0, "the song alone, at full level")
        zero = track["offset"] - track["delay"]   # the song's time at the video's start
        clips = ed["clips"]
        intro = next(x for x in clips if x["scene"] == "uintro_s")
        ok(abs(intro["dur"] - 8 * BEAT) < 1 / FPS + 1e-6, f"the 5 s intro: whole beats (8 × {BEAT} s)")
        # the whole song starts at 0 (not on its first bar): the beats counted from where the clip's first scene sits
        first = next(x for x in clips if graph.node(c.g, x["scene"])["data"].get("cut"))
        t0 = zero + first["start"]
        on = []
        for x in clips:
            t = zero + x["start"]
            grid = t - DOWN if t > t0 + 1e-6 else t0 - t   # after it the song's bars; before it whole beats back
            on.append(abs(grid / BEAT - round(grid / BEAT)) * BEAT <= 1 / FPS + 1e-6)
            if not on[-1]:
                print("      off the beat:", x["scene"], x["start"], round(t, 4), x["dur"])
        ok(all(on), f"every cut on a beat of the song ({len(clips)} clips)")
        bar_ok = []
        for x in clips:
            n = graph.node(c.g, x["scene"])
            if n["data"].get("cut"):
                bar_ok.append(abs(zero + x["start"] - n["data"]["cut"]["start"]) <= 1 / FPS + 1e-6)
        ok(bar_ok and all(bar_ok), "each clip's scene on its own bar of the song")
        ok(track["delay"] > 0 and track["offset"] == 0, "an intro before the song: the song comes in after it (`delay`)")
        ok(graph.evaluate_node(c, mon["id"]).state == "wait", "nothing shot yet: the join waits for its scenes")

        print("an older graph stays as it was")
        g2 = graph_io.starter()
        g2["nodes"][0]["data"]["text"] = "A woman walks in the rain."
        p2 = graph_io.new_project("старый", "16:9", g=g2)
        pids.append(p2["id"])
        c2 = graph.Ctx(projects.load(p2["id"]), graph.load(p2["id"]))
        sc = next(n for n in c2.g["nodes"] if n["type"] == "scene")
        want = render.scene_key("A woman walks in the rain.", graph.seed_of(sc), c2.w, c2.h, videomodels.frames(c2.card, 5), [], None, [], c2.card.id,
                                end=None, fit=None)
        ok(graph.evaluate_node(c2, sc["id"]).key == want, "a scene without a model or bars: its key as before")
        ok(graph.seconds_of(sc) == 5 and isinstance(graph.seconds_of({"data": {"seconds": 7}}), int), "its length: whole seconds, 3-15")
        mn = {"id": "m", "type": "montage", "position": {"x": 0, "y": 0}, "data": {}}
        g3 = graph.load(p2["id"])
        g3["nodes"].append(mn)
        c3 = graph.Ctx(projects.load(p2["id"]), g3)
        ok(graph.montage_track(c3, "m", {"clips": []}) is None, "a montage with nothing on «трек»: no track, its key as before")
    finally:
        pt.undo()
        for p in pids:
            projects.remove(p)
        shutil.rmtree(song.SONGS / SID, ignore_errors=True)


PLACES = [("Склад", "A neon-lit warehouse with concrete floors and steel beams, cold blue light at night."),
          ("Крыша", "A flat rooftop at night above the city lights, wet tar and a low parapet.")]


async def fake_places(client, plan, seed=None):
    """The writer's grouping, always the same: the new scenes' first half in the warehouse, the rest on the roof."""
    new = [s["index"] for s in plan["scenes"] if s.get("kind") == "new"]
    half = len(new) // 2
    return [{"name_ru": PLACES[0][0], "en": PLACES[0][1], "scenes": [i + 1 for i in new[:half]]},
            {"name_ru": PLACES[1][0], "en": PLACES[1][1], "scenes": [i + 1 for i in new[half:]]}]


class FakeSession:
    """llm.session for the button's «Собрать места»: no server, the model's name only."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return SimpleNamespace(model=MODEL)

    async def __aexit__(self, *a):
        return False


def catchup() -> None:
    """The graph catches up with the button (2026-11-03): «Места», the pre-rolls of the scenes from a picture, the face
    check with reshoots before the montage, the song on the montage editor's «Музыка» lane; an older graph as it was."""
    from app import checks, frames, picture, places
    from app.planner import clip as clip_plan_mod
    print("the graph catches up: places")
    make_song()
    pt = Patch()
    asked: list = []

    async def ask(client, plan, seed=None):
        asked.append(places._ask(plan)[0])
        return await fake_places(client, plan, seed)
    pt(writer, "write_clip", fake_write_clip)
    pt(llm, "find", lambda m=None: MODEL)
    pt(llm, "session", FakeSession)
    pt(picture, "edit_problem", lambda: None)
    pt(picture, "problem", lambda: None)
    pt(places, "ask", ask)
    pids = []
    try:
        pid, ids = _graph()
        pids.append(pid)
        # the button: its script, then its stage «места» (clip_api.group_places -> places.group -> places.ask)
        req = ap.Autopilot(song=SID, idea="ночной клуб", aspect="16:9", video_model="ltx23", model=MODEL.id, delivery="cinema", look="live_cinema")
        bid, write, _, _ = clip_api.start_clip(ap._clip_req(req, 0.0, DUR))
        pids.append(bid)
        asyncio.run(write(_job("write")))
        bp = projects.load(bid)
        ap.sing_cameras(bp["plan"])
        projects.save(bp)
        asyncio.run(clip_api.group_places(_job("autopilot"), bid))
        bp = projects.load(bid)["plan"]
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(seed=bp["seed"]))
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        data = asyncio.run(graph.write_clip(SimpleNamespace(model=MODEL), projects.load(pid), c, graph.node(c.g, ids["clip_writer"])))
        gp = data["plan"]
        ok(len(asked) == 2 and asked[0] == asked[1], "the writer is asked for the places as the button asks (places._ask)")
        ok([(x["name_ru"], x["en"]) for x in gp.get("places") or []] == [(x["name_ru"], x["en"]) for x in bp.get("places") or []]
           and len(gp["places"]) == 2, "the graph's plan has the button's places")
        name_of = lambda p, s: (places.of_scene(p, s) or {}).get("name_ru")  # noqa: E731
        ok([name_of(gp, s) for s in gp["scenes"]] == [name_of(bp, s) for s in bp["scenes"]] and all(name_of(gp, s) for s in gp["scenes"] if s["kind"] == "new"),
           "every new scene in the button's place")
        ok(data.get("v") == graph.CLIP_V, "a plan written now is marked (`v`)")
        # a failure is noted, as the button's stage: no places, the frames without them
        async def broken(client, plan, seed=None):
            raise graph.EngineError("no places")
        pt(places, "ask", broken)
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        bad = asyncio.run(graph.write_clip(SimpleNamespace(model=MODEL), projects.load(pid), c, graph.node(c.g, ids["clip_writer"])))
        ok(not bad["plan"].get("places") and bad["plan"]["clip"].get("places_error") == "no places"
           and not any(s.get("place") for s in bad["plan"]["scenes"]), "the places fail: noted on the clip, the scenes without places")
        pt(places, "ask", ask)
        # a rewrite keeps the places' ids by their names: their «Картинка» nodes stay
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(data))
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        again = asyncio.run(graph.write_clip(SimpleNamespace(model=MODEL), projects.load(pid), c, graph.node(c.g, ids["clip_writer"])))
        ok([x["id"] for x in again["plan"]["places"]] == [x["id"] for x in gp["places"]], "a rewrite: the places keep their ids")

        print("«Развернуть в сцены» with the places")
        g = graph.expand(pid, ids["clip_writer"])
        mine = [n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"]]
        pl_nodes = {n["data"]["place"]: n for n in mine if n["type"] == "picture" and n["data"].get("place")}
        ok(len(pl_nodes) == 2 and all(n["data"]["model"] == "krea2" and n["data"]["prompt"] == places.prompt_of(gp, places.by_id(gp, k))
                                      for k, n in pl_nodes.items()), "a «Картинка» per place: Krea 2, the button's prompt (an empty set)")
        portrait = next(n for n in mine if n["type"] == "picture" and n["data"].get("scene") is None and not n["data"].get("place"))
        fr = {n["data"]["scene"]: n for n in mine if n["type"] == "picture" and isinstance(n["data"].get("scene"), int)}
        refs = lambda nid: [(e["source"], e["sourceHandle"]) for e in g["edges"] if e["target"] == nid and e["targetHandle"] == "ref"]  # noqa: E731
        hero_sc = [s for s in gp["scenes"] if s["kind"] == "new" and not frames.empty(gp, s)]
        empty_sc = [s for s in gp["scenes"] if s["kind"] == "new" and frames.empty(gp, s)]
        ok(hero_sc and all(refs(fr[s["index"]]["id"]) == [(portrait["id"], "image"), (pl_nodes[s["place"]]["id"], "image")] for s in hero_sc),
           "a frame with the hero: the face first, then its place's picture (frames.draw_one's order)")
        withphoto = json.loads(json.dumps(gp))
        withphoto["cast"][0]["photo"] = "hero.png"
        okp = []
        for s in hero_sc:
            names = [prompts.speaker(withphoto, k + 1) for k in frames.frame_people(withphoto, s)][:3]
            outfits = [(withphoto["cast"][k].get("outfit_en") or "") for k in frames.frame_people(withphoto, s)][:3]
            okp.append(fr[s["index"]]["data"]["prompt"] == frames.prompt_of(withphoto, s, names, True, False, outfits))
        ok(okp and all(okp) and "second image" in fr[hero_sc[0]["index"]]["data"]["prompt"], "... its prompt is the button's, the place «in the second image»")
        ok(empty_sc and all(refs(fr[s["index"]]["id"]) == [(pl_nodes[s["place"]]["id"], "image")]
                            and fr[s["index"]]["data"]["prompt"] == frames.empty_prompt(withphoto, s, True) for s in empty_sc),
           "an insert without a face: its frame drawn in its place, the button's empty frame")
        scn = {n["data"]["scene"]: n for n in mine if n["type"] == "scene"}
        ok(all(any(e["source"] == fr[i]["id"] and e["target"] == scn[i]["id"] and e["targetHandle"] == "start" for e in g["edges"]) for i in fr),
           "every frame is its scene's start")

        print("pre-rolls of the scenes from a picture")
        pre = clip_plan_mod.PREROLL
        ok(gp["clip"].get("preroll") == pre and all(scn[i]["data"]["cut"].get("pre") == pre for i in fr)
           and not any(scn[s["index"]]["data"]["cut"].get("pre") for s in gp["scenes"] if s["index"] not in fr),
           f"a scene from a picture: {pre} frames before its bar (`cut.pre`); the others none")
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        card = videomodels.card("ltx23")
        same = []
        for i in fr:
            n = scn[i]
            cut = n["data"]["cut"]
            secs, got = graph.scene_frames(c, n, card)
            # the button's clip with this scene: its frames (render.scene_spec) and its pre-roll (render.preroll)
            bplan = {"clip": {"preroll": pre, "from": 0}}
            bs = {"start": cut["start"], "end": cut["end"], "start_photo": "frame.png", "kind": "new", "seconds": n["data"]["seconds"]}
            blen = render.scene_length({"video_model": "ltx23"}, render.scene_seconds(bs) + render.preroll(bplan, bs) / FPS, card)
            same.append(videomodels.frames(card, secs) == blen and got == render.preroll(bplan, bs, blen))
        ok(same and all(same), f"the shot's frames and pre-roll as the button's clip ({len(same)} scenes)")
        # the song's piece starts the pre-roll earlier (the key's track start), the montage starts after it
        first = min(i for i in fr if scn[i]["data"]["cut"]["start"] > 1)   # at the song's very start: no room before it
        n = scn[first]
        z = scn[min(fr)]
        ok(z["data"]["cut"]["start"] > 1 or graph.scene_frames(c, z, card)[1] == int(z["data"]["cut"]["start"] * FPS + 1e-6),
           "a pre-roll never before the song's start")
        secs, got = graph.scene_frames(c, n, card)
        tr, at, _ = graph.scene_track(c, n, card, got)
        ok(got and abs(at - (n["data"]["cut"]["start"] - got / FPS)) < 1e-3, "its piece of the song starts that many frames earlier")
        ed = graph.montage_of(c, next(x["id"] for x in mine if x["type"] == "montage"))
        cl = next(x for x in ed["clips"] if x["scene"] == n["id"])
        ok(abs(cl["in"] - got / FPS) <= 1 / FPS + 1e-6, "in the montage it starts after its still start (`in` = the pre-roll)")
        zero = [graph.node(c.g, x["scene"])["data"]["cut"]["start"] - x["start"] for x in ed["clips"]]   # the song's time at 0
        ok(max(zero) - min(zero) <= 1 / FPS + 1e-6, f"every clip still on its own bar ({len(zero)} clips)")
        a_old = graph.scene_frames(c, {"id": "x", "data": {"seconds": 2.4, "cut": {"start": 0.3, "end": 2.7}}}, card)
        ok(a_old == (2.4, 0), "a scene without `cut.pre`: its length as before, no pre-roll")

        print("the face check before the montage")
        mon = next(x for x in g["nodes"] if x["type"] == "montage" and x["data"].get("from") == ids["clip_writer"])
        ok(mon["data"].get("faces") is True and any(e["source"] == portrait["id"] and e["target"] == mon["id"] and e["targetHandle"] == "face"
                                                     for e in g["edges"]), "«Склейка по тактам»: «Проверять лица» on, the hero's face on «лицо»")
        judged = [s["index"] for s in gp["scenes"] if ap.face_judged(gp, s)]
        ok(judged and all(bool(scn[s["index"]]["data"]["cut"].get("face")) == (s["index"] in judged) for s in gp["scenes"]),
           f"the scenes the button's audit judges are marked ({len(judged)})")
        made: dict = {}

        async def fake_make(job, c, x, a):   # «shot»: the files where the node's key says
            a.path.parent.mkdir(parents=True, exist_ok=True)
            a.path.write_bytes(b"x")
            if c.by[x]["type"] == "scene":
                a.png.write_bytes(b"x")
                made[a.path.stem] = (x, graph.seed_of(c.by[x]))
            if c.by[x]["type"] in ("montage", "join"):
                a.path.with_suffix(".json").write_text(json.dumps({"cuts": []}), encoding="utf-8")
        weak, never = scn[judged[0]]["id"], scn[judged[-1]]["id"]
        seen: dict = {}

        def fake_sim(video, portrait_path):   # the weak scene's first take unlike, its next like; one never like (a later take a bit likelier)
            sid, seed = made[Path(video).stem]
            order = seen.setdefault(sid, [])
            if seed not in order:
                order.append(seed)
            if sid == never:
                return 0.1 + 0.01 * order.index(seed)
            return 0.12 if sid == weak and order.index(seed) == 0 else 0.55
        pt(graph, "_make", fake_make)
        pt(checks, "hero_similarity", fake_sim)
        pt(edit, "shot_seconds", lambda mp4, unshot=None: (unshot or 121) / FPS)   # a «shot» has no frames to count
        job = _job("graph")
        res = asyncio.run(graph.run(job, pid, [mon["id"]], None))
        g = graph.load(pid)
        tk = lambda sid: graph.node(g, sid)["data"]["takes"]  # noqa: E731
        ok(not res["errors"] and job.progress.get("faces", {}).get("judged") == len(judged), f"the run judged the faces ({job.progress.get('faces')})")
        ok(len(tk(weak)) == 2 and graph.seed_of(graph.node(g, weak)) == tk(weak)[1], "an unlike scene: a take more, the like one chosen")
        ok(len(tk(never)) == 1 + ap.FACE_ROUNDS, f"never like: {ap.FACE_ROUNDS} rounds of reshooting, as the button")
        ok(graph.seed_of(graph.node(g, never)) == seen[never][-1] == tk(never)[-1], "... and its likest take stays chosen")
        others = [scn[i]["id"] for i in judged if scn[i]["id"] not in (weak, never)]
        ok(all(len(tk(x)) == 1 for x in others), "a like scene is shot once")
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        ok(graph.evaluate_node(c, mon["id"]).state == "ready", "the montage made of the chosen takes")
        mo = graph.montage_of(c, mon["id"])
        ok(next(x for x in mo["clips"] if x["scene"] == weak)["chosen"] == tk(weak)[1], "... the like take in it")
        # the button's audit (the same rounds, now face_rounds): its scenes' seeds as before
        bproj = projects.load(bid)
        sids = [s["id"] for s in bproj["plan"]["scenes"]][:2]
        rounds = {"n": 0}

        def b_scores(p):
            cur = projects.load(p)
            return {s["id"]: (s["seed"], 0.1 if s["id"] == sids[0] and rounds["n"] == 0 else 0.5) for s in cur["plan"]["scenes"] if s["id"] in sids}

        async def b_run(job, p):
            rounds["n"] += 1
            return {"complete": True}
        pt(ap, "_scores", b_scores)
        pt(render, "run", b_run)
        r = asyncio.run(ap._faces(_job("autopilot"), bid, {"complete": True}))
        after = projects.load(bid)["plan"]["scenes"]
        ok(r.get("complete") and len(after[0]["takes"]) == 2 and after[0]["seed"] == after[0]["takes"][1] and not after[1].get("takes"),
           "the button's audit: an unlike scene shot again, the like take kept, the others untouched")

        print("the montage editor's «Музыка» lane")
        from app import graph_api
        v = graph_api._montage_view(projects.load(pid), mon["id"])
        t = v["track"]
        ok(t and t["name"] == "Тестовая песня" and abs(t["duration"] - DUR) < 0.01 and t["beats"] and t["node"] == ids["song"]
           and t["scene_volume"] == 0 and "offset" in t and "delay" in t, f"the song «по тактам» on the lane ({t and t['name']}, from {t and t['offset']} s)")
        c = graph.Ctx(projects.load(pid), graph.load(pid))
        ok({k: t[k] for k in ("offset", "delay", "volume", "file")} == {k: graph.montage_track(c, mon["id"], graph.montage_of(c, mon["id"]))[k]
                                                                         for k in ("offset", "delay", "volume", "file")}, "... as the montage mixes it")

        print("«Развернуть в сцены»: a look drawn by every frame")
        keep_plan = json.loads(json.dumps(graph.node(graph.load(pid), ids["clip_writer"])["data"]["plan"]))

        def drawn(gg):   # the same script in «Акварель», without places: every new scene a «Картинка»
            p = graph.node(gg, ids["clip_writer"])["data"]["plan"]
            p.update(look="watercolor", looks_v=looks.LOOKS_V, places=[])
            p["style_en"] = looks.style_for(p, p.get("style_en", ""))
            for s in p["scenes"]:
                s.pop("place", None)
        graph.update(pid, drawn)
        wp = graph.node(graph.load(pid), ids["clip_writer"])["data"]["plan"]
        g = graph.expand(pid, ids["clip_writer"], "replace")
        mine = [n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"]]
        fr = {n["data"]["scene"]: n for n in mine if n["type"] == "picture" and isinstance(n["data"].get("scene"), int)}
        scn = {n["data"]["scene"]: n for n in mine if n["type"] == "scene"}
        pr = {n["data"]["scene"]: n for n in mine if n["type"] == "prompt" and isinstance(n["data"].get("scene"), int)}
        new_i = [s["index"] for s in wp["scenes"] if s["kind"] == "new"]
        ok(looks.needs_frame(wp) and new_i and set(fr) == set(new_i), f"every new scene has its «Картинка» ({len(fr)} of {len(new_i)})")
        ok(all(any(e["source"] == fr[i]["id"] and e["target"] == scn[i]["id"] and e["targetHandle"] == "start" for e in g["edges"]) for i in fr),
           "... and starts from it")
        ins = [s for s in wp["scenes"] if s["kind"] == "new" and frames.empty(wp, s)]
        ok(ins and all(fr[s["index"]]["data"]["model"] == "krea2" and fr[s["index"]]["data"]["prompt"] == frames.empty_prompt(wp, s, False)
                       and "not a photograph" in fr[s["index"]]["data"]["prompt"] for s in ins), "an insert without a place: Krea 2, the empty frame in the look")
        hero = [s for s in wp["scenes"] if s["kind"] == "new" and not frames.empty(wp, s)]
        ok(hero and all("watercolour painting" in fr[s["index"]]["data"]["prompt"] and "Photorealistic" not in fr[s["index"]]["data"]["prompt"] for s in hero),
           "the hero's frames: the medium said strongly")
        ok(all("Watercolour animation." in pr[i]["data"]["text"] for i in new_i), "every scene's prompt: the motion and the technique's phrase")
        graph.update(pid, lambda gg: graph.node(gg, ids["clip_writer"])["data"].update(plan=keep_plan))

        print("an older «Сценарист клипа» expands as it did")
        def old_writer(gg):
            d = graph.node(gg, ids["clip_writer"])["data"]
            d.pop("v", None)
        graph.update(pid, old_writer)
        g = graph.expand(pid, ids["clip_writer"], "replace")
        mine = [n for n in g["nodes"] if n["data"].get("from") == ids["clip_writer"]]
        ok(not any(n["data"].get("place") for n in mine), "no place pictures (its plan has places, but no `v`)")
        ok(not any((n["data"].get("cut") or {}).get("pre") or (n["data"].get("cut") or {}).get("face") for n in mine if n["type"] == "scene"),
           "no pre-rolls, no face marks: its scenes' keys as before")
        ok(not any(n["type"] == "picture" and isinstance(n["data"].get("scene"), int) and frames.empty(gp, gp["scenes"][n["data"]["scene"]]) for n in mine),
           "its inserts from text, as before")
    finally:
        pt.undo()
        for p in pids:
            projects.remove(p)
        shutil.rmtree(song.SONGS / SID, ignore_errors=True)


def review() -> None:
    """The GPU test of 2026-11-03: a stern 32-year-old for «девушка», an insert letterboxed, the sound 0.25 s short, a
    sign with garbled letters - each fixed for a plan written since (writer.RULES_V), an older plan word for word."""
    import subprocess
    import tempfile
    from app import config, frames
    print("the review of the GPU test")
    new = writer.new_clip_plan("девушка едет в ночном трамвае", "one", "", {"song": SID, "name": "x", "from": 0, "to": 8, "bpm": BPM, "bar_s": 4 * BEAT},
                               [{"start": 0, "seconds": 4.0}, {"start": 4.0, "seconds": 4.0}], 1, None)
    old = {k: v for k, v in new.items() if k != "rules_v"}
    ok(writer._rules_people(new) in writer._mode_rules(new) and writer._rules_people(new) and writer.PEOPLE_FROM_IDEA not in writer._mode_rules(old) and writer._system(new) == writer._system(old) + writer.NO_TOOLS_RULE,
       "1. a new plan: the people's age and look follow the idea's words; an older plan's brief as it was")
    ok(not any(w in writer.PEOPLE_FROM_IDEA.lower() for w in ("feminine", "beautiful", "attractive")) and not any(ch.isdigit() for ch in writer.PEOPLE_FROM_IDEA),
       "1. no fixed age or look in that rule")
    ok(writer.PEOPLE_FROM_IDEA not in writer._mode_rules({**new, "mode": "none"}), "1. a video without people: no such rule")
    looks.choose_request(new, "clip", "night_neon", "")
    looks.choose_request(old, "clip", "night_neon", "")
    rest = "Cinematic grain, deep shadows, anamorphic lens flares emphasizing the slick, urban atmosphere."
    a, b = looks.style_for(new, rest), looks.style_for(old, rest)
    ok("anamorphic" not in a.lower() and "widescreen" not in a.lower() and "lens flares" in a and "anamorphic" in b,
       "2. a new plan's style without «anamorphic» / «widescreen» (lens flares stay); an older plan's as it was")
    s = {"index": 0, "kind": "new", "beats_en": ["The tram passes a dense cluster of saturated magenta signage."], "framing": "wide",
         "camera": "pan", "place_en": "An empty station at night.", "who": [], "shot": "insert", "id": "a"}
    for p in (new, old):
        p.update(style_en="night", cast=[{"gender": "female", "age": 22, "en": "a young woman", "outfit_en": "a coat"}])
    ok("signage" not in model_prompts.for_model("ltx25", new, s) and "magenta lights" in model_prompts.for_model("ltx25", new, s)
       and "signage" in model_prompts.for_model("ltx25", old, s), "4. a new plan's signs are lights in LTX's prompt; an older plan's prompt as it was")
    ok(looks.no_letterbox("utilizing an anamorphic lens feel") == "utilizing a lens feel", "2. «an anamorphic lens» reads «a lens»")
    ok(model_prompts.no_signs("She signs the letter.") == "She signs the letter.", "4. the verb stays")
    ok("signage" not in frames.prompt_of(new, {**s, "who": [1]}, ["The heroine"]), "4. ... and in the frame's prompt")
    # 3. a shot whose sound is shorter than its picture: the joined sound is as long as the picture
    d = Path(tempfile.mkdtemp())
    for k in range(3):
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x96:r=24:d=2", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1.85", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(d / f"c{k}.mp4")], capture_output=True)
    items = [{"id": f"s{k}", "index": k, "name": f"{k}", "chain": k, "kind": "new", "from": None, "chosen": 1,
              "shot": {"key": f"c{k}", "file": f"c{k}.mp4", "png": None, "src": str(d / f"c{k}.mp4")}, "takes": []} for k in range(3)]
    ed = edit.lay(items, {}, d)
    edit.render(ed, d, d / "out.mp4")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", str(d / "out.mp4")],
                       capture_output=True, text=True).stdout.split()
    dur = {x.split(",")[0]: float(x.split(",")[1]) for x in r}
    ok(abs(dur.get("audio", 0) - dur.get("video", 0)) < 0.05, f"3. the joined sound as long as the picture ({dur})")
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    run()
    catchup()
    review()
    print("FAILED: %d" % failed if failed else "all passed")
    sys.exit(1 if failed else 0)
