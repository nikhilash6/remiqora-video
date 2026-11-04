"""A story from an idea: the project the idea screen's «Расписать сцены» makes and its writer's work - shared by that
button (main.py, a «write» job) and by «Сделать ролик» from text on the home (autopilot.py, its «Сценарий» stage), so
both write the same storyboard from the same request."""
from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import config, i18n, jobs, looks, picture, projects, timings, videomodels
from .engine import graphs
from .llm import client as llm, writer
from .planner import check, grid


class NewProject(BaseModel):
    idea: str = Field("", max_length=4000)
    seconds: float = Field(30, ge=5, le=grid.MAX_SECONDS)
    mode: str = "one"                # one | several | none: who is in the frame
    aspect: str = "16:9"
    mood: str = Field("", max_length=200)   # an older client's «Настроение»: mapped to a delivery when none is given
    delivery: str = Field("", max_length=40)   # «Подача» (looks.DELIVERIES), "auto" - the writer picks
    look: str = Field("", max_length=40)       # «Вид» (looks.LOOKS or a saved «my_…»), "auto" - the writer picks
    look_own: str = Field("", max_length=600)  # «Свой вид»: the user's own words about the look
    music: str = Field("", max_length=300)
    photo: Optional[str] = None      # a name from /api/frames: the hero's photo, described by the writer
    photo_start: bool = False        # the photo is also the very first frame: scene 1 starts from it (i2v)
    hero_text: str = Field("", max_length=1200)   # «Герой своими словами»: the hero in the user's own words, any language (cast 1, fixed)
    hero_kind: str = "person"        # person | animal | robot | creature (the kind of that hero)
    hero_gender: str = "other"       # a person's: female | male | other
    seed: int = -1
    model: Optional[str] = None
    manual: bool = False             # no writer: empty scenes, the user writes every prompt by hand
    video_model: str = ""            # phase 10 ("": the default one, videomodels.default()): which model shoots it (videomodels.CARDS)
    loras: list[dict] = []           # the video's LoRA [{id, name, strength}], as «Съёмка» → LoRA keeps them
    pic_loras: dict = {}             # «LoRA картинок»: {"krea": [...], "flux2": [...]} (picture.project_loras)


# «Кто в кадре: по идее» (the home sends mode "auto" unless the user picked one): the idea's own words, no LLM call.
# «парень и девушка встречаются под дождём» was shot with one heroine - the page always sent «one».
_PERSON = (r"(?:парен|парн|девуш|девчон|мальчик|мальчиш|мужчин|женщин|челове|юнош|дев[оа]чк|бабушк|дедушк|старик|стару[шх]|"
           r"мам[аыуе]|пап[аыуе]|отец|отц|сын|дочь|дочк|брат|сестр|друг(?:а|у|ом|ья|ей|ом)?\b|подруг|влюблённ|влюблен|пар[аыуе]\b|"
           r"man\b|men\b|woman|women|boy|girl|guy|lady|mother|father|mom\b|dad\b|brother|sister|friend|lovers?|couple)")
_GROUP = re.compile(r"\b(?:двое|двоих|вдвоём|вдвоем|трое|втроём|втроем|вчетвером|пара|пары|парой|пару|друзья|друзей|подруги|"
                    r"семья|семьи|семью|толпа|толпы|люди|людей|дети|детей|танцующие|прохожие|влюблённые|влюбленные|"
                    r"они|команда|группа|компания|two|three|couple|friends|family|crowd|people|group|"
                    r"kids|children|they|lovers|band)\b", re.I)
# «два щенка» / «three kittens»: a count and a creature, no person word - several characters all the same (rules_v 4's writer
# decides the kind; this only stops the count from being «one»)
_GROUP_CREATURES = re.compile(r"\b(?:два|две|три|четыре|two|three|four)\s+(?:маленьк\w+\s+|small\s+|little\s+|cute\s+)?(?:щен\w+|котят\w*|котёнк\w*|котенк\w*|"
                              r"кот(?:а|ов)\b|кошк\w+|собак\w+|птиц\w*|птенц\w+|медвежон\w+|медвежат\w*|лисен\w+|лисят\w*|зайц\w+|"
                              r"кролик\w*|лошад\w+|пингвин\w*|дракон\w*|робот\w*|puppies|puppy|kittens?|dogs|cats|birds|bears|rabbits|"
                              r"foxes|robots|dragons|penguins|horses)", re.I)
