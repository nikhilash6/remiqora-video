"""story.people_of: a person AND a pet / robot / creature -> «several» (two heroes), false friends stay «one». No GPU, no LLM.

    .venv/Scripts/python.exe tools/test_person_pet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import story  # noqa: E402

CASES = [
    ("девушка с собакой гуляет по парку", "several"),
    ("мальчик и его робот", "several"),
    ("a man and his cat", "several"),
    ("a girl riding a horse at sunset", "several"),
    ("старик с попугаем на плече", "several"),
    ("женщина и дракон над городом", "several"),
    ("девушка с котом на руках", "several"),
    ("a boy plays with a puppy", "several"),
    ("щенок бежит по траве", "one"),
    ("a robot dances in the rain", "one"),
    ("девушка ест котлету в кафе", "one"),
    ("девушка читает книгу, который час", "one"),
    ("парень поёт песню на закате", "one"),
    ("девушка в конце улицы покупает конфеты", "one"),
    ("девушка идёт мимо птиц у реки", "one"),
    ("пейзаж без людей, кот на заборе", "none"),
    ("парень и девушка танцуют под дождём", "several"),
    ("a woman in the forest", "one"),
]

failed = 0
for idea, want in CASES:
    got = story.people_of(idea)
    ok = got == want
    failed += not ok
    print(f"  {'ok ' if ok else 'FAIL'} {got:8} {idea!r}" + ("" if ok else f"  (want {want})"))


# ---- rules_v 5: a clip with a non-person cast (robot) ----
from app.llm import writer  # noqa: E402
from app.planner import prompts  # noqa: E402
from app import looks  # noqa: E402


def check(cond, what):
    global failed
    print(f"  {'ok ' if cond else 'FAIL'} {what}")
    failed += not cond


ROBOT = {"kind": "robot", "gender": "other", "age": 0, "en": "a small round robot vacuum with a blue light", "outfit_en": ""}
GIRL = {"gender": "female", "age": 24, "en": "a young woman with freckles", "outfit_en": "a top"}


def plan(v, cast):
    return {"mode": "one", "cast": cast, "rules_v": v, "clip": {"lang": "en"}, "prompt_v": prompts.PROMPT_V}


def scene(beat):
    return {"kind": "new", "beats_en": [beat], "place_en": "A bright room with a window to the sea", "who": [1],
            "to_en": "", "sound_en": "", "ru": "Робот-пылесос катится к окну и мигает огоньком"}


check(writer.RULES_V == 5, "RULES_V is 5")
check(writer.CLIP_NONPERSON in writer._clip_rules(plan(5, [ROBOT])), "v5 robot clip: the non-person singing rule is added")
check(writer.CLIP_NONPERSON not in writer._clip_rules(plan(5, [GIRL])), "v5 person clip: no extra rule")
check(writer._clip_rules(plan(4, [ROBOT])) == writer._clip_rules(plan(4, [GIRL])), "v4 robot clip: rules byte-identical to a person's")
bad = scene("A young woman with freckles and sunglasses stands on a balcony, swaying her shoulders to the beat.")
good = scene("The small round robot rolls toward the window, its blue light pulsing to the beat.")
check(any("no person" in x for x in prompts.scene_problems(plan(5, [ROBOT]), bad)), "v5: a woman in a robot's clip is a problem")
check(any("no person" in x for x in prompts.scene_problems(plan(5, [ROBOT]), scene("She smooths her dress and adjusts her sunglasses.") )), "v5: a dress is a problem")
check(not any("no person" in x for x in prompts.scene_problems(plan(5, [ROBOT]), scene("He spins and blinks his blue light.") )), "v5: 'he' for the robot is fine")
check(not any("no person" in x for x in prompts.scene_problems(plan(5, [ROBOT]), good)), "v5: the robot's own scene is fine")
check(not any("no person" in x for x in prompts.scene_problems(plan(5, [GIRL]), bad)), "v5: a person cast may have a woman")
check(not any("no person" in x for x in prompts.scene_problems(plan(5, [ROBOT, GIRL]) | {"mode": "several"}, bad)), "v5: mixed cast keeps the woman")
check(not any("no person" in x for x in prompts.scene_problems(plan(4, [ROBOT]), bad)), "v4: no new check")

# the grain stripping: no dangling adjective (rules_v 5)
st = "Soft colours, cinematic lighting, gentle film grain."
check(looks.unphoto(st, True) == "Soft colours, cinematic lighting.", "unphoto v5: 'gentle film grain' goes whole")
check(looks.unphoto(st) == "Soft colours, cinematic lighting, gentle.", "unphoto v4: unchanged output")
check(looks.unphoto("warm light, subtle film grain, 35mm lens", True) == "warm light", "unphoto v5: grain and lens")

# rules_v 5: a non-person singer - no lips in the start frame or the audio line, no lips check
from app import frames  # noqa: E402

sing = {**good, "shot": "sing", "framing": "close", "lines": [{"who": 1, "text": "Ciao Riviera", "how": "sing"}]}
quiet = {**good, "shot": "perform", "framing": "wide"}
fr, act = frames.first_moment(plan(5, [ROBOT]), sing, ["The robot"])
check("lips" not in act and "hands" not in act and act == good["beats_en"][0], "v5 robot: the sing frame is its own first beat")
check("lips" in frames.first_moment(plan(4, [ROBOT]), sing, ["The robot"])[1], "v4 robot: the sing frame as before")
check("lips" in frames.first_moment(plan(5, [GIRL]), sing, ["The heroine"])[1], "v5 person: the sing frame as before")
a = prompts.audio_text(plan(5, [ROBOT]), sing)
check("No singing and no speech." in a and "voice" not in a and "lips" not in a, "v5 robot: no singing voice in the audio line")
check("lips" not in prompts.audio_text(plan(5, [ROBOT]), quiet), "v5 robot: no «its lips stay closed»")
check("lips stay closed" in prompts.audio_text(plan(4, [ROBOT]), quiet), "v4 robot: the audio line as before")
check("voice singing" in prompts.audio_text(plan(5, [GIRL]), sing), "v5 person: still sings")
mixed = {**plan(5, [GIRL, ROBOT]), "mode": "several"}
check(prompts.nonperson_singer(mixed, {**sing, "who": [2], "lines": [{"who": 2, "text": "la", "how": "sing"}]}), "mixed cast: the robot's line")
check(not prompts.nonperson_singer(mixed, {**sing, "who": [1, 2]}), "mixed cast: the heroine's line")
print("failed:", failed)
sys.exit(1 if failed else 0)
