"""The home's «Что снимаем?» and «Сделать ролик» (app/autopilot.py) without a GPU or an LLM: what the box makes of
what it was given («Поняли так», «Не так? Изменить»), the story's request and stages, the stops of «Спрашивать меня»
(the hero picked, more tries, nobody answering, the trial scene and «Сменить вид»), asking again after a restart, and
a whole story run with stand-ins for the writer and the GPU stages (then «Продолжить» after a failed stage).

    .venv\\Scripts\\python.exe tools\\test_home.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.main as main  # noqa: E402,F401 - the routes, as the server has them
from app import autopilot as ap, clip_api, config, jobs, looks, picture, projects, render, story  # noqa: E402
from app.engine.comfy import EngineError  # noqa: E402
from app.llm import client as llm, writer  # noqa: E402
from app.planner import check  # noqa: E402

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


def understand() -> None:
    print("«Поняли так»")
    u = ap.understand
    ok(u(True, False, "") == "clip", "a song -> a clip")
    ok(u(True, True, "") == "clip_hero", "a song + a photo -> a clip with this hero")
    ok(u(True, True, "лето, море") == "clip_hero", "... the text is the clip's idea")
    ok(u(False, True, "") == "story_hero", "a photo alone -> the hero of a story")
    ok(u(False, True, "две подруги едут к морю") == "story_hero", "a photo + text -> a story with this hero")
    ok(u(False, False, "Лето, море, подруги едут к закату") == "story", "text -> a story")
    ok(u(False, False, "  ") == "" and u(False, False, "ab") == "", "nothing (or two letters) -> nothing yet")
    m = ap.misfit
    ok(m("clip", False, False, "текст") is not None, "«Клип» without a song: said why")
    ok(m("clip_hero", True, False, "") is not None, "«Клип с героем» without a photo: said why")
    ok(m("story", True, True, "") is not None, "«История» without text: said why")
    ok(m("story_start", False, True, "") is None and m("story_hero", False, True, "") is None, "a photo alone: a story from it")
    ok(m("story", True, False, "песня о лете") is None, "a song given, the user wants a story: allowed")
    ok(m("nope", True, True, "x") is not None, "an unknown kind: said why")
    for song in (False, True):   # what understand() says always fits what it was given
        for photo in (False, True):
            for text in ("", "Лето у моря"):
                w = ap.understand(song, photo, text)
                ok(not w or ap.misfit(w, song, photo, text) is None, f"understood {w or '—'} fits (song {song}, photo {photo}, text {bool(text)})")


def request() -> None:
    print("the story's request and stages")
    r = ap.Autopilot(idea="Лето, море, подруги едут к закату", video_model="h3", seconds=45, mode="several", delivery="cinema", look="noir")
    ok(ap.what_of(r) == "story" and not ap.is_clip(r), "text: a story")
    n = ap._story_req(r)
    ok((n.idea, n.seconds, n.mode, n.delivery, n.look, n.photo, n.photo_start, n.video_model) ==
       ("Лето, море, подруги едут к закату", 45, "several", "cinema", "noir", None, False, "h3"), "the idea screen's form")
    ok(ap.stages_of(r) == ("write", "hero", "places", "frames", "render"), f"a story's stages: {ap.stages_of(r)}")
    r.ask = True
    ok(ap.stages_of(r) == ("write", "hero", "places", "frames", "trial", "render"), "«Спрашивать меня»: the trial scene before the shooting")
    c = ap.Autopilot(song="abc", photo="p.png")
    ok(ap.what_of(c) == "clip_hero" and ap.stages_of(c)[0] == "words", "a song + a photo: a clip, the words first")
    old = ap.Autopilot(**{"song": "abc", "idea": "x", "aspect": "9:16", "length": "best"})   # saved before «what»
    ok(ap.what_of(old) == "clip" and ap.stages_of(old) == ("words", "write", "hero", "places", "frames", "render"), "an older clip's request: as before")
    p = ap.Autopilot(photo="p.png", what="story_start")
    s = ap._story_req(p)
    ok(s.photo == "p.png" and s.photo_start and len(s.idea) > 10, "a photo alone, «первый кадр»: the photo starts it, an idea about the person")
    q = ap.Autopilot(photo="p.png", idea="история", what="story")
    ok(ap._story_req(q).photo is None, "«История» forced: the photo not used")
    # trial_scene: the first singing new scene, else the first with the hero, else the first
    plan = {"mode": "one", "cast": [{"en": "x"}], "scenes": [
        {"index": 0, "kind": "new", "shot": "insert", "prompt": "a"}, {"index": 1, "kind": "continue", "shot": "sing", "prompt": "b"},
        {"index": 2, "kind": "new", "shot": "sing", "prompt": "c"}]}
    pt = Patch()
    pt(ap.prompts, "is_written", lambda s: bool(s.get("prompt")))
    pt(ap.prompts, "no_cast", lambda plan, s: s.get("shot") == "insert")
    try:
        ok(ap.trial_scene(plan) == 2, "the trial: the first new singing scene (not a continuation)")
        plan["scenes"][2]["shot"] = "perform"
        ok(ap.trial_scene(plan) == 2, "... no singing: the first with the hero")
        plan["scenes"][2]["shot"] = "insert"
        ok(ap.trial_scene(plan) == 0, "... nobody: the first new scene")
        plan["scenes"] = [{"index": 0, "kind": "new"}]
        ok(ap.trial_scene(plan) is None, "... nothing written: no trial")
    finally:
        pt.undo()


def _project(cast: int = 1) -> str:
    proj = projects.create({"idea": "x", "seconds": 10, "mode": "one", "aspect": "16:9", "video_model": "h3", "quality": "draft"})
    plan = writer.new_plan("ночной город", 10, "one", "", "", None, seed=1)
    looks.choose_request(plan, "cinema", "live_cinema", "")
    plan.update(style_en="live-action cinematic film, natural light.", look_style=looks.LOOKS["live_cinema"]["style_en"],
                cast=[{"gender": "female", "age": 25, "en": f"a 25-year-old woman number {i}", "outfit_en": "a coat", "ru": "", "outfit_ru": ""} for i in range(cast)])
    for s in plan["scenes"]:
        s.update(ru="Она идёт.", beats_en=["She walks toward the camera slowly through the rain."], place_en="A night street.")
    proj["plan"] = check.plan(plan)
    projects.save(proj)
    return proj["id"]


def _job(pid: str) -> jobs.Job:
    job = jobs.Job(id=f"t{pid[-6:]}", params={"project": pid}, kind="autopilot", status="running", steps=0)
    job.started = __import__("time").time()
    jobs.JOBS[job.id] = job
    return job


async def _answer(pid: str, body: dict, after: float = 0.05) -> int:
    """The page's answer, once the stop is up."""
    for _ in range(200):
        if ap.waiting(pid):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(after)
    try:
        ap.answer(pid, ap.Answer(**body))
        return 200
    except ap.HTTPException as e:
        return e.status_code


