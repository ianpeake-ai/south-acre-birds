#!/usr/bin/env python3
"""
shoot_checked.py - the station's own shoot.py, with a head count.

The maker's shoot.py waits until the collage says it is ready, then takes
the photograph. On the Pi that is not always enough: on 3 November one photo
in three caught 10 of 27 birds, though the page swore it was ready. This
wrapper runs the maker's shoot.py unchanged, and checks every photo before
it is accepted:

    - every bird the station heard, and has a drawing for, must have a tile
      on the page (--expect gives the list the renderer was told)
    - every tile must actually be painted in the photo: the middle of its box
      must hold ink, not bare paper

A photo that fails is retaken (after a pause for the browser to finish
painting), and if that doesn't cure it the whole page is reloaded, up to
--tries times. The best photo is kept either way, and a short report is
written to --report so render_frame.py can decide whether to use it.

    exit 0   every expected bird is in the photo
    exit 3   a photo was taken but birds are missing (best attempt kept)
    exit 1   no photo at all

Every other option is passed straight to the maker's shoot.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import time

from PIL import Image, ImageChops

from playwright.sync_api import Page

INK_MIN = 0.03        # share of a tile's middle that must differ from bare paper
INK_STEP = 24         # how far from the paper colour a pixel must be to count (0-255)
SETTLE_RETAKES = 3    # retakes in the same page before reloading it
TILES_JS = """async () => {
  const c = document.getElementById('collage');
  const tiles = c ? [...c.querySelectorAll('.gtile')].map(t => {
    const r = t.getBoundingClientRect();
    return {sci: t.getAttribute('data-sci') || '', x: r.left, y: r.top, w: r.width, h: r.height};
  }) : [];
  let maskKeys = null;
  try {
    maskKeys = Object.keys(await (await fetch('./masks.json', {cache: 'force-cache'})).json());
  } catch (e) {}
  return {tiles: tiles, maskKeys: maskKeys, vw: innerWidth, vh: innerHeight};
}"""


def slugify(sci: str) -> str:            # the collage page's own rule
    return re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("maker_shoot", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec.loader.exec_module(mod)
    return mod


def unpainted(png: bytes, info: dict) -> list[str]:
    """Tiles whose middle is bare paper in the photo."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    sx = img.width / max(1, info["vw"])
    sy = img.height / max(1, info["vh"])
    corners = [img.getpixel((x, y)) for x in (2, img.width - 3) for y in (2, img.height - 3)]
    paper = tuple(sorted(c[i] for c in corners)[len(corners) // 2] for i in range(3))
    bad = []
    for t in info["tiles"]:
        if t["w"] <= 0 or t["h"] <= 0:
            continue
        if t["x"] + t["w"] <= 0 or t["y"] + t["h"] <= 0 or t["x"] >= info["vw"] or t["y"] >= info["vh"]:
            bad.append(t["sci"])                # parked off the page
            continue
        # the middle half of the box: the bird's body, clear of neighbours
        x0 = max(0, int((t["x"] + t["w"] * 0.25) * sx)); x1 = min(img.width, int((t["x"] + t["w"] * 0.75) * sx))
        y0 = max(0, int((t["y"] + t["h"] * 0.25) * sy)); y1 = min(img.height, int((t["y"] + t["h"] * 0.75) * sy))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        box = img.crop((x0, y0, x1, y1))
        r, g, b = ImageChops.difference(box, Image.new("RGB", box.size, paper)).split()
        diff = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > INK_STEP else 0)
        ink = diff.histogram()[255] / (box.width * box.height)
        if ink < INK_MIN:
            bad.append(t["sci"])
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--maker-shoot", required=True, help="the station's frame/shoot.py")
    ap.add_argument("--expect", help="JSON list of the scientific names the station reported")
    ap.add_argument("--report", help="where to write the head count (JSON)")
    ap.add_argument("--tries", type=int, default=3, help="page loads before giving up")
    ours, rest = ap.parse_known_args()
    out = rest[rest.index("--out") + 1] if "--out" in rest else "frame.png"
    expect = json.load(open(ours.expect)) if ours.expect else None

    maker = load_module(ours.maker_shoot)
    seen: dict = {}
    real_screenshot = Page.screenshot

    def counted_screenshot(self, *a, **k):
        info = self.evaluate(TILES_JS)
        png, bad = None, []
        for retake in range(SETTLE_RETAKES + 1):
            if retake:
                self.wait_for_timeout(1500 * retake)    # let the browser finish painting
            png = real_screenshot(self, *a, **k)
            bad = unpainted(png, info)
            if not bad:
                break
        seen.update(info=info, unpainted=bad, retakes=retake)
        return png

    Page.screenshot = counted_screenshot

    best = None
    attempts = []
    for attempt in range(1, ours.tries + 1):
        seen.clear()
        sys.argv = [ours.maker_shoot] + rest
        try:
            maker.main()
            ok = True
        except SystemExit as e:
            ok = e.code in (None, 0)
        if not ok or "info" not in seen or not os.path.exists(out):
            attempts.append({"attempt": attempt, "error": "the photographer failed"})
            continue
        info = seen["info"]
        tiles = [t["sci"] for t in info["tiles"]]
        keys = set(info["maskKeys"] or [])
        wanted = expect if expect is not None else tiles
        if keys:
            # a bird can only be drawn if the station has its silhouette
            drawable = [s for s in wanted if slugify(s) in keys]
            drawable += [s for s in tiles if s not in drawable]   # page knew of more: fine
            no_art = [s for s in wanted if slugify(s) not in keys]
        else:
            drawable, no_art = list(tiles), []   # can't tell: just check the painting
        drawn = [s for s in tiles if s not in seen["unpainted"]]
        missing = [s for s in drawable if s not in drawn]
        result = {"attempt": attempt, "expected": len(drawable), "drawn": len(drawn),
                  "missing": missing, "no_art": no_art, "retakes": seen["retakes"]}
        attempts.append(result)
        if best is None or result["drawn"] > best["drawn"]:
            best = result
            shutil.copyfile(out, out + ".best")
        if not missing:
            break
        time.sleep(2)

    report = {"best": best, "attempts": attempts}
    if ours.report:
        with open(ours.report, "w") as f:
            json.dump(report, f, indent=1)
    if best is None:
        print("no photo: " + "; ".join(a.get("error", "") for a in attempts), file=sys.stderr)
        return 1
    os.replace(out + ".best", out)
    note = f"drew {best['drawn']} of {best['expected']} birds"
    if best["no_art"]:
        note += f" (no drawing yet: {', '.join(best['no_art'])})"
    if best["missing"]:
        print(f"{note}; missing {', '.join(best['missing'])} after {len(attempts)} tries", file=sys.stderr)
        return 3
    print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