# A person with a pet / robot / creature («девушка с собакой», «a man and his cat»): two heroes, a person and a non-person
# (rules_v 4's cast kinds). Whole words only: «котлета», «скотина», «который», «конец», «песня» must not match. Birds in general
# (птицы, голуби) are left out on purpose: they are usually background. A companion word in a background phrase («девушка в
# шляпе с котом на футболке») still counts - accepted. A companion alone stays «one» (the hero is the animal).
_COMPANION = re.compile(r"\b(?:собак\w*|собач\w*|пёс|пес|пса|псу|псом|псе|псы|псов|щен\w*|кот|кота|коту|котом|коте|коты|котов|"
                        r"котик\w*|котёнк\w*|котенк\w*|котят\w*|кошк\w*|кошеч\w*|лошад\w*|конь|коня|коню|конём|конем|коне|кони|коней|"
                        r"пони|попуга\w*|кролик\w*|хомяк\w*|черепах\w*|робот\w*|дракон\w*|единорог\w*|"
                        r"dogs?|puppy|puppies|cats?|kittens?|kitty|horses?|ponys?|ponies|parrots?|rabbits?|bunny|hamsters?|"
                        r"robots?|dragons?|unicorns?)\b", re.I)
_NOBODY = re.compile(r"\b(?:без людей|никого нет|пустая? |пустой |пустые |пейзаж|природа|no people|nobody|empty|landscape|"
                     r"timelapse|таймлапс)", re.I)


def people_of(idea: str, photo: bool = False) -> str:
    """The people count (grid.MODES) from the idea's words: no one named -> «one» (as the page always sent); two
    different people named, or a group word -> «several»; «без людей», a landscape -> «none» (never with a photo)."""
    text = (idea or "").lower().split(" shot plan:")[0]
    if photo:
        return "one"
    if _NOBODY.search(text) and not re.search(_PERSON, text):
        return "none"
    kinds = {m.group(0) for m in re.finditer(_PERSON, text)}
    if len(kinds) >= 2 or _GROUP.search(text) or _GROUP_CREATURES.search(text) or (kinds and _COMPANION.search(text)):
        return "several"
    return "one"


def check_form(req: NewProject) -> None:
    """The mode, the frame and the video model (a project without the writer too). No model asked: the default one."""
    req.video_model = videomodels.pick(req.video_model)
    if req.mode == "auto":
        req.mode = people_of(req.idea, bool(req.photo))
    if req.mode not in grid.MODES:
        raise HTTPException(400, i18n.L(f"Неизвестный режим: {req.mode}", f"Unknown mode: {req.mode}"))
    if req.aspect not in graphs.ASPECTS:
        raise HTTPException(400, i18n.L(f"Неизвестный формат кадра: {req.aspect}", f"Unknown frame format: {req.aspect}"))
    if not videomodels.offered(req.video_model):
        raise HTTPException(400, i18n.L(f"Неизвестная модель: {req.video_model}", f"Unknown model: {req.video_model}"))


def check_new(req: NewProject):
    """What would fail at once for a written project: the form, the idea, the writer, the photo. -> the writer (llm.find)."""
    check_form(req)
    if len(req.idea.strip()) < 3:
        raise HTTPException(400, i18n.L("Напишите идею", "Write an idea"))
    try:
        model = llm.find(req.model)
    except llm.LLMError as e:
        raise HTTPException(400, str(e)) from e
    if req.photo:
        if Path(req.photo).name != req.photo or not (config.COMFY_INPUT / req.photo).exists():
            raise HTTPException(400, i18n.L("Фото не найдено, загрузите его снова", "Photo not found, upload it again"))
        if (req.mode != "none" or req.photo_start) and not model.mmproj:
            raise HTTPException(400, i18n.L(f"Модель {model.name} не видит картинок: выберите другую или уберите фото", f"Model {model.name} can't see pictures: choose another one or remove the photo"))
    return model


def create(req: NewProject, model) -> dict:
    """The project on disk (no plan yet), the photo copied into it. -> the project."""
    settings = {**req.model_dump(exclude={"photo", "model"}), "model": model.id, "quality": "draft", "loras": picture.pick_loras(req.loras), "rtx": True,
                "pic_loras": picture.pick_pic_loras(req.pic_loras)}
    proj = projects.create(settings)
    if req.photo:
        settings["photo"] = projects.put_photo(proj["id"], config.COMFY_INPUT / Path(req.photo).name)
        projects.save(proj)
    return proj