def people() -> None:
    print("who is in the frame, from the idea")
    p = story.people_of
    ok(p("парень и девушка встречаются под дождём в ночном городе") == "several", "a guy and a girl -> several")
    ok(p("две подруги едут к морю") == "several" and p("friends dance on a roof") == "several", "a group word -> several")
    ok(p("девушка едет в ночном трамвае") == "one" and p("a man walks along the shore in another town") == "one", "one person -> one")
    ok(p("человек идёт по другой улице") == "one", "«другой» is not a friend")
    ok(p("пустой пляж на рассвете, без людей") == "none" and p("a quiet landscape of mountains") == "none", "no people -> none")
    ok(p("парень и девушка") == "several" and p("парень и девушка", photo=True) == "one", "a photo is the hero: one")
    ok(p("a girl sings. Shot plan: a guy and a boy") == "one", "the autopilot's plan hint is not the idea")
    req = ap.Autopilot(idea="парень и девушка встречаются под дождём")
    ok(req.mode == "auto" and ap._story_req(req).mode == "several", "the home: auto -> several")
    ok(ap._story_req(ap.Autopilot(idea="парень и девушка", mode="one")).mode == "one", "the user's choice stays")
    nr = story.NewProject(idea="парень и девушка идут под дождём", mode="auto")
    story.check_form(nr)
    ok(nr.mode == "several", "POST /api/projects: auto resolved by check_form")


