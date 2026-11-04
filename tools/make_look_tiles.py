r"""Regenerates the shipped «Вид» tile pictures (app/looks_tiles/<id>.webp) from the 2026-11-03 look probes.

Every offered look takes its MEDIUM frame from the LTX 2.5 probes; the H3-only look (papercut) takes the H3 CLOSE
frame; a look drawn by every frame (looks.FRAME_LOOKS) the middle frame of its route-A shot (a Krea 2 still in the
look, then LTX 2.5 from it: <routes>/<id>_A.jpg). The 16:9 frame is cropped to the tile's 16:10 (frontend/src/styles/looks.css .lp-pic) and
scaled to 320 px wide.

  engine\.venv\Scripts\python.exe tools\make_look_tiles.py [--ltx DIR] [--h3 DIR] [--routes DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import looks  # noqa: E402

OUT = ROOT / "app" / "looks_tiles"
W, H = 320, 200   # 16:10, as .lp-pic
LIMIT = 30 * 1024
# looks whose LTX 2.5 medium frame came out plain (a rooftop at dusk): the H3 medium frame draws the place much better
H3_MEDIUM = {"space", "cyberpunk", "fairy_forest", "night_neon"}


def find(base: Path, lid: str, framing: str) -> Path:
    hits = sorted(base.glob(f"*/{lid}_{framing}.jpg"))
    if not hits:
        raise SystemExit(f"no frame {lid}_{framing}.jpg under {base}")
    return hits[0]


def tile(src: Path, dst: Path) -> int:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    ch = min(h, round(w * H / W))   # crop to 16:10 around the centre
    cw = round(ch * W / H)
    x, y = (w - cw) // 2, (h - ch) // 2
    im = im.crop((x, y, x + cw, y + ch)).resize((W, H), Image.LANCZOS)
    for q in (90, 85, 80, 72, 65):
        im.save(dst, "WEBP", quality=q, method=6)
        if dst.stat().st_size <= LIMIT:
            break
    return dst.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ltx", required=True)
    ap.add_argument("--h3", required=True)
    ap.add_argument("--routes", required=True)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    total = 0
    for x in looks.offered():
        lid = x["id"]
        if lid in looks.FRAME_LOOKS:
            src = Path(a.routes) / f"{lid}_A.jpg"
            if not src.exists():
                raise SystemExit(f"no frame {src}")
        elif lid in looks.H3_ONLY:
            src = find(Path(a.h3), lid, "close")
        elif lid in H3_MEDIUM:
            src = find(Path(a.h3), lid, "medium")
        else:
            src = find(Path(a.ltx), lid, "medium")
        n = tile(src, OUT / f"{lid}.webp")
        total += n
        print(f"{lid:14s} {n / 1024:5.1f} KB  <- {src}")
    print(f"total {total / 1024:.0f} KB")


if __name__ == "__main__":
    main()
