"""«Подача» and «Вид» (app/looks.py) without a GPU or an LLM: the lists, the hero's rules, the migration of older
moods, «Решит сценарист» with a stand-in writer, the writer's brief and rules, the portrait, the clip's cuts by a
delivery, the graph's look per scene, and the API through FastAPI's TestClient («Мои виды» in a temporary file).

    .venv\\Scripts\\python.exe tools\\test_looks.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import hero, i18n, looks, projects  # noqa: E402
from app.llm import writer  # noqa: E402
from app.planner import check, clip  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


def lists() -> None:
    print("lists")
    ok(len(looks.DELIVERIES) == 19 and next(iter(looks.DELIVERIES)) == "auto", "19 deliveries, «Решит сценарист» first")
    ok(len(looks.LOOKS) == 37 and next(iter(looks.LOOKS)) == "auto", "36 looks + auto")
    for g, _, _ in looks.GROUPS:
        ok(sum(1 for x in looks.LOOKS.values() if x["group"] == g) == 6, f"group {g}: 6 looks")
    for d in looks.DELIVERIES.values():
        ok(all(d.get(k) for k in ("ru", "en", "hint_ru", "hint_en")), f"delivery {d['id']}: both languages")
        ok(d["id"] == "auto" or (d["brief"] and d["scene"][0] < d["scene"][1] and 0 <= d["inserts"] <= 1 and d["cameras"]),
           f"delivery {d['id']}: brief, scene range, inserts, cameras")
        ok(not re.search(r"[а-яё]", d["brief"], re.I), f"delivery {d['id']}: brief in English")
    from app.planner import prompts
    brands = re.compile(r"pixar|disney|ghibli|marvel|dc comics|wes anderson|tarantino|kodak|fuji|polaroid|instagram|nintendo|"
                        r"miyazaki|van gogh|monet|banksy|blade runner|netflix|tiktok|youtube", re.I)
    for x in looks.LOOKS.values():
        ok(all(x.get(k) for k in ("ru", "en", "hint_ru", "hint_en")), f"look {x['id']}: both languages")
        ok(x["face_tested"] is None and x["portrait"] and x["medium"], f"look {x['id']}: portrait, medium, face_tested None")
        ok(not brands.search(x["style_en"] + " " + x["en"]), f"look {x['id']}: no brands or artists")
        ok(x["sing_camera"] in (None, *prompts.CAMERAS), f"look {x['id']}: singing camera known")
        ok(x["id"] == "auto" or len(x["style_en"].split()) <= 16, f"look {x['id']}: fragment of at most 16 words")
    for d in looks.DELIVERIES.values():
        ok(all(c in prompts.CAMERAS for c in d["cameras"]), f"delivery {d['id']}: cameras known")
    # a drawn or animated look reads as stylized (no handheld camera for its singers)
    for lid in ("anime", "cartoon3d", "clay", "watercolor", "comic", "pixel"):
        ok(prompts.stylized({"style_en": looks.LOOKS[lid]["style_en"]}), f"{lid}: prompts.stylized")
    ok(not prompts.stylized({"style_en": looks.LOOKS["live_cinema"]["style_en"]}), "live_cinema: not stylized")


def clean() -> None:
    print("clean_look")
    live, anime = looks.LOOKS["live_cinema"], looks.LOOKS["anime"]
    doll = "a 24-year-old woman with slender build, porcelain skin, large expressive eyes with long lashes, long wavy auburn hair"
    got = looks.clean_look(doll, live)
    ok(not looks.has_banned(got), f"live: banned words gone ({got[:70]}...)")
    ok("long wavy auburn hair" in got and "slender build" in got, "live: the rest of the look stays")
    ok(got.endswith(looks.ANCHORS), "live: anchors appended")
    ok(looks.clean_look(got, live) == got, "live: idempotent (anchors once)")
    ok(looks.clean_look(doll, anime) == doll, "anime: the look stays as written")
    for t in ("a woman with a doll-like face, flawless skin and big blue eyes, freckles on the nose",
              "a man with thick long eyelashes, a mole on the left cheek", "woman with Porcelain Skin and huge green eyes"):
        ok(not looks.has_banned(looks.clean_look(t, live)), f"live: «{t[:40]}...» cleaned")
    ok(", ," not in looks.clean_look("woman with porcelain skin, flawless complexion, freckles", live), "no empty commas left")


def migrate() -> None:
    print("migration of «Настроение»")
    for mood, want in (("cinematic live-action film", ("cinema", "auto")), ("Кино", ("cinema", "auto")),
                       ("music video: rhythmic, stylish, expressive motion", ("clip", "auto")), ("Реклама", ("ad", "auto")),
                       ("documentary: natural, observational, handheld feel", ("docu", "auto")),
                       ("epic fantasy with visual effects, photoreal", ("cinema", "fantasy_epic")),
                       ("Фэнтези и эффекты", ("cinema", "fantasy_epic"))):
        p = {"mood": mood}
        looks.migrate(p)
        ok((p["delivery"], p["look"]) == want, f"{mood!r} -> {want}")
        ok(not looks.active(p), f"{mood!r}: an older plan keeps its brief (not active)")
    p = {"mood": "", "clip": {"song": "x"}}
    looks.migrate(p)
    ok(p["delivery"] == "clip", "a clip without a mood -> «Клип»")
    p = {"mood": "", "delivery": "ad"}
    ok(not looks.migrate(p), "a plan with a delivery is not migrated")
    ok(looks.resolve({"mood": "Фэнтези и эффекты"}) == ("cinema", "fantasy_epic"), "resolve reads an older plan")
    st = looks.state({"mood": "Клип"})
    ok(st["delivery"] == "clip" and st["effective"]["look"] == "live_cinema", "state: delivery and the look in effect")


class FakeLLM:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.asked = answer, fail, []

    async def chat_json(self, system, user, schema, **kw):
        self.asked.append((system, user, schema))
        if self.fail:
            raise RuntimeError("no server")
        return self.answer


def pick() -> None:
    print("«Решит сценарист»")
    p = looks.choose({"idea": "ночной город", "clip": {"name": "Song", "bpm": 120, "map": "0:00-0:20 verse: words"}})
    ok(looks.needs_pick(p), "auto + auto: a pick is needed")
    llm = FakeLLM({"delivery": "concert", "look": "synthwave", "why_ru": "ночь и плёнка", "why_en": "night and tape"})
    asyncio.run(looks.decide(llm, p))
    ok(p["look_pick"]["delivery"] == "concert" and p["look_pick"]["look"] == "synthwave", "the writer's pick kept")
    ok(looks.resolve(p) == ("concert", "synthwave"), "resolve follows the pick")
    ok("synthwave" in llm.asked[0][2]["properties"]["look"]["enum"], "the answer is limited to the list's ids")
    ok(not set(llm.asked[0][2]["properties"]["look"]["enum"]) & looks.HIDDEN, "the writer is not offered hidden looks")
    q = looks.choose({"idea": "x", "video": "ltx25"}); llm2 = FakeLLM({"delivery": "cinema", "look": "noir", "why_ru": "-", "why_en": "-"}); asyncio.run(looks.decide(llm2, q))
    ok("papercut" in llm.asked[0][2]["properties"]["look"]["enum"] and "papercut" not in llm2.asked[0][2]["properties"]["look"]["enum"], "the writer: paper cut-out for H3, not for LTX")
    ok("comic" in llm2.asked[0][2]["properties"]["look"]["enum"] and "watercolor" in llm2.asked[0][2]["properties"]["look"]["enum"], "... comic and watercolour (drawn by every frame) for LTX too")
    ok(not looks.needs_pick(p), "picked once: no second call")
    p = looks.choose({"idea": "x", "clip": {"name": "S"}})
    asyncio.run(looks.decide(FakeLLM(fail=True), p))
    ok(p["look_pick"].get("fallback") and looks.resolve(p) == ("clip", "live_cinema"), "LLM fails: clip -> clip + live_cinema")
    p = looks.choose({"idea": "x"})
    asyncio.run(looks.decide(FakeLLM({"delivery": "nonsense", "look": "nonsense", "why_ru": "", "why_en": ""}), p))
    ok(looks.resolve(p) == ("cinema", "live_cinema"), "an idea, a wrong answer: cinema + live_cinema")
    p = looks.choose({"idea": "x"}, "horror", "auto")
    asyncio.run(looks.decide(FakeLLM({"delivery": "comedy", "look": "noir", "why_ru": "", "why_en": ""}), p))
    ok(looks.resolve(p) == ("horror", "noir"), "the user's delivery stays, only the look is the writer's")
    p = looks.choose({"idea": "x"}, "ad", "glossy")
    ok(not looks.needs_pick(p), "both chosen: no pick")
    ok(looks.pick_fallback({})["delivery"] == "cinema", "fallback without a song: cinema")


def rules() -> None:
    print("the rules in the writer, the style, the portrait")
    old = {"mode": "one", "idea": "x", "mood": "cinematic live-action film", "scenes": [{}], "seconds": 5}
    ok("Mood: cinematic live-action film" in writer._brief(old), "an older plan: the «Mood:» brief word for word")
    ok(writer._mode_rules(old) == writer.MODE_RULES["one"], "an older plan: the mode rules word for word")
    new = looks.choose({"mode": "one", "idea": "x", "mood": "", "scenes": [{}], "seconds": 5}, "cinema", "live_cinema")
    b = writer._brief(new)
    ok("Delivery: cinematic live-action" in b and "Mood:" not in b, "a new plan: the delivery's brief")
    ok("live-action cinematic film, natural light" in b, "a new plan: the look's fragment asked for")
    ok("long lashes)" not in writer._mode_rules(new) and "real person" in writer._mode_rules(new), "a live look: no lashes, a real person")
    an = looks.choose({"mode": "one", "idea": "x", "scenes": [{}], "seconds": 5}, "clip", "anime")
    ok(writer._mode_rules(an) == writer.MODE_RULES["one"], "anime: the usual look rule")
    ok(not writer._hand_ok({**an, "clip": {}}), "anime clip: no handheld camera for singers")
    # style_en starts from the look; a change of look swaps the fragment and keeps the rest
    new["style_en"] = "Soft rim light, teal and amber palette, 35mm lens."
    new["cast"] = [{"gender": "female", "age": 25, "en": "a 25-year-old woman with porcelain skin and large expressive eyes, auburn hair", "outfit_en": "a red coat"}]
    looks.apply(new)
    ok(new["style_en"].startswith("live-action cinematic film") and "teal and amber" in new["style_en"], f"style_en: {new['style_en']}")
    ok(not looks.has_banned(new["cast"][0]["en"]), "apply: the hero's look cleaned")
    s1 = new["style_en"]
    looks.apply(new)
    ok(new["style_en"] == s1, "apply is idempotent")
    looks.choose(new, look="noir")
    looks.restyle(new)
    ok(new["style_en"].startswith("black and white film noir") and "live-action cinematic" not in new["style_en"]
       and "teal and amber" in new["style_en"], f"restyle swaps the fragment: {new['style_en']}")
    ok(len(new["style_en"].split()) <= 50, "style_en within the validator's 50 words")
    long = looks.choose({"style_en": " ".join(["word"] * 60)}, look="anime")
    looks.apply(long)
    ok(len(long["style_en"].split()) <= 47, "a long style is cut to fit")
    # the portrait
    plan = {"style_en": new["style_en"], "cast": [{"gender": "female", "age": 25, "en": "a 25-year-old woman with freckles"}], "scenes": [], **{k: new[k] for k in looks.KEYS if k in new}}
    pr = hero.prompt_of(plan, 0)
    ok("A waist-up black and white photo of" in pr and looks.WOMAN_PHOTO in pr, f"live portrait: {pr[:120]}...")
    pr_old = hero.prompt_of({"style_en": "x", "cast": plan["cast"], "scenes": []}, 0)
    ok("A medium close-up photo of" in pr_old and looks.WOMAN_PHOTO not in pr_old, "an older plan's portrait as it was")
    pr_an = hero.prompt_of({**plan, "look": "anime"}, 0)
    ok("anime illustration of" in pr_an and looks.WOMAN_PHOTO not in pr_an, "anime portrait: an illustration, no photo anchor")
    ok(looks.camera_for_singing(an) == "push_in" and looks.camera_for_singing(new) is None, "singing camera: anime push-in, live - the default")
    ok(looks.insert_share(looks.choose({}, "concert", "auto"), 0.5) == 0.2 and looks.insert_share({}, 0.5) == 0.5,
       "insert share: the delivery's, an older plan the caller's")

    v2 = {"mode": "one", "idea": "x", "looks_v": 1, "look": "live_cinema", "delivery": "cinema", "rules_v": 2}
    words = ("feminine", "attractive", "beautiful", "handsome")
    ok(not any(w in writer._mode_rules(v2) for w in words) and "real person" in writer._mode_rules(v2), "rules_v 2: no beauty words, the real-person anchors stay")
    ok("attractive" in writer._mode_rules({**v2, "rules_v": 1}), "rules_v 1: its rules word for word")
    ok(not any(w in writer.looks_system(v2) for w in words) and "attractive" in writer.looks_system({}), "the look translation: rules_v 2 without, older with")
    ok(hero.who_of({"gender": "female", "age": 24}, v2) == "one young woman" and "beautiful" in hero.who_of({"gender": "female", "age": 24}), "Krea portrait: rules_v 2 says the gender and age only")

def v3() -> None:
    print("rules_v 3: no tool names in the style, a calm singing mouth")
    from app import frames
    ok(looks.no_tools("Shot on LTX-2.5, warm light, film grain") == "warm light, film grain", "«Shot on LTX-2.5» out")
    ok(looks.no_tools("cinematic, shot with MiniMax H3 model, soft grain.") == "cinematic, soft grain.", "«shot with MiniMax H3 model» out")
    ok(looks.no_tools("Krea 2 portrait look, FLUX.2 colours, ComfyUI render") == "portrait look, colours, render", "bare names out")
    ok(looks.no_tools("shot on 35mm film, soft light") == "shot on 35mm film, soft light", "35mm film and plain words stay")
    base = {"mode": "one", "idea": "x", "looks_v": 1, "look": "live_cinema", "delivery": "cinema"}
    s3 = looks.style_for({**base, "rules_v": 3}, "Shot on LTX-2.5, warm light")
    ok("LTX" not in s3 and "warm light" in s3, "style_for, rules_v 3: " + s3)
    ok("LTX" in looks.style_for({**base, "rules_v": 2}, "Shot on LTX-2.5, warm light"), "style_for, rules_v 2: word for word")
    ok(writer.RULES_V >= 3 and "Never name a video model" in writer._rules_v2({**base, "rules_v": 3}) and "Never name a video model" not in writer._rules_v2({**base, "rules_v": 2}),
       "the writer is told not to name tools since rules_v 3 only")
    clip3 = {**base, "clip": {"name": "s"}, "prompt_v": 3, "rules_v": 3}
    ok("softly" in writer._clip_rules(clip3) and "wide-open" in writer._clip_rules(clip3), "rules_v 3: the first beat sings softly")
    ok(writer.SING_FIRST_BEAT in writer._clip_rules({**clip3, "rules_v": 2}), "rules_v 2: the first beat as it was")
    sc = {"shot": "sing", "framing": "close", "beats_en": ["x"]}
    new = frames.first_moment({**clip3, "cast": [{"gender": "female"}]}, sc, ["Anna"])[1]
    old = frames.first_moment({**clip3, "rules_v": 2, "cast": [{"gender": "female"}]}, sc, ["Anna"])[1]
    ok("mouth open" not in new and "softly" in new and "mouth open mid-word" in old, "the singer's frame: calm for rules_v 3, as it was for 2")


def cuts() -> None:
    print("a clip's cuts by the delivery")
    from app import song
    bars = [i * 2.0 for i in range(31)]
    a = song.Analysis(duration=60.0, bpm=120, beat_s=0.5, bar_s=2.0, downbeat=0.0, beats=[i * 0.5 for i in range(121)], bars=bars,
                      bar_energy=[0.5] * 31, bar_rms=[0.5] * 31,
                      sections=[song.Section("groove", 0, 30, 0, 15, 0.4), song.Section("drop", 30, 60, 15, 30, 0.9)])
    base = clip.scenes(a, [], 0, 60)
    same = clip.scenes(a, [], 0, 60, shot=looks.DELIVERIES["clip"]["scene"])
    ok([x["seconds"] for x in base] == [x["seconds"] for x in same], "«Клип»: the cuts as before")
    fast = clip.scenes(a, [], 0, 60, shot=looks.DELIVERIES["trailer"]["scene"])
    slow = clip.scenes(a, [], 0, 60, shot=looks.DELIVERIES["meditative"]["scene"])
    ok(len(fast) > len(base) > len(slow), f"trailer {len(fast)} > clip {len(base)} > meditative {len(slow)} scenes")
    ok(all(x["seconds"] <= 8 for x in slow), "no scene longer than MAX_SHOT")


def needs_frame() -> None:
    print("needs_frame: a look drawn by every frame")
    from app import frames, places
    from app.planner import model_prompts, prompts
    ok(set(looks.FRAME_LOOKS) == {"watercolor", "oil", "comic", "sketch", "clay", "stopmotion"}, "six looks drawn by every frame")
    ok(not set(looks.FRAME_LOOKS) & looks.HIDDEN and "papercut" in looks.H3_ONLY and "pixel" in looks.HIDDEN, "offered again; paper cut-out H3 only, pixel art hidden")
    ok(all(looks.LOOKS[k]["needs_frame"] and looks.LOOKS[k]["tech_en"] for k in looks.FRAME_LOOKS), "each has its medium and its technique's phrase")

    def plan(look="watercolor", v=looks.LOOKS_V, mode="one"):
        p = {"mode": mode, "idea": "x", "prompt_v": prompts.PROMPT_V, "rules_v": writer.RULES_V, "looks_v": v, "delivery": "cinema", "look": look,
             "style_en": looks.LOOKS[look]["style_en"] + ", soft morning light.", "video": "ltx25",
             "cast": [{"gender": "female", "age": 25, "en": "a young woman with dark hair", "outfit_en": "a green dress", "photo": "hero.png"}] if mode != "none" else [],
             "world": {"en": "a quiet seaside town"} if mode == "none" else {},
             "scenes": [
                 {"index": 0, "id": "a", "kind": "new", "who": [1], "framing": "medium", "camera": "push_in", "seconds": 5, "place_en": "a pier at dawn",
                  "beats_en": ["She walks along the pier", "she turns to the sea"], "sound_en": "waves"},
                 {"index": 1, "id": "b", "kind": "new", "who": [], "framing": "wide", "camera": "static", "seconds": 5, "place_en": "an empty beach",
                  "beats_en": ["Waves roll onto the empty sand"], "sound_en": "waves"},
                 {"index": 2, "id": "c", "kind": "new", "who": [1], "framing": "medium", "camera": "pan", "seconds": 5, "place_en": "a market street",
                  "beats_en": ["A fisherman sells fish to a crowd"], "frame_people": [5], "sound_en": "voices"},
                 {"index": 3, "id": "d", "kind": "continue", "from": 0, "who": [1], "framing": "medium", "camera": "push_in", "seconds": 5,
                  "beats_en": ["She smiles and waves"], "sound_en": "waves"}]}
        return p

    p = plan()
    ok(looks.needs_frame(p) and not looks.needs_frame(plan("live_cinema")), "needs_frame: watercolour yes, live cinema no")
    ok(not looks.needs_frame(plan(v=1)), "an older plan (looks_v 1) with watercolour: no")
    mine = {**p, "look": "my_x", "look_mine": {"name": "Мой", "base": "oil", "own": ""}}
    ok(looks.needs_frame(mine), "a saved look on oil: drawn by every frame too")
    new = [s for s in p["scenes"] if s["kind"] == "new"]
    ok(all(frames.can_draw(p, s) for s in new), "every new scene gets a picture: the hero's, the empty insert, the people without a photo")
    ok(not frames.can_draw(p, p["scenes"][3]), "a continuation starts from its source's last frame")
    pn = plan(mode="none")
    ok(all(frames.can_draw(pn, s) for s in pn["scenes"] if s["kind"] == "new") and not frames.can_draw(plan("live_cinema", mode="none"), pn["scenes"][1]),
       "a video without people: drawn too (a live look: as before, none)")
    old = plan(v=1)
    ok(not frames.can_draw(old, old["scenes"][2]), "an older plan: the scene whose people have no photo gets none, as before")
    fp = frames.prompt_of(p, p["scenes"][0], ["The heroine"])
    ok("watercolour painting on textured paper, not a photograph" in fp and "Photorealistic" not in fp and "A new picture in this technique" in fp,
       f"the hero's frame says the medium strongly: {fp[:120]}")
    ofp = frames.prompt_of(old, old["scenes"][0], ["The heroine"])
    ok("Photorealistic film still." in ofp and "A new photograph" in ofp, "an older plan's frame prompt as it was")
    ep = frames.empty_prompt(p, p["scenes"][1], False)
    ok("not a photograph" in ep and "no people" in ep, "the empty insert's frame: the medium, nobody")
    wp = frames.empty_prompt(p, p["scenes"][2], False, nobody=False)
    ok("fisherman" in wp and "no people" not in wp and "not a photograph" in wp, "people without a photo: from the words, in the medium")
    ok(frames.empty(pn, pn["scenes"][0]), "a video without people: every frame is empty")
    pl = places.prompt_of(p, {"en": "a pier at dawn"})
    ok(pl.startswith("Watercolour illustration") and "Photorealistic" not in pl and "not a photograph" in pl, "the place is painted in the look")
    ok(places.prompt_of(old, {"en": "a pier at dawn"}).startswith("Photorealistic"), "an older plan's place as it was")

    # the shot: from its picture, the motion and one phrase of the technique - never the style or the place again
    for s in p["scenes"][:3]:
        s.update(start_photo=f"frame_{s['id']}.png", start_en="x")
    for s in p["scenes"]:
        s["prompt"] = prompts.compose(p, s)
    ltx = model_prompts.for_model("ltx25", p, p["scenes"][0])
    ok(ltx.startswith(model_prompts.LTX_HOLD) and "Watercolour animation." in ltx and "a pier at dawn" not in ltx and "soft morning light" not in ltx
       and "soft bleeding washes" not in ltx, f"LTX: motion + the technique, no place, no style: {ltx[:200]}")
    cont = model_prompts.for_model("ltx25", p, p["scenes"][3])
    ok(cont.startswith(model_prompts.LTX_HOLD) and "Watercolour animation." in cont and "soft morning light" not in cont and "pier" not in cont,
       "a continuation too: held on its picture, the technique's phrase")
    h3 = p["scenes"][0]["prompt"]
    ok(prompts.FRAME_HOLD in h3 and "Watercolour animation." in h3 and "soft morning light" not in h3 and "a pier at dawn" not in h3, "H3: held on the picture, the technique's phrase")
    # an older plan: the same scenes, word for word as before (no phrase, the continuation not held)
    for s in old["scenes"][:3]:
        s.update(start_photo=f"frame_{s['id']}.png", start_en="x")
    o0 = model_prompts.for_model("ltx25", old, old["scenes"][0])
    o3 = model_prompts.for_model("ltx25", old, old["scenes"][3])
    ok("Watercolour animation." not in o0 and o0.startswith(model_prompts.LTX_HOLD), "an older plan: held as before, no technique's phrase")
    ok(not o3.startswith(model_prompts.LTX_HOLD) and "soft morning light" in o3, "an older plan's continuation: style and place as before")
    ok("Watercolour animation." not in prompts.compose(old, old["scenes"][0]), "an older plan's H3 prompt: no phrase")
    live = plan("live_cinema")
    for s in live["scenes"][:3]:
        s.update(start_photo=f"frame_{s['id']}.png", start_en="x")
    ok(not model_prompts.for_model("ltx25", live, live["scenes"][3]).startswith(model_prompts.LTX_HOLD), "a live look's continuation: as before")
    pub = looks.public()
    ok(next(x for x in pub["looks"] if x["id"] == "watercolor")["needs_frame"] and not next(x for x in pub["looks"] if x["id"] == "noir")["needs_frame"], "GET /api/looks: needs_frame on the tile")


def graph_look() -> None:
    print("the graph: the look per scene")
    from app import graph
    proj = {"id": "x", "settings": {"delivery": "clip", "look": "vhs90"}}

    class C:
        pass
    c = C()
    c.proj = proj
    w = {"id": "w", "type": "writer", "data": {"plan": {"look_style": looks.LOOKS["live_cinema"]["style_en"]}}}
    pn = {"id": "p", "type": "prompt", "data": {"from": "w"}}
    hand = {"id": "h", "type": "prompt", "data": {}}
    sc = {"id": "s", "type": "scene", "data": {}}
    sc2 = {"id": "s2", "type": "scene", "data": {"look": "anime"}}
    c.by = {n["id"]: n for n in (w, pn, hand, sc, sc2)}
    text = "Live-action cinematic film, natural light, shallow depth of field, subtle film grain, warm dusk. She walks."
    got = graph.look_text(c, "s", "p", text)
    ok(got.startswith("1990s VHS home video look") and "She walks." in got, f"the project's look swaps the writer's: {got[:60]}")
    got = graph.look_text(c, "s2", "p", text)
    ok(got.startswith("Anime style, cel-shaded"), "the scene's own look wins")
    got = graph.look_text(c, "s", "h", "A woman dances in the rain.")
    ok(got.startswith("1990s VHS home video look") and got.endswith("dances in the rain."), "a hand prompt gets the look in front")
    c.proj = {"id": "x", "settings": {}}
    ok(graph.look_text(c, "s", "h", "A woman.") == "A woman.", "an older graph: the text as it is")
    c.proj = {"id": "x", "settings": {"delivery": "auto", "look": "auto"}}
    ok(graph.look_text(c, "s", "h", "A woman.") == "A woman.", "«Решит сценарист»: a hand prompt as it is")


class _Resp:
    def __init__(self, status: int, body: bytes, headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def json(self):
        return json.loads(self.body)


class AsgiClient:
    """FastAPI's TestClient without its httpx dependency: one request straight through the ASGI app (no server)."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, json_body=None, headers=None) -> _Resp:
        from urllib.parse import unquote
        body = json.dumps(json_body).encode() if json_body is not None else b""
        hdrs = [(b"host", b"127.0.0.1")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if json_body is not None:
            hdrs += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        raw = path.split("?")[0]
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
                 "path": unquote(raw), "raw_path": raw.encode(), "query_string": b"", "root_path": "", "headers": hdrs,
                 "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
        out = {"status": 0, "body": b""}
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()   # a file response listens for a disconnect while it streams: stay quiet until cancelled
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
                out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")
        asyncio.run(self.app(scope, receive, send))
        return _Resp(out["status"], out["body"], out.get("headers"))

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, json=None):
        return self.request("POST", path, json)

    def put(self, path, json=None):
        return self.request("PUT", path, json)

    def delete(self, path):
        return self.request("DELETE", path)