def hero_stop() -> None:
    print("«Выберите героя»")
    pt = Patch()
    drawn = {"n": 0}

    async def tries(job, pid, i, photo, every):
        cur = projects.load(pid)
        new = []
        for k in range(ap.TRIES if every else 1):
            drawn["n"] += 1
            f = f"cast_t{drawn['n']}.png"
            (projects.folder(pid) / f).write_bytes(b"png")
            t = {"file": f, "seed": drawn["n"], "faces": 1}
            cur["plan"]["cast"][i].setdefault("tries", []).append(t)
            new.append(t)
        projects.save(cur)
        return new
    pt(ap, "_tries", tries)
    pt(picture, "problem", lambda: None)
    pt(ap, "WAIT", 1.0)
    pid = _project(cast=2)
    job = _job(pid)
    try:
        req = ap.Autopilot(idea="история", what="story", ask=True)

        async def go():
            run = asyncio.ensure_future(ap._hero(job, req, pid))
            ok(await _answer(pid, {"go": True}) == 400, "the hero's stop: «Дальше» is not an answer (400)")
            ok(await _answer(pid, {"pick": "cast_nope.png"}) == 400, "... a try that is not among them: 400")
            ask = projects.load(pid)["autopilot"]["ask"]
            ok(ask["kind"] == "hero" and len(ask["tries"]) == 3 and ask["until"] > 0 and bool(ask.get("id")),
               "the stop kept in the project: kind, three tries, its deadline")
            ok(job.progress["ask"]["best"] == "cast_t1.png", "... the one it would take: the first with one face")
            ok(await _answer(pid, {"more": True}) == 200, "«Попросить других кандидатов»")
            for _ in range(100):
                if drawn["n"] == 6 and ap.waiting(pid):
                    break
                await asyncio.sleep(0.02)
            ok(drawn["n"] == 6 and len(job.progress["ask"]["tries"]) == 3 and job.progress["ask"]["tries"][0]["file"] == "cast_t4.png",
               "... three new tries drawn and asked about")
            ok(await _answer(pid, {"pick": "cast_t5.png"}) == 200, "a try picked")
            await run
        asyncio.run(go())
        cur = projects.load(pid)
        ok(cur["plan"]["cast"][0]["photo"] == "cast_t5.png", "the picked try is the hero's portrait")
        ok(cur["autopilot"].get("ask") is None and "ask" not in job.progress, "the stop is gone")
        ok(cur["plan"]["cast"][1].get("photo") == "cast_t7.png", "a story's second hero: a portrait without asking")
        ok(ap.waiting(pid) is None, "nothing waits")
    finally:
        jobs.JOBS.pop(job.id, None)
        projects.remove(pid)

    # nobody answers: the one it would have taken; a restart: asked again without drawing anew
    pid = _project()
    job = _job(pid)
    try:
        ap.WAIT = 0.3
        t0 = __import__("time").time()
        asyncio.run(ap._hero(job, ap.Autopilot(idea="история", what="story", ask=True), pid))
        cur = projects.load(pid)
        ok(cur["plan"]["cast"][0]["photo"] == "cast_t8.png" and __import__("time").time() - t0 >= 0.3, "nobody answered: the first try with one face, after the wait")
        # a stop left in the project by a restart (the job gone): «Продолжить» asks with the same tries
        cur["plan"]["cast"][0].pop("photo", None)
        cur["plan"]["cast"][0]["tries"] = [{"file": f, "seed": 1, "faces": 1} for f in ("cast_a.png", "cast_b.png")]
        cur["autopilot"]["ask"] = {"kind": "hero", "tries": [{"file": "cast_a.png"}, {"file": "cast_b.png"}], "until": 0}
        projects.save(cur)
        n = drawn["n"]

        async def again():
            run = asyncio.ensure_future(ap._hero(job, ap.Autopilot(idea="история", what="story", ask=True), pid))
            ok(await _answer(pid, {"pick": "cast_b.png"}) == 200, "after a restart: the same stop, answered")
            await run
        asyncio.run(again())
        ok(drawn["n"] == n and projects.load(pid)["plan"]["cast"][0]["photo"] == "cast_b.png", "... no new tries drawn, the pick taken")
        # «Решать самому»: no stop
        cur = projects.load(pid)
        cur["plan"]["cast"][0].pop("photo", None)
        cur["autopilot"].pop("ask", None)
        projects.save(cur)
        asyncio.run(ap._hero(job, ap.Autopilot(idea="история", what="story", ask=False), pid))
        ok(drawn["n"] == n + 1 and projects.load(pid)["autopilot"].get("ask") is None, "«Решать самому»: one try, never a stop")
    finally:
        jobs.JOBS.pop(job.id, None)
        projects.remove(pid)
        pt.undo()


