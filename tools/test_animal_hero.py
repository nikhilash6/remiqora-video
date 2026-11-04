"""A hero that is an animal (rules_v 4) without a GPU or an LLM: the writer's rules and cast schema, Krea's portrait, the
scene prompt, FLUX's frame prompt; and an older plan (rules_v 3) word for word (the LLM cache keys).

    .venv\\Scripts\\python.exe tools\\test_animal_hero.py
    .venv\\Scripts\\python.exe tools\\test_animal_hero.py --dump   (the old-plan texts, to compare two checkouts)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import frames, hero, story  # noqa: E402
from app.llm import writer  # noqa: E402
from app.planner import prompts  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


WOMAN = {"gender": "female", "age": 24, "en": "a 24-year-old woman with a slim build, fair skin, grey eyes and long red hair",
         "outfit_en": "a green chiffon dress", "ru": "девушка", "outfit_ru": "платье", "photo": "a.png"}
KITTEN = {"kind": "animal", "gender": "other", "age": 0, "photo": "k.png", "outfit_en": "", "ru": "котёнок", "outfit_ru": "",
          "en": "a ginger kitten with white paws, a pink nose, big green eyes and a tiny white spot on the chest, soft fur"}
DOG = {"kind": "animal", "gender": "other", "age": 0, "photo": "d.png", "outfit_en": "", "ru": "пёс", "outfit_ru": "",
       "en": "a golden retriever puppy with a fluffy cream coat, dark brown eyes and floppy ears, a white blaze on the nose"}


def plan_of(v: int, mode: str, cast: list, beats=("The kitten chases a butterfly across the green lawn.",)) -> dict:
    scenes = [{"id": "s1", "kind": "new", "beats_en": list(beats), "place_en": "A sunny lawn in a garden", "who": [1],
               "framing": "medium", "camera": "static", "to_en": "", "sound_en": "birds"}]
    p = {"idea": "рыжий котенок играет на газоне", "mode": mode, "cast": cast, "scenes": scenes, "style_en": "Warm natural light, 35mm, soft grain, bright greens",
         "prompt_v": prompts.PROMPT_V, "ref_photo": True}
    if v:
        p["rules_v"] = v
    return p


def dump() -> dict:
    """Texts of an old plan: they must not change a byte."""
    out = {}
    for v in (0, 1, 2, 3):
        pl = plan_of(v, "one", [dict(WOMAN)], ("She walks across the lawn toward the camera.",))
        out[f"rules_one_{v}"] = writer._mode_rules(pl)
        out[f"rules_sev_{v}"] = writer._mode_rules({**pl, "mode": "several"})
        out[f"system_{v}"] = writer._system(pl)
        out[f"looks_system_{v}"] = writer.looks_system(pl)
        out[f"schema_{v}"] = json.dumps(writer._plan_schema("one", 2, (1, 1), pl), sort_keys=True)
        out[f"lock_{v}"] = prompts.lock_text(pl, pl["scenes"][0])
        out[f"scene_{v}"] = prompts.compose(pl, pl["scenes"][0])
        out[f"hero_{v}"] = hero.prompt_of(pl, 0)
        out[f"frame_{v}"] = frames.prompt_of(pl, pl["scenes"][0], ["The heroine"], False, False, ["a green chiffon dress"])
        out[f"fix_{v}"] = json.dumps(_fixed(pl), sort_keys=True)
    return out


def _fixed(pl: dict) -> list:
    import copy
    q = copy.deepcopy(pl)
    q["cast"][0]["en"] = "Slim build, fair skin, grey eyes, long red hair wearing a dress"
    prompts.fix_cast(q)
    return q["cast"]


def main() -> None:
    if "--dump" in sys.argv:
        print(json.dumps({k: hashlib.sha1(v.encode()).hexdigest()[:12] for k, v in dump().items()}, indent=1, sort_keys=True))
        return

    print("rules version")
    ok(writer.RULES_V == 5, "RULES_V is 5")
    for v in (0, 2, 3):
        pl = plan_of(v, "one", [dict(WOMAN)])
        ok("kind" not in writer._cast_item(pl)["properties"], f"rules_v {v}: the cast schema has no kind")
        ok("animal" not in writer._mode_rules(pl) and "animal" not in writer._mode_rules({**pl, "mode": "several"}), f"rules_v {v}: the rules say nothing of animals")
    ok(writer._mode_rules(plan_of(3, "one", [])) == writer.MODE_RULES["one"].replace(writer.LOOK_RULE, writer.LOOK_RULE_V2 + writer._rules_people(plan_of(3, "one", []))),
       "rules_v 3 one: LOOK_RULE_V2 + PEOPLE_FROM_IDEA as before")

    print("the writer, rules_v 4")
    for mode in ("one", "several"):
        pl = plan_of(4, mode, [])
        r = writer._mode_rules(pl)
        ok('kind: "person", "animal", "robot" or "creature"' in r and "NO person appears" in r, f"{mode}: the rules name kind and say no person for an animal idea")
        ok("never she / he" in r, f"{mode}: it / the kitten, not she / he")
        ok("For a person (kind person only)" in r, f"{mode}: the look wording is about people only")
    item = writer._cast_item(plan_of(4, "one", []))
    ok(item["properties"]["kind"]["enum"] == ["person", "animal", "robot", "creature"] and "kind" in item["required"], "the cast item has kind: person / animal, required")
    ok(list(item["properties"])[:2] == ["kind", "gender"], "kind comes first")
    sch = writer._plan_schema("one", 2, (1, 1), plan_of(4, "one", []))
    ok("kind" in sch["properties"]["cast"]["items"]["properties"], "the plan schema carries it")
    ok("kind" in writer._outline_schema("one", 2, (1, 1), plan_of(4, "one", []))["properties"]["cast"]["items"]["properties"], "the long film's outline too")
    ok("kind" not in writer._outline_schema("one", 2, (1, 1))["properties"]["cast"]["items"]["properties"], "without a plan: as before")
    ok("animal" in writer.looks_system(plan_of(4, "one", [])) and "animal" not in writer.looks_system(plan_of(3, "one", [])), "the look translator knows animals from rules_v 4")

    print("the idea's count")
    ok(story.people_of("рыжий котенок играет на газоне") == "one", "a kitten: one")
    ok(story.people_of("два щенка играют в снегу") == "several", "two puppies: several (a group word)")
    ok(story.people_of("кот и пёс гуляют") == "one", "a cat and a dog: one (the writer may take two)")
    ok(story.people_of("три маленьких котёнка спят") == "several" and story.people_of("три дня в дороге") == "one", "three kittens: several; three days: one")

    print("the hero's portrait")
    pl = plan_of(4, "one", [dict(KITTEN)])
    t = hero.prompt_of(pl, 0)
    ok("one animal" in t and "Only this one animal in the picture" in t, "Krea: one animal, only this one animal")
    ok(not any(w in t.lower() for w in ("woman", "man ", "person", "neutral expression", "face,", "makeup", "feminine")), "Krea: no person words")
    ok("ginger kitten" in t, "Krea: the look is in it")
    pw = plan_of(4, "one", [dict(WOMAN)])
    ok("Only this one person in the picture" in hero.prompt_of(pw, 0), "a woman at rules_v 4: still a person")
    ok(hero.who_of({"gender": "other", "age": 40}, plan_of(3, "one", [])) == "one person", "no kind (an older plan): who_of untouched")
    ok(hero.who_of(KITTEN, pl) == "one animal", "rules_v 4 who_of: one animal")

    print("the scene prompt")
    p = prompts.compose(pl, pl["scenes"][0])
    ok("The kitten chases" in p and "is the animal in <Picture 1>" in p and "its fur, markings" in p.lower() and "face, hair and outfit" not in p,
       "compose: the beat, the animal's reference line and tail, no hair / outfit")
    lock = prompts.lock_text(pl, pl["scenes"][0])
    ok("the same animal in every shot" in lock and "ginger kitten" in lock, "the lock: the same animal")
    ok(prompts.has_people({"beats_en": ["The kitten chases a butterfly."]}, pl), "an animal hero is in a scene that names no pronoun")
    ok(not prompts.has_people({"beats_en": ["The kitten chases a butterfly."]}, plan_of(3, "one", [dict(WOMAN)])), "an old plan: as before")
    ok(prompts._whose(pl, pl["scenes"][0]) == "its", "its, not their")

    print("fix_cast and the checks")
    q = plan_of(4, "one", [dict(KITTEN, age=24)])
    q["cast"][0]["en"] = "ginger kitten with white paws, wearing a red ribbon around the neck, big green eyes, soft fur and a pink nose"
    prompts.fix_cast(q)
    c = q["cast"][0]
    ok(c["age"] == 0 and c["en"].startswith("ginger kitten") and "year-old" not in c["en"] and "wearing" not in c["en"], "an animal: no age, no «woman with», no wearing clause")
    q["cast"][0]["en"] = "tiny baby kitten with ginger fur, big green eyes, white paws, a pink nose and a thin tail, very fluffy"
    ok(not any("adults" in m for m in prompts.cast_problems(q)), "a baby kitten is not a minor")
    w = plan_of(4, "one", [dict(WOMAN, en="a woman with a slim build, fair skin, grey eyes, long red hair, freckles, a baby face")])
    ok(any("adults" in m for m in prompts.cast_problems(w)), "a person: the minor check stays")
    ok(not [m for m in prompts.scene_problems(pl, pl["scenes"][0]) if "character is not" in m or "other people" in m], "no false stranger / gender problems")

    print("FLUX frame prompt")
    s0 = pl["scenes"][0]
    f = frames.prompt_of(pl, s0, ["The main character"], False, False, [""])
    ok("Only this one animal in the picture" in f and "is the animal in the first image" in f and "fur" in f, "one animal: counted as an animal")
    ok("person" not in f and "people" not in f, "no person words")
    pm = plan_of(4, "several", [dict(WOMAN), dict(DOG)])
    pm["scenes"][0]["who"] = [1, 2]
    pm["scenes"][0]["frame_people"] = [0, 1]
    f2 = frames.prompt_of(pm, pm["scenes"][0], ["Character 1", "Character 2"], False, False, ["a dress", ""])
    ok("Only this person and this animal in the picture" in f2, "a girl and her dog: one person and one animal")
    ok(frames._only(["person"] * 2) == "Only these 2 people in the picture." and frames._only(["animal"] * 2) == "Only these 2 animals in the picture.", "_only: counts")
    f3 = frames.prompt_of(plan_of(3, "one", [dict(WOMAN)]), s0, ["The heroine"], False, False, ["a dress"])
    ok("Only this one person in the picture" in f3, "an old plan: person")

    print("robot, creature, the user's own hero")
    for k, word in (("robot", "robot"), ("creature", "creature")):
        c = {**KITTEN, "kind": k, "en": "an old rusty delivery robot with one round blue eye, a bent antenna and small wheels, dented grey metal body"}
        q = plan_of(4, "one", [c])
        t = hero.prompt_of(q, 0)
        ok(f"one {word}" in t and f"Only this one {word} in the picture" in t and "woman" not in t and "person" not in t, f"{k}: Krea's portrait")
        ok(f"the same {word} in every shot" in prompts.lock_text(q, q["scenes"][0]), f"{k}: the lock")
        ok(f"Only this one {word} in the picture" in frames.prompt_of(q, q["scenes"][0], ["The main character"], False, False, [""]), f"{k}: FLUX frame")
    ok("Real photo" not in hero.prompt_of(plan_of(4, "one", [dict(KITTEN, kind="creature")]), 0), "a creature: no photo anchor (it may be a cartoon)")
    ok(hero.TWEAKS["bigger"][1] and hero.TWEAKS["worn"][1] and hero.TWEAKS["glow"][1], "the new tweaks exist")
    from types import SimpleNamespace
    req = story.NewProject(idea="робот-курьер теряется в большом городе", seconds=10, mode="one", hero_text="старый ржавый робот", hero_kind="robot")
    pl = story.plan_for(req, SimpleNamespace(id="m"), None)
    h = pl["cast"][0]
    ok(h["kind"] == "robot" and h["from_user"] and h["ru_dirty"] and h["ru"] == "старый ржавый робот" and h["age"] == 0, "own hero text: cast 1 is the user's, fixed, in need of English")
    ok(not story.plan_for(story.NewProject(idea="x idea", seconds=10, mode="one"), SimpleNamespace(id="m"), None)["cast"], "no hero text: no fixed cast")
    ok(not story.plan_for(story.NewProject(idea="x idea", seconds=10, mode="none", hero_text="a robot"), SimpleNamespace(id="m"), None).get("cast"), "no people: the hero text is ignored")
    ok(writer._who_line({"kind": "robot"}, 0).startswith("Robot 1") and "years old" not in writer._who_line({"kind": "person", "gender": "female", "age": 0}, 0), "the look translator's line by kind")

    print("the graph: a «Герой» node by kind")
    import shutil
    from app import graph, projects
    proj = projects.create({"quality": "draft", "aspect": "16:9"})
    try:
        f = projects.folder(proj["id"])
        for nm in ("a.png", "b.png"):
            (f / nm).write_bytes(b"PNGDATA")
        mk = lambda i, t, d: {"id": i, "type": t, "position": {"x": 0, "y": 0}, "data": d}   # noqa: E731
        nodes = [mk("idea", "idea", {"text": "робот-курьер"}),
                 mk("hp", "hero", {"en": "a 30-year-old woman, grey eyes", "gender": "female", "age": 30, "photo": "a.png"}),
                 mk("hr", "hero", {"en": "an old rusty robot with one blue eye", "kind": "robot", "photo": "b.png"}),
                 mk("w", "writer", {"seconds": 10, "mode": "several"}),
                 mk("sc", "scene", {})]
        edges = [{"id": "e1", "source": "idea", "sourceHandle": "text", "target": "w", "targetHandle": "idea"},
                 {"id": "e2", "source": "hp", "sourceHandle": "look", "target": "w", "targetHandle": "cast"},
                 {"id": "e3", "source": "hr", "sourceHandle": "look", "target": "w", "targetHandle": "cast"},
                 {"id": "e4", "source": "hp", "sourceHandle": "image", "target": "sc", "targetHandle": "faces"},
                 {"id": "e5", "source": "hr", "sourceHandle": "image", "target": "sc", "targetHandle": "faces"}]
        c = graph.Ctx(projects.load(proj["id"]), {"nodes": nodes, "edges": edges})
        ok(graph.hero_kind(nodes[1]["data"]) == "person" and graph.hero_kind(nodes[2]["data"]) == "robot", "a node without a kind is a person")
        cp, cr = graph.hero_cast(nodes[1]), graph.hero_cast(nodes[2])
        ok("kind" not in cp and "from_user" not in cp, "a person hero: the cast item as before (no new keys)")
        ok(cr["kind"] == "robot" and cr["age"] == 0 and cr["from_user"], "a robot hero: kind goes into the writer's cast, no age")
        ok(graph.faceless_src(c, "hr") and not graph.faceless_src(c, "hp"), "the robot has no face to check")
        ok(len(graph.scene_faces(c, "sc")) == 1 and graph.scene_faces(c, "sc")[0].name == "a.png", "the scene's faces: only the person's")
        idea, heroes = graph.writer_inputs(c, nodes[3])
        old = graph._sha(idea, [{k: h["data"].get(k) for k in ("gender", "age", "en", "outfit_en", "photo")} for h in heroes[:1]],
                        {k: nodes[3]["data"].get(k) for k in ("seconds", "mode", "mood", "music", "ref_photo")})
        only_p = graph.Ctx(projects.load(proj["id"]), {"nodes": [n for n in nodes if n["id"] != "hr"], "edges": [e for e in edges if e["source"] != "hr"]})
        ok(graph.writer_sig(only_p, nodes[3]) == old, "a person-only graph: the writer's signature is the old formula's (cache keys the same)")
        ok(graph.writer_sig(c, nodes[3]) != graph.writer_sig(only_p, nodes[3]), "with a robot hero the signature differs")
        pic = mk("pr", "picture", {"hero": "hr"})
        c.by["pr"] = pic
        ok(graph.faceless_src(c, "pr"), "the robot's portrait node is faceless too")
        ok(not prompts.faces_apply(plan_of(4, "one", [dict(KITTEN)])) and prompts.faces_apply(plan_of(3, "one", [dict(WOMAN)])) and prompts.faces_apply({"cast": []}), "faces_apply: an animal-only cast no, persons / empty yes")
        from app import render
        pl = plan_of(4, "one", [dict(KITTEN)])
        ok(render.face_ref(pl, pl["scenes"][0], Path("x.png"), []) is None, "render.face_ref: nothing to check for an animal")
        ok(render.face_ref(plan_of(3, "one", [dict(WOMAN)]), pl["scenes"][0], Path("x.png"), []) == Path("x.png"), "render.face_ref: a person, as before")
        ok(hero.faces_on("x.png", dict(KITTEN)) == -1, "no face fault on an animal's try")
    finally:
        shutil.rmtree(projects.folder(proj["id"]), ignore_errors=True)

    from app import looks
    lp = {"mode": "one", "idea": "x", "looks_v": 1, "look": "live_cinema", "delivery": "cinema", "rules_v": 4, "style_en": "warm light",
          "cast": [dict(KITTEN), dict(WOMAN, en="a 24-year-old woman with a slim build and grey eyes")]}
    looks.apply(lp)
    ok("real person" not in lp["cast"][0]["en"].lower() and "real person" in lp["cast"][1]["en"].lower(), "a live look: «a real person» anchors for people only")

    print("a photo's description by kind")
    from app.llm import describe
    ok(describe.system_for("person") == describe.SYSTEM and describe.system_for("") == describe.SYSTEM, "a person: the description brief as before")
    for k in ("animal", "robot", "creature"):
        sy = describe.system_for(k)
        ok(k in sy and "age: 0" in sy and "starting with" not in sy and "year-old" not in sy and "hair (color" not in sy, f"{k}: described as itself, no age, no hair")
    import asyncio
    seen = {}

    class FakeLlm:
        model = SimpleNamespace(name="fake")

        async def chat_json(self, system, user, schema, **kw):
            seen["system"], seen["user"] = system, user
            return {"gender": "male", "age": 33, "en": "x " * 40, "outfit_en": "", "ru": "один " * 12, "outfit_ru": ""}

    d = asyncio.run(describe.describe_with(FakeLlm(), Path("x.png"), 1, "robot"))
    ok(d["age"] == 0 and d["gender"] == "other" and d["kind"] == "robot" and "robot" in seen["user"], "describe_with(robot): age 0, gender other, kind kept")
    d = asyncio.run(describe.describe_with(FakeLlm(), Path("x.png")))
    ok(d["age"] == 33 and d["gender"] == "male" and "kind" not in d and "person" in seen["user"], "describe_with(): a person as before")

    print("a stylised look holds its medium (rules_v 4)")
    from app import looks as lk_
    def cart(v, look="cartoon3d"):
        pl = plan_of(v, "one", [dict(WOMAN, en="a 24-year-old woman with fair skin with visible texture, large hazel eyes, fine pores, wavy auburn hair")])
        pl.update(looks_v=2, look=look, delivery="fairytale", style_en="3D animated film style, warm light, fine film grain texture, shot with a 35mm lens")
        pl["scenes"] = [{**pl["scenes"][0], "id": "a", "index": 0, "kind": "new", "start_photo": "frame_a.png", "frame": {"seed": 1, "ok": True}},
                        {**pl["scenes"][0], "id": "b", "index": 1, "kind": "continue"}]
        return pl
    new, old = cart(4), cart(3)
    ptexts = [prompts.compose(new, sc) for sc in new["scenes"]]
    ok(all("Keep the 3D animated film look of <Picture 1>" in t for t in ptexts), "cartoon3d: the anchor in every scene, the frame one too")
    ok("3D animated film style" in ptexts[0] and "grain" not in ptexts[0] and "35mm" not in ptexts[1] and "pores" not in ptexts[1] and "visible texture" not in ptexts[1], "the style line in the frame scene; no grain, lens, pores")
    otexts = [prompts.compose(old, sc) for sc in old["scenes"]]
    ok(not any("Keep the 3D" in t for t in otexts) and "3D animated film style" not in otexts[0] and "film grain" in otexts[1] and "pores" in otexts[1], "an older plan: the texts as before")
    live = cart(4, "live_cinema")
    lt = [prompts.compose(live, sc) for sc in live["scenes"]]
    ok(not any("Keep the" in t and "look of <Picture 1>" in t for t in lt) and "pores" in lt[1], "a live look: unchanged")
    ok("anime look" in lk_.medium_anchor(cart(4, "anime")) and "hand-drawn look" in lk_.medium_anchor(cart(4, "pastel")) and lk_.medium_anchor(cart(4, "retro_film")) == "" and lk_.medium_anchor(cart(4, "watercolor")) == "", "anchor by family; none for retro film and the frame-drawn looks (their technique phrase)")

    print("an old plan, word for word")
    got = {k: hashlib.sha1(v.encode()).hexdigest()[:12] for k, v in dump().items()}
    ref = json.loads((ROOT / "tools" / "test_animal_hero.sha.json").read_text(encoding="utf-8")) if (ROOT / "tools" / "test_animal_hero.sha.json").exists() else {}
    for k, v in sorted(ref.items()):
        ok(got.get(k) == v, f"unchanged: {k}")

    print("\nFAILED" if failed else "\nall ok", failed or "")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
