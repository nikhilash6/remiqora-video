"""MiniMax H3's licence statement: the licence does not apply in the US, the EU, the UK and South Korea. H3 stays installable
everywhere, but only after the user accepts the statement (the installer's tick, the script's y/N, the Models page's tick,
the cloud panel's tick). Every front end sends the flag; the server refuses H3 without it."""
from __future__ import annotations

from fastapi import HTTPException

from . import i18n

URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3"
SETS = ("h3", "ref2va")               # install/manifest.json sets under the H3 licence
REPOS = ("Comfy-Org/MiniMax-H3", "MiniMaxAI/MiniMax-H3")


def notice() -> str:
    return i18n.L("Лицензия MiniMax H3 не действует в США, ЕС, Великобритании и Южной Корее. Устанавливая H3, вы сами отвечаете за соблюдение её условий; автор Remiqora Video ответственности не несёт.",
                  "The MiniMax H3 license does not apply in the United States, the EU, the United Kingdom and South Korea. By installing H3 you are responsible for complying with its terms; the author of Remiqora Video bears no responsibility.")


def refusal() -> str:
    return i18n.L("Сначала прочитайте и примите лицензию MiniMax H3. ", "Read and accept the MiniMax H3 license first. ") + notice()


def is_h3_repo(repo: str) -> bool:
    return repo in REPOS


def require(sets: list[str], accepted: bool) -> None:
    """400 when the sets include H3's and the statement was not accepted."""
    if not accepted and any(s in SETS for s in sets):
        raise HTTPException(400, refusal())