def trial_stop() -> None:
    print("«Пробная сцена»")
    pt = Patch()
    shot, redrawn = [], []

    async def take(job, pid, i, seed):
        shot.append((i, seed))
        return {"scene": i, "seed": seed, "key": f"k{len(shot)}", "cached": False, "seconds": 1}

    async def frames(job, pid, ahead=lambda n: None):
        redrawn.append(pid)
    pt(render, "shoot_take", take)
    pt(clip_api, "draw_frames", frames)
    pt(ap.COMFY, "problems", lambda: [])
    pt(ap, "WAIT", 2.0)
    pid = _project()
    job = _job(pid)
    try:
        async def go():
            run = asyncio.ensure_future(ap._trial(job, ap.Autopilot(idea="история", what="story", ask=True), pid))
            ok(await _answer(pid, {"pick": "x.png"}) == 400, "the trial's stop: a pick is not an answer (400)")
            ask = job.progress["ask"]
            ok(ask["kind"] == "trial" and ask["file"] == "scenes/k1.mp4", "the trial scene shot and shown")
            held = await _answer(pid, {"hold": True})
            await asyncio.sleep(ap.POLL * 2)
            ok(held == 200 and job.progress["ask"]["until"] - __import__("time").time() > 60,
               "«Сменить вид» opened: the wait is longer")
            ok(await _answer(pid, {"look": "anime"}) == 200, "a new look")
            for _ in range(100):
                if len(shot) == 2 and ap.waiting(pid):
                    break
                await asyncio.sleep(0.02)
            ok(redrawn == [pid] and len(shot) == 2, "... the frames drawn again, the trial shot again")
            ok(await _answer(pid, {"go": True}) == 200, "«Дальше»")
            await run
        asyncio.run(go())
        cur = projects.load(pid)
        ok(cur["plan"]["look"] == "anime" and cur["settings"]["look"] == "anime", "the look saved by the answer (project and plan)")
        ok(cur["settings"]["quality"] == "final" and cur["settings"]["output"]["size"] == "1080p", "the trial shot as the video will be: final, 1080p")
        s = cur["plan"]["scenes"][shot[0][0]]
        ok(shot[0][1] == render._seed(cur["plan"], s), "... with the scene's own seed (the shooting takes it from the cache)")
        ok(cur["autopilot"].get("ask") is None, "the stop is gone")
        # nobody answers: on to the shooting
        shot.clear()
        ap.WAIT = 0.2
        asyncio.run(ap._trial(job, ap.Autopilot(idea="история", what="story", ask=True), pid))
        ok(len(shot) == 1 and ap.waiting(pid) is None, "nobody answered: one trial, then on")
    finally:
        jobs.JOBS.pop(job.id, None)
        projects.remove(pid)
        pt.undo()
    ok(ap.answer.__name__ and ap.waiting("nope") is None, "no job: nothing waits")
    try:
        ap.answer("nope", ap.Answer(go=True))
        ok(False, "an answer with nothing asked: 409")
    except ap.HTTPException as e:
        ok(e.status_code == 409, "an answer with nothing asked: 409")


def story_run() -> None:
    print("a story from text, with stand-ins")
    pt = Patch()
    calls = []

    async def write(pid, plan, mode):
        calls.append("write")
        plan["title_ru"] = "Лето у моря"
        for s in plan["scenes"]:
            s.update(ru="Она идёт.", beats_en=["She walks toward the camera slowly through the rain."], place_en="A night street.")
        plan["cast"] = [{"gender": "female", "age": 25, "en": "a woman", "outfit_en": "a coat", "ru": "", "outfit_ru": ""}]
        cur = projects.load(pid)
        cur["plan"] = check.plan(plan)
        cur["name"] = plan["title_ru"]
        return projects.save(cur)

    def stage(nm):
        async def f(job, *a, **k):
            calls.append(nm)
            if nm == "frames" and fail.get("frames"):
                fail["frames"] = False
                raise EngineError("stand-in failure")
            return {"complete": True} if nm == "render" else None
        return f
    fail = {"frames": True}
    pt(llm, "find", lambda mid=None: MODEL)
    pt(story, "write", write)
    pt(ap, "_hero", stage("hero"))
    pt(clip_api, "group_places", stage("places"))
    pt(clip_api, "draw_places", stage("places_draw"))
    pt(clip_api, "draw_frames", stage("frames"))
    pt(ap, "_trial", stage("trial"))
    pt(ap, "_render", stage("render"))
    pt(picture, "edit_problem", lambda: None)
    pt(ap.COMFY, "problems", lambda: [])
    pt(ap.videomodels, "weights_missing", lambda c: [])
    pid = None
    try:
        req = ap.Autopilot(idea="Лето, море, подруги едут к закату", video_model="h3", seconds=10, ask=True, look="noir")
        ap._check(req)
        ok(req.what == "story" and req.song is None and req.model == MODEL.id, "checked: a story, the writer settled")
        job = jobs.Job(id="tstory", params={"project": None}, kind="autopilot", status="running", steps=0)
        job.started = __import__("time").time()
        try:
            asyncio.run(ap.run(job, req))
            ok(False, "a failed stage fails the job")
        except EngineError as e:
            ok("stand-in failure" in str(e), f"a failed stage fails the job, named: {e}")
        pid = job.params["project"]
        cur = projects.load(pid)
        ok(calls == ["write", "hero", "places", "places_draw", "frames"], f"the stages in order: {calls}")
        ok(cur["name"] == "Лето у моря" and cur["plan"]["scenes"] and cur["settings"]["video_model"] == "h3", "the project written as «Расписать сцены» writes it")
        a = cur["autopilot"]
        ok(a["done"] == ["write", "hero", "places"] and a["stage"] == "frames" and a["error"], "the project says where it failed")
        ok(ap.Autopilot(**a["req"]).what == "story", "... and what was asked («Продолжить» reads it)")
        stages = job.progress["auto"]["stages"]
        ok([x["id"] for x in stages] == ["write", "hero", "places", "frames", "trial", "render"], "the bar's stages")
        summ = projects.summary(cur)
        ok(summ["auto"]["stage"] == "frames" and summ["auto"]["what"] == "story" and summ["looks"]["look"] == "noir", "the card: the autopilot's state and «Подача · Вид»")
        # «Продолжить»: from the failed stage on
        calls.clear()
        job2 = jobs.Job(id="tstory2", params={"project": pid}, kind="autopilot", status="running", steps=0)
        job2.started = __import__("time").time()
        r = asyncio.run(ap.run(job2, ap.Autopilot(**a["req"]), pid))
        ok(calls == ["frames", "trial", "render"] and r == {"project": pid, "complete": True}, f"«Продолжить»: the rest ({calls})")
        ok(projects.load(pid)["autopilot"]["finished"], "finished")

        print("a story in «Акварель»: every scene from a picture")
        from app import frames
        from app.planner import model_prompts
        calls.clear()
        fail["frames"] = True
        req = ap.Autopilot(idea="Лето, море, подруги едут к закату", video_model="ltx25", seconds=15, ask=False, look="watercolor")
        ap._check(req)
        job3 = jobs.Job(id="tstory3", params={"project": None}, kind="autopilot", status="running", steps=0)
        job3.started = __import__("time").time()
        try:
            asyncio.run(ap.run(job3, req))
        except EngineError:
            pass
        projects.remove(pid)
        pid = job3.params["project"]
        cur = projects.load(pid)
        plan = cur["plan"]
        new = [s for s in plan["scenes"] if s["kind"] == "new"]
        ok(looks.needs_frame(plan) and new and all(frames.can_draw(plan, s) for s in new), f"needs_frame: every new scene gets a picture ({len(new)})")
        _, est = ap._estimates(req, cur)
        ok(est.get("frames") == round(frames.estimate(len(new)) * 1.4), f"the estimate counts every scene's picture ({est.get('frames')} s)")
        for s in new:   # the frames stage as frames.draw_one leaves them
            s.update(start_photo=f"frame_{s['id']}.png", start_en="x")
        ok(all(model_prompts.for_model("ltx25", plan, s).startswith(model_prompts.LTX_HOLD) and "Watercolour animation." in model_prompts.for_model("ltx25", plan, s)
               and "night street" not in model_prompts.for_model("ltx25", plan, s).lower() for s in plan["scenes"]),
           "every scene's prompt: held on its picture, the motion and the technique's phrase, no place")
    finally:
        if pid:
            projects.remove(pid)
        pt.undo()