def api() -> None:
    print("the API")
    import app.main as main
    TestClient = AsgiClient
    tmp = Path(tempfile.mkdtemp())
    looks.MY_FILE, looks.PREVIEWS = tmp / "my_looks.json", tmp / "looks"
    looks.PREVIEWS.mkdir()
    (looks.PREVIEWS / "noir.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    cl = TestClient(main.app)
    r = cl.get("/api/looks", headers={"X-Lang": "ru"}).json()
    ok(set(r) == {"deliveries", "groups", "looks", "mine", "hidden"}, "GET /api/looks: deliveries, groups, looks, mine, hidden")
    ok(r["deliveries"][0]["id"] == "auto" and r["deliveries"][0]["name"] == "Решит сценарист", "Russian names")
    ok([g["id"] for g in r["groups"]] == ["live", "anim", "drawn", "retro", "genre", "mine"], "the groups' tabs (no empty «Арт»)")
    ok(not {x["id"] for x in r["looks"]} & looks.HIDDEN and len(r["looks"]) == 26, "hidden looks are not offered (18 + 6 drawn by every frame + 1 for H3 + auto)")
    ok([x["id"] for x in r["looks"] if x.get("models")] == ["papercut"] and all(x["models"] == ["h3"] for x in r["looks"] if x.get("models")), "paper cut-out: H3 only")
    ok("papercut" in {x["id"] for x in looks.offered("h3")} and "papercut" not in {x["id"] for x in looks.offered("ltx25")}, "offered(model): H3 gets it, LTX does not")
    ok(sorted(x["id"] for x in r["looks"] if x.get("needs_frame")) == sorted(looks.FRAME_LOOKS), "the six drawn by every frame say so (needs_frame)")
    ok(looks.resolve({"looks_v": 1, "delivery": "clip", "look": "watercolor"})[1] == "watercolor", "a stored plan with a hidden look keeps it")
    ok(next(x for x in r["looks"] if x["id"] == "noir")["preview"] is True, "a drawn preview is said")
    r = cl.get("/api/looks", headers={"X-Lang": "en"}).json()
    ok(r["deliveries"][0]["name"] == "Writer decides" and next(x for x in r["looks"] if x["id"] == "noir")["name"] == "Noir", "English names")
    ok(cl.get("/api/looks/preview/noir").status_code == 200, "a preview served")
    ok(cl.get("/api/looks/preview/anime").headers["content-type"] == "image/webp", "no own preview: the shipped tile")
    ok(cl.get("/api/looks/preview/noir").headers["content-type"] == "image/jpeg", "the studio's own preview wins over the shipped one")
    ok(cl.get("/api/looks/preview/pastel").status_code == 404, "no tile at all (a hidden look): 404, the page draws its placeholder")
    ok(all((looks.TILES / f"{x['id']}.webp").stat().st_size < 30 * 1024 for x in looks.offered()), "every offered look has a shipped tile under 30 KB")
    ok(all(x["preview"] for x in r["looks"] if x["id"] != looks.AUTO), "the list says every offered look has a preview")
    ok(cl.get("/api/looks/preview/..%2F..%2Fsecret").status_code == 404, "an unknown id: 404")
    m = cl.post("/api/looks/mine", json={"name": "Закат у моря", "base": "golden_hour", "own": "выгоревшие цвета"}).json()
    ok(m["id"].startswith("my_") and m["base"] == "golden_hour", "«Сохранить как свой вид»")
    ok(cl.post("/api/looks/mine", json={"name": "", "base": "noir"}).status_code == 400, "a look without a name: 400")
    ok(cl.post("/api/looks/mine", json={"name": "x", "base": "nope"}).status_code == 400, "an unknown base: 400")
    ok(cl.put(f"/api/looks/mine/{m['id']}", json={"name": "Закат", "base": "golden_hour", "own": ""}).json()["name"] == "Закат", "renamed")
    ok(cl.get("/api/looks").json()["mine"][0]["group"] == "mine", "my looks in group «Мои»")
    # a project: the look chosen, changed («пересобрать стиль»), the locked scene's text kept
    plan = writer.new_plan("ночной город", 10, "one", "", "", None, seed=1)
    looks.choose_request(plan, "cinema", "live_cinema", "")
    plan.update(style_en="live-action cinematic film, natural light, shallow depth of field, subtle film grain, cold night.", look_style=looks.LOOKS["live_cinema"]["style_en"],
                cast=[{"gender": "female", "age": 25, "en": "a 25-year-old woman with dark hair and freckles", "outfit_en": "a coat", "ru": "", "outfit_ru": ""}])
    for s in plan["scenes"]:
        s.update(ru="Она идёт.", beats_en=["She walks toward the camera slowly through the rain."], place_en="A night street.", locked=s["index"] == 0)
    proj = projects.create({"idea": "x", "seconds": 10, "mode": "one", "aspect": "16:9", "video_model": "h3", "quality": "draft"})
    try:
        proj["plan"] = check.plan(plan)
        projects.save(proj)
        g = cl.get(f"/api/projects/{proj['id']}").json()
        ok(g["looks"]["delivery"] == "cinema" and g["looks"]["effective"]["look"] == "live_cinema", "GET project: looks state")
        beats = g["plan"]["scenes"][0]["beats_en"]
        r = cl.put(f"/api/projects/{proj['id']}", json={"settings": {"look": m["id"]}})
        ok(r.status_code == 200, f"PUT look: {r.status_code}")
        p = r.json()
        ok(p["plan"]["style_en"].startswith("live-action golden hour") and "cold night" in p["plan"]["style_en"], f"style rebuilt: {p['plan']['style_en']}")
        ok(p["plan"]["scenes"][0]["beats_en"] == beats and p["plan"]["scenes"][0]["locked"], "the locked scene's text stays")
        ok(p["plan"]["look_mine"]["base"] == "golden_hour" and p["settings"]["look"] == m["id"], "a saved look kept as a snapshot")
        ok("golden hour" in p["plan"]["scenes"][1]["prompt"], "the scene prompts follow the new style")
        ok(p["looks"]["effective"]["look"] == "golden_hour", "the state says the base look in effect")
        r = cl.put(f"/api/projects/{proj['id']}", json={"settings": {"delivery": "trailer", "look": "anime", "look_own": "пастельные тона"}}).json()
        ok(r["plan"]["delivery"] == "trailer" and r["plan"]["look_own"] == "пастельные тона" and "look_mine" not in r["plan"], "delivery and own words")
        # an older project: mood only
        old = projects.load(proj["id"])
        for k in looks.KEYS + ("look_style",):
            old["plan"].pop(k, None)
        old["plan"]["mood"] = "epic fantasy with visual effects, photoreal"
        projects.save(old)
        g = cl.get(f"/api/projects/{proj['id']}").json()
        ok(g["looks"]["delivery"] == "cinema" and g["looks"]["look"] == "fantasy_epic", "an older project reads as cinema + fantasy epic")
        ok("delivery" not in projects.load(proj["id"])["plan"], "... without saving it")
    finally:
        projects.remove(proj["id"])
    ok(cl.delete(f"/api/looks/mine/{m['id']}").status_code == 200 and not looks.my_looks(), "my look removed")
    ok(cl.delete("/api/looks/mine/my_nope").status_code == 404, "an unknown one: 404")
    # an older client's «Настроение» on a new plan
    p = writer.new_plan("x", 10, "one")
    looks.choose_request(p, "", "", "", "Фэнтези и эффекты")
    ok((p["delivery"], p["look"]) == ("cinema", "fantasy_epic") and looks.active(p), "an older client's mood on a new plan")


if __name__ == "__main__":
    lists()
    clean()
    migrate()
    pick()
    rules()
    v3()
    cuts()
    needs_frame()
    graph_look()
    api()
    print("FAILED: %d" % failed if failed else "all passed")
    sys.exit(1 if failed else 0)
