"""A photo -> the hero's lock: how the person looks, in English for the prompt and in Russian for the card.
The age is always an explicit adult number (21 or older: a fixed part of the prompt formula, not a filter), and no
names of real people get into the text."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import i18n
from . import client

MIN_AGE = 21

SYSTEM = """You describe the main person in a photo for a video generation prompt. The description is pasted word for word into every scene, so it must fix the look: what stays the same from scene to scene.

Write:
- gender: female, male or other.
- age: the apparent age as one number; the person is an adult, never under 21.
- en: one English paragraph, 35-70 words, starting with "a <age>-year-old" and the gender: build, skin tone, face, eyes, hair (color, length, style), notable features (freckles, tattoos, glasses, makeup). Concrete visual words only, no feelings, no story, no camera or lighting. No clothing here: it goes to outfit_en.
- outfit_en: what the person wears, 10-30 words, colors and materials; "" if not visible.
- ru, outfit_ru: the same descriptions in natural Russian for the user, with every detail of en / outfit_en (ru is a full paragraph, not one word).
Never write a name, even if you recognize the person. Describe only what is visible."""

SCHEMA = {
    "type": "object",
    "properties": {
        "gender": {"type": "string", "enum": ["female", "male", "other"]},
        "age": {"type": "integer"},
        "en": {"type": "string"},
        "outfit_en": {"type": "string"},
        "ru": {"type": "string"},
        "outfit_ru": {"type": "string"},
    },
    "required": ["gender", "age", "en", "outfit_en", "ru", "outfit_ru"],
    "additionalProperties": False,
}


# A hero that is not a person (cast `kind`: animal, robot, creature): the same lock, in the words of what it is. Age and
# gender belong to people only: age 0, gender "other" (an animal's sex only when it is plain to see).
KIND_WHAT = {"animal": "animal", "robot": "robot", "creature": "creature (a fantasy or cartoon being, a toy)"}
KIND_LOOK = {"animal": "species or breed, size, fur / feathers / scales colour and pattern, eyes, distinctive marks",
             "robot": "build and shape first (wheels or legs, arms or none), materials, colours, lights or eyes, wear, scratches and marks",
             "creature": "body, skin / fur / scales, colours, eyes, distinctive features"}


def system_for(kind: str = "person") -> str:
    if kind not in KIND_WHAT:
        return SYSTEM
    return f"""You describe the main {KIND_WHAT[kind]} in a photo for a video generation prompt. The description is pasted word for word into every scene, so it must fix the look: what stays the same from scene to scene.

Write:
- gender: "other" (female or male only for an animal whose sex is plain to see).
- age: 0 (age is for people only).
- en: one English paragraph, 35-70 words: {KIND_LOOK[kind]}. Concrete visual words only, no human words (no face, hair, skin, makeup unless it really has them), no feelings, no story, no camera or lighting.
- outfit_en: anything it wears or carries as part of its look (a collar, a scarf), 5-20 words; "" if nothing.
- ru, outfit_ru: the same descriptions in natural Russian for the user, with every detail of en / outfit_en (ru is a full paragraph, not one word).
Never write a name. Describe only what is visible."""


RU_SCHEMA = {"type": "object", "properties": {"ru": {"type": "string"}, "outfit_ru": {"type": "string"}},
             "required": ["ru", "outfit_ru"], "additionalProperties": False}


async def describe_with(llm: client.Client, image: Path, seed: int = 1, kind: str = "person") -> dict:
    """Inside an open session (the writer describes the photo and writes the storyboard in one model load). `kind`: what the
    hero is (a cast entry's `kind`; anything but a person is described as itself, with no age)."""
    what = KIND_WHAT.get(kind, "person").split(" (")[0]
    d = await llm.chat_json(system_for(kind), f"Describe the main {what} in this photo.", SCHEMA, images=(image,),
                            temperature=0.3, seed=seed, max_tokens=1024, step="photo")
    d["age"] = 0 if kind in KIND_WHAT else max(MIN_AGE, int(d.get("age") or MIN_AGE))
    if kind in KIND_WHAT:
        d["gender"] = d.get("gender") if kind == "animal" and d.get("gender") in ("female", "male") else "other"
        d["kind"] = kind
    if len(d.get("ru", "").split()) < 8:   # a small model sometimes answers ru with one word: translate en instead
        t = await llm.chat_json(f"You translate a description of a {'person' if kind not in KIND_WHAT else what} from English into natural Russian, every detail kept.",
                                f"look: {d.get('en', '')}\noutfit: {d.get('outfit_en', '')}", RU_SCHEMA,
                                temperature=0.2, seed=seed, max_tokens=800, step="photo")
        d.update(ru=t.get("ru") or d.get("ru", ""), outfit_ru=t.get("outfit_ru") or d.get("outfit_ru", ""))
    d["model"] = llm.model.name
    return d


FRAME_SYSTEM = """You describe a photo that becomes the very first frame of a video: the video model starts from exactly this picture and the scene grows out of it. The screenwriter reads your words to continue the moment, so say what is in the frame at second 0.

Write:
- en: 30-60 English words: who or what is in the frame and where in it, their pose and what they are doing, where they look, the framing (close-up, medium or wide), the light and time of day. Concrete visual words only, no story, no feelings, no names (even if you recognize someone).
- place_en: one English sentence, 8-25 words: the place, the light and the time of day, as a location for the scene."""

FRAME_SCHEMA = {"type": "object", "properties": {"en": {"type": "string"}, "place_en": {"type": "string"}},
                "required": ["en", "place_en"], "additionalProperties": False}


async def describe_frame_with(llm: client.Client, image: Path, seed: int = 1) -> dict:
    """A start photo -> {en: what the frame shows at second 0, place_en: where}, for the writer (inside a session)."""
    d = await llm.chat_json(FRAME_SYSTEM, "Describe this first frame.", FRAME_SCHEMA, images=(image,),
                            temperature=0.3, seed=seed, max_tokens=600, step="photo")
    return {"en": str(d.get("en", "")).strip(), "place_en": str(d.get("place_en", "")).strip()}


async def describe(image: Path, model_id: Optional[str] = None, seed: int = 1) -> dict:
    client.use_lang(i18n.text_name())   # the texts' language (i18n)
    async with client.session(model_id) as llm:
        return await describe_with(llm, image, seed)