def switches() -> None:
    print("«Ещё настройки»: what the autopilot runs")
    A = ap.Autopilot
    story_ = dict(idea="Лето, море, подруги едут к закату", video_model="h3")
    ALL = ("write", "hero", "places", "frames", "render")
    ok(ap.stages_of(A(**story_)) == ALL, "no flags (an old request): everything, as before")
    ok(ap.stages_of(A(**story_, hero=False)) == ("write", "places", "frames", "render"), "hero off: no hero stage")
    ok(ap.stages_of(A(**story_, places=False)) == ("write", "hero", "frames", "render"), "places off: frames from the words")
    ok(ap.stages_of(A(**story_, frames=False)) == ("write", "hero", "render"), "frames off: no places either")
    ok(ap.stages_of(A(**story_, frames=False, hero=False, ask=True)) == ("write", "trial", "render"), "frames and hero off, asking: the trial stays")
    ok(ap.stages_of(A(**story_, script="own", ask=True)) == ("write",), "a story, own script: the scenes only")
    ok(ap.stages_of(A(song="abc", script="own")) == ("words", "write"), "a clip, own script: words and the cut scenes")
    ok(ap.stages_of(A(song="abc", frames=False)) == ("words", "write", "hero", "render"), "a clip, frames off")
    # a look that draws every frame (the field may not exist in this build: put it on a look for the test)
    pt = Patch()
    pt(ap.looks, "LOOKS", {**looks.LOOKS, "noir": {**looks.LOOKS["noir"], "needs_frame": True}})
    pt(llm, "find", lambda mid=None: MODEL)
    pt(ap.videomodels, "weights_missing", lambda c: [])
    pt(ap.COMFY, "problems", lambda: [])
    try:
        r = A(**story_, look="noir", frames=False, places=False)
        ok(ap.stages_of(r) == ("write", "hero", "frames", "render"), "a needs_frame look: frames stay on")
        ap._check(r)
        ok(r.frames and not r.places, "... the server forces them on (and keeps places off)")
        r = A(**story_, look="noir", frames=False)
        ap._check(r)
        ok(r.frames and r.places, "... places as asked")
        r = A(**story_, frames=False)
        ap._check(r)
        ok(not r.frames and not r.places, "frames off: places off with them")
        try:
            ap._check(A(**story_, script="nope"))
            ok(False, "an unknown script choice: 400")
        except Exception as e:  # noqa: BLE001
            ok(getattr(e, "status_code", 0) == 400, "an unknown script choice: 400")
        # «пишу сам», a story: the empty board of the length, nothing drawn
        called = []

        async def never(*a, **k):
            called.append(1)
        for nm in ("_hero", "_trial", "_render"):
            pt(ap, nm, never)
        pt(clip_api, "draw_frames", never)
        pt(clip_api, "group_places", never)
        pt(story, "write", never)
        req = A(**story_, seconds=10, script="own", ask=True)
        ap._check(req)
        job = jobs.Job(id="town", params={"project": None}, kind="autopilot", status="running", steps=0)
        job.started = __import__("time").time()
        out = asyncio.run(ap.run(job, req))
        pid = job.params["project"]
        try:
            cur = projects.load(pid)
            ok(out == {"project": pid, "complete": True} and not called, "own script: the job ends at the board, nothing drawn or written")
            ok(cur["plan"].get("manual") and cur["plan"]["scenes"] and all(x.get("source") == "user" for x in cur["plan"]["scenes"]), "... empty scenes for the user's prompts")
            ok([x["id"] for x in job.progress["auto"]["stages"]] == ["write"], "... the bar has one stage")
            ok(cur["autopilot"]["finished"] and cur["autopilot"]["req"]["script"] == "own", "... the project remembers the request")
        finally:
            projects.remove(pid)
    finally:
        pt.undo()