def plan_for(req: NewProject, model, photo: Optional[str]) -> dict:
    """The storyboard's grid the writer fills: the scenes by the model's length, «Подача» and «Вид», the first frame."""
    mood = req.mood.strip() if req.mood.strip() not in looks.MOOD_MAP else ""   # a known mood is a delivery; a free one goes as it is
    plan = writer.new_plan(req.idea, req.seconds, req.mode, mood, req.music, None,
                           seed=req.seed if req.seed >= 0 else random.randrange(100000), model=model.id,
                           scene_seconds=videomodels.card(req.video_model).grid_seconds)   # LTX: scenes of 10 s
    looks.choose_request(plan, req.delivery, req.look, req.look_own, req.mood)   # the writer reads them, not a mood
    if req.hero_text.strip() and req.mode != "none":   # cast 1 is the user's: the writer only puts it into English (writer.write)
        kind = req.hero_kind if req.hero_kind in writer.KINDS else "person"
        plan["cast"] = [{"kind": kind, "gender": req.hero_gender if kind == "person" and req.hero_gender in writer.GENDERS else "other",
                         "age": 0, "ru": req.hero_text.strip(), "outfit_ru": "", "en": "", "outfit_en": "", "ru_dirty": True, "from_user": True}]
    if photo and req.photo_start:
        plan["scenes"][0]["start_photo"] = photo
    plan["video"] = req.video_model   # the writer knows what shoots (writer.MODEL_NOTES)
    return plan


async def write(pid: str, plan: dict, mode: str) -> dict:
    """The writer writes the plan (the photo described first when there are people) into the project. -> the project."""
    p = await writer.write(plan, projects.photo_path(projects.load(pid)) if mode != "none" else None, projects.folder(pid))
    timings.record_writer(p.get("writer") or {}, len(p["scenes"]))
    cur = projects.load(pid)
    cur["plan"] = check.plan(p)
    cur["name"] = p.get("title_ru") or cur.get("name")
    return projects.save(cur)


def writer_seconds(model, n: int, photo: bool) -> float:
    return timings.writer_seconds(model.name, n, 8 + 3 * n) + (10 if photo else 0)


def writer_job(pid: str, req: NewProject, model, photo: Optional[str]) -> dict:
    """The writer's job of a new project: the storyboard written from the idea and saved into the project."""
    plan = plan_for(req, model, photo)

    async def run(job):
        await write(pid, plan, req.mode)
        return {"project": pid}

    n = len(plan["scenes"])
    job = jobs.submit_task("write", {"project": pid, "idea": req.idea[:80], "scenes": n}, run, estimate=writer_seconds(model, n, bool(photo)))
    return job.public()


def look_now(proj: dict) -> dict:
    """The project's «Подача» / «Вид» / «Свой вид» as they stand: the written plan's, else the settings' (an older
    project's mood mapped, not saved)."""
    plan = proj.get("plan")
    st = proj.get("settings") or {}
    src = dict(plan) if plan and plan.get("scenes") else {k: v for k, v in st.items() if k in looks.KEYS}
    looks.migrate(src, st.get("mood", ""))
    return {"delivery": src.get("delivery", looks.AUTO), "look": src.get("look", looks.AUTO), "look_own": src.get("look_own", "") or ""}


def change_look(proj: dict, st: dict) -> bool:
    """«Подача» / «Вид» changed on a project: kept in its settings (a graph's style for all its scenes); a written plan
    takes them. Only what differs from what stands counts - the board sends its whole settings with every save (the
    quality, the LoRA), and that must not touch the look. A new look (or own words) rebuilds the style («пересобрать
    стиль»: the look's fragment swapped, the heroes by its rules) - the scenes' own texts, a locked scene's too, stay. A
    new delivery alone rebuilds nothing; on an older plan (written before the looks, look «Решит сценарист», no pick) it
    stays an older plan: its style and its heroes' looks are not rewritten by a look nobody chose. -> anything changed."""
    now = look_now(proj)
    diff = {k: str(st[k]).strip() if k == "look_own" else str(st[k]) for k in ("delivery", "look", "look_own")
            if isinstance(st.get(k), str) and (str(st[k]).strip() if k == "look_own" else str(st[k])) != now[k]}
    if not diff:
        return False
    get = diff.get
    # the settings follow the plan's choice (they may lag: a clip's, or one made before the looks), then the change
    probe = looks.choose({**{k: v for k, v in proj["settings"].items() if k in looks.KEYS}, **now}, get("delivery"), get("look"), get("look_own"))
    for k in looks.KEYS:
        if k in probe:
            proj["settings"][k] = probe[k]
        else:
            proj["settings"].pop(k, None)
    plan = proj.get("plan")
    if plan and plan.get("scenes"):
        older = not looks.active(plan)
        looks.migrate(plan, proj["settings"].get("mood", ""))
        prev_v = plan.get("looks_v")
        looks.choose(plan, get("delivery"), get("look"), get("look_own"))
        looked = "look" in diff or "look_own" in diff
        if not looked and prev_v:
            plan["looks_v"] = prev_v   # only the delivery changed: the plan keeps the rules it was written by (frames, cache keys)
        if older and not looked:
            plan.pop("looks_v", None)   # still an older plan: the delivery goes to the writer's brief from now on
        elif looked and (plan.get("style_en") or plan.get("cast")) and not plan.get("manual"):
            looks.restyle(plan)
        proj["plan"] = check.plan(plan)
    return True