def api() -> None:
    print("the API")
    sys.path.insert(0, str(ROOT / "tools"))
    from test_looks import AsgiClient
    cl = AsgiClient(main.app)
    r = cl.post("/api/autopilot", {"what": "clip", "idea": "просто текст"})
    d = str(r.json().get("detail")).lower()
    ok(r.status_code == 400 and ("песн" in d or "song" in d), f"a clip without a song: 400 ({d})")
    r = cl.post("/api/autopilot", {"idea": "ab"})
    ok(r.status_code == 400, "nothing to make: 400")
    r = cl.post("/api/projects/nope/autopilot/answer", {"go": True})
    ok(r.status_code == 409, "an answer to nothing: 409")
    r = cl.post("/api/projects/nope/autopilot", {})
    ok(r.status_code == 404, "«Продолжить» of an unknown project: 404")


def estimate() -> None:
    print("the route «Как снимаем»: its estimate before the start (POST /api/autopilot/estimate)")
    sys.path.insert(0, str(ROOT / "tools"))
    from test_looks import AsgiClient
    cl = AsgiClient(main.app)
    A = ap.Autopilot
    base = dict(idea="Девушка на велосипеде едет вдоль моря на закате", video_model="h3", seconds=30, aspect="16:9")
    r = cl.post("/api/autopilot/estimate", base)
    ok(r.status_code == 200, f"a story: 200 ({r.status_code})")
    d = r.json()
    ok(d["what"] == "story" and d["run"] == ["write", "hero", "places", "frames", "render"], "... the stages of the request")
    ok(list(d["steps"]) == ["write", "hero", "places", "frames", "shoot", "montage"], f"... by the home's steps ({list(d['steps'])})")
    ok(d["total"] == sum(d["stages"].values()) == sum(d["steps"].values()), "... the steps add up to the total")
    ok(abs(d["total"] - ap._estimates(A(**base), None)[0]) <= 1, "... the same figure the job starts with")
    grid_s = ap.videomodels.card("h3").grid_seconds
    ok(d["scenes"] == max(1, round(30 / grid_s)) and d["model"] and set(d["models"]) >= {"h3"}, "... the scenes, the model and every model's scene time")
    ok("words" not in d["steps"] and not d["words_done"], "... a story has no song's words")
    # the route's switches: the request flags reach the same stages as the job's (stages_of)
    for flags in ({"hero": False}, {"places": False}, {"frames": False}, {"frames": False, "hero": False, "ask": True},
                  {"script": "own"}, {"script": "own", "ask": True}, {"ask": True}):
        body = {**base, **flags}
        d = cl.post("/api/autopilot/estimate", body).json()
        ok(d["run"] == list(ap.stages_of(A(**body))), f"flags {flags}: the stages {d['run']}")
    d = cl.post("/api/autopilot/estimate", {**base, "ask": True}).json()
    ok(d["stages"].get("trial") and d["steps"]["shoot"] >= d["stages"]["trial"], "asking: the trial scene is part of «Съёмка»")
    d = cl.post("/api/autopilot/estimate", {**base, "script": "own"}).json()
    ok(list(d["steps"]) == ["write"], "«пишу сам»: the route ends at the script")
    d = cl.post("/api/autopilot/estimate", {**base, "frames": False}).json()
    ok("frames" not in d["steps"] and "places" not in d["steps"], "frames off: no places either")
    pt = Patch()
    pt(ap.looks, "LOOKS", {**looks.LOOKS, "noir": {**looks.LOOKS["noir"], "needs_frame": True}})
    try:
        d = cl.post("/api/autopilot/estimate", {**base, "look": "noir", "frames": False}).json()
        ok("frames" in d["steps"], "a look that draws every frame: «Кадры» stay")
    finally:
        pt.undo()
    d60 = cl.post("/api/autopilot/estimate", {**base, "seconds": 60}).json()
    ok(d60["scenes"] > cl.post("/api/autopilot/estimate", base).json()["scenes"], "a longer story: more scenes")
    # what can't be estimated
    ok(cl.post("/api/autopilot/estimate", {"idea": "ab"}).status_code == 400, "nothing given: 400")
    ok(cl.post("/api/autopilot/estimate", {"what": "clip", "idea": "просто текст"}).status_code == 400, "a clip without a song: 400")
    ok(cl.post("/api/autopilot/estimate", {**base, "video_model": "nope"}).status_code == 400, "an unknown model: 400")
    ok(cl.post("/api/autopilot/estimate", {"song": "nosuchsong1", "what": "clip"}).status_code == 404, "an unknown song: 404")
    # a song in this copy's library (tools may run on a clean checkout: then skipped)
    sid = next((x.name for x in (ROOT / "data" / "songs").glob("*") if (x / "analysis.json").exists()), None) if (ROOT / "data" / "songs").exists() else None
    if sid:
        d = cl.post("/api/autopilot/estimate", {"song": sid, "length": "own", "start": 10, "seconds": 30, "video_model": "ltx23"}).json()
        ok(d["what"] == "clip" and d["run"][0] == "words" and d["seconds"] == 30 and d["scenes"] == round(30 / 4), "a clip's own piece: words first, a scene every ~4 s")
        ok("montage" in d["steps"] and d["words_done"] == bool(ap.song.recognised(sid)), "... the words done when the song is recognised")
    else:
        print("  --  no song in data/songs: the clip's estimate not checked")


def review() -> None:
    print("the review's cases")
    import tempfile
    from app import story
    tmp = Path(tempfile.mkdtemp())
    pt = Patch()
    pt(looks, "MY_FILE", tmp / "my_looks.json")
    pid = _project()
    try:
        # 1. a board save sends its whole settings: the same look is no change; a deleted «Мой вид» keeps its snapshot
        m = looks.add_mine("Закат", "golden_hour", "")
        cur = projects.load(pid)
        ok(story.change_look(cur, {"look": m["id"]}), "a saved look chosen")
        projects.save(cur)
        cur = projects.load(pid)
        cur["plan"]["style_en"] = "my own hand-edited style, " + " ".join(["word"] * 50)
        projects.save(cur)
        looks.remove_mine(m["id"])
        cur = projects.load(pid)
        before = (cur["plan"]["style_en"], [c["en"] for c in cur["plan"]["cast"]])
        ok(not story.change_look(cur, {**cur["settings"], "quality": "final"}), "1. the whole settings again: no look change")
        ok((cur["plan"]["style_en"], [c["en"] for c in cur["plan"]["cast"]]) == before, "1. ... the hand-edited style and the heroes untouched")
        p2 = looks.choose(dict(cur["plan"]), look=m["id"])
        ok(p2["look"] == m["id"] and p2["look_mine"]["base"] == "golden_hour", "1. a deleted «Мой вид»: the plan keeps it by its snapshot")
        ok(looks.resolve(p2)[1] == "golden_hour", "1. ... and it still resolves to its base")
        # 3. a delivery change sending the look as it stands: only the delivery changes, nothing restyled
        now = story.look_now(cur)
        ok(story.change_look(cur, {**now, "delivery": "trailer"}) and cur["plan"]["delivery"] == "trailer"
           and cur["plan"]["style_en"] == before[0], "3. the delivery alone (with the look as it stands): no restyle")
        # 3b. a looks_v 1 plan: changing only the delivery must not move it to looks_v 2 (frames, prompts, cache keys)
        v1 = projects.load(pid)
        v1["plan"]["looks_v"] = 1
        now1 = story.look_now(v1)
        ok(story.change_look(v1, {**now1, "delivery": "documentary" if now1["delivery"] != "documentary" else "trailer"})
           and v1["plan"]["looks_v"] == 1, "3b. the delivery alone on a looks_v 1 plan: it stays looks_v 1")
        # 2. an older plan, written before the looks: a delivery alone keeps its style and its heroes
        old = projects.load(pid)
        for k in looks.KEYS + ("look_style",):
            old["plan"].pop(k, None)
        old["plan"]["style_en"] = "anime style, cel shading, soft pastel palette."
        old["plan"]["cast"][0]["en"] = "a young woman with large expressive eyes and long lashes"
        old["plan"].pop("clip", None)
        for k in ("delivery", "look", "look_own"):
            old["settings"].pop(k, None)
        ok(story.change_look(old, {"delivery": "trailer"}), "2. an older plan: a new delivery taken")
        ok(old["plan"]["style_en"] == "anime style, cel shading, soft pastel palette." and "large expressive eyes" in old["plan"]["cast"][0]["en"],
           "2. ... its style and its hero's look untouched (no live-action prefix, no cleaning)")
        ok(not looks.active(old["plan"]) and old["plan"]["delivery"] == "trailer", "2. ... and it stays an older plan")
        # 4. the card of an older clip: no «Клип · Живое кино» guessed
        old["plan"]["clip"] = {"name": "x"}
        ok(projects.summary(old)["looks"] is None, "4. an older clip's card: no look badge")
        cur = projects.load(pid)
        cur["plan"]["looks_v"] = looks.LOOKS_V
        ok(projects.summary(cur)["looks"] is not None, "4. a plan written by the looks: its badge")
    finally:
        projects.remove(pid)
        pt.undo()

    # 5. «Сменить вид» never holds past HOLD from the stop's start; 7. «Готово» on the same look is «Дальше»
    pt(ap, "WAIT", 0.3)
    pt(ap, "HOLD", 1.0)
    pid = _project()
    job = _job(pid)
    try:
        async def holds():
            run = asyncio.ensure_future(ap._ask(job, pid, {"kind": "trial", "scene": 0, "file": "scenes/x.mp4"}))
            t0 = __import__("time").time()
            for _ in range(4):   # re-armed again and again
                await _answer(pid, {"hold": True}, after=0.1)
            got = await run
            return got, __import__("time").time() - t0
        got, took = asyncio.run(holds())
        ok(got is None and took < 1.6, f"5. the hold re-armed four times still ends at HOLD ({took:.1f} s)")

        async def same_look():
            run = asyncio.ensure_future(ap._ask(job, pid, {"kind": "trial", "scene": 0, "file": "scenes/x.mp4"}))
            now = story.look_now(projects.load(pid))
            ok(await _answer(pid, dict(now)) == 200, "7. «Готово» with the look as it stands")
            return await run
        ok(asyncio.run(same_look()) == {"go": True}, "7. ... is «Дальше»: nothing rebuilt")
        # 6. a stop cut short by «Остановить»: the stop is cleared
        async def cancelled():
            run = asyncio.ensure_future(ap._ask(job, pid, {"kind": "hero", "tries": [], "best": None}))
            for _ in range(100):
                if ap.waiting(pid):
                    break
                await asyncio.sleep(0.01)
            run.cancel()
            try:
                await run
            except asyncio.CancelledError:
                pass
        ap.WAIT = 5.0
        asyncio.run(cancelled())
        ok(projects.load(pid)["autopilot"].get("ask") is None and "ask" not in job.progress, "6. stopped at a stop: the stop cleared")
    finally:
        jobs.JOBS.pop(job.id, None)
        projects.remove(pid)
        pt.undo()

    # 8. an older client's own mood reaches the writer's brief; a known one is a delivery, not repeated
    pl = story.plan_for(story.NewProject(idea="ночной город", mood="грустный джаз под дождём"), MODEL, None)
    ok("Mood: грустный джаз под дождём." in looks.brief(pl), "8. a free mood in the writer's brief")
    pl = story.plan_for(story.NewProject(idea="ночной город", mood="Кино"), MODEL, None)
    ok(pl["delivery"] == "cinema" and "Mood:" not in looks.brief(pl), "8. a known mood: the delivery, no «Mood:»")

    # 9. a dropped «video» that is a playlist: refused before ffmpeg opens anything
    d = Path(tempfile.mkdtemp())
    (d / "v.mp4").write_text("\n".join(["#EXTM3U", "#EXT-X-TARGETDURATION:1", "#EXTINF:1,", "file:///C:/Windows/win.ini", "#EXT-X-ENDLIST", ""]))
    ok(not main.video_frame(d / "v.mp4", d / "out.png") and not (d / "out.png").exists(), "9. an HLS playlist named .mp4: no frame, nothing opened")


if __name__ == "__main__":
    understand()
    people()
    request()
    hero_stop()
    trial_stop()
    story_run()
    switches()
    api()
    estimate()
    review()
    print("FAILED: %d" % failed if failed else "all passed")
    sys.exit(1 if failed else 0)
