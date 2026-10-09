#!/usr/bin/env python3
"""
render_frame.py - South Acre wireless-frame renderer (runs on the outdoor Pi).

Version 2: the maker's own collage, framed for a portrait 7.3" Inky Frame.

Each run it:
    1. asks BirdNET-Pi's recent API which birds have sung (the same endpoint
       the collage page uses), and stops unless the set has changed and it is
       outside quiet hours - so the panel is never redrawn for nothing
    2. photographs the station's live collage page with the maker's shoot.py
       (a headless browser), so the birds are packed exactly as on the website
       and rearranged afresh each time
    3. crops the collage out of that screenshot, draws the title above it in
       three lines (TITLE_LINES, below), and fits both to a 480x800 portrait
       page on the page's own paper colour
    4. writes, atomically:
         today.jpg            800x480, turned on its side for the panel
         today-portrait.jpg   the same page upright, for checking on a phone
         today-preview.png    upright and dithered to the six inks: roughly
                              what the e-ink will actually show

The browser runs at low priority and volunteers itself to the out-of-memory
killer first, so if memory ever runs short it is the screenshot that dies,
not BirdNET.

install_frame_server.sh runs this every 15 minutes and serves the folder on
port 8090; the Frame fetches today.jpg.

    python3 render_frame.py --force          # make one now, whatever the hour
    python3 render_frame.py --rotate 90      # if the frame hangs the other way
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# --- panel -----------------------------------------------------------------
PAGE_W, PAGE_H = 480, 800      # the Inky Frame 7.3" hung portrait
MARGIN_X = 20                  # left/right paper margin on the page
MARGIN_TOP, MARGIN_BOTTOM = 26, 24
HEADER_GAP = 18                # paper between title block and collage

# The title is drawn here rather than by the web page, so it can sit on three
# lines. Edit freely: each line is (text, style); styles are defined below.
TITLE_LINES = [("The Birds", "big"), ("of", "small"), ("South Acre", "big")]

INK = (26, 26, 28)             # the panel's black ink
RED = (165, 60, 56)            # the panel's red ink - echoes a woodblock seal
TITLE_STYLES = {               # font, size in page pixels, colour, space above
    "eyebrow": ("italic", 21, INK, 0),    # the date line
    "big":     ("bold",   60, INK, 4),
    "small":   ("italic", 30, RED, 0),
}
FONT_FILES = {                 # first one found wins
    "bold":   ["/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
               "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
               "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"],
    "italic": ["/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
               "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf",
               "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"],
}

# Spectra-6 inks (from the maker's display.py), used only for the preview.
SPECTRA6 = [(236, 234, 223), (26, 26, 28), (165, 60, 56),
            (198, 176, 74), (49, 71, 130), (58, 110, 72)]

# Older BirdNET models use superseded genus names; keeps the change-detection
# signature stable whichever name the station reports.
ALIASES = {"sylvia-communis": "curruca-communis",
           "carduelis-cannabina": "linaria-cannabina",
           "coloeus-monedula": "corvus-monedula"}


def slugify(sci: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")
    return ALIASES.get(slug, slug)


def bucket(n: int) -> int:
    for i, edge in enumerate((1, 2, 5, 15, 40, 100, 300, 1000)):
        if n <= edge:
            return i
    return 8


def fetch_recent(base: str, hours: int, timeout: int) -> list[dict]:
    url = f"{base.rstrip('/')}/avian/api/birdnet-api.php?action=recent&hours={hours}"
    req = urllib.request.Request(url, headers={"User-Agent": "SouthAcre-frame/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000)).get("species", [])


def signature(species: list[dict], day: str) -> str:
    # the date is part of it, so the page turns over at midnight even on a quiet day
    items = sorted((slugify(s["sci"]), bucket(int(s.get("n") or 1))) for s in species)
    return hashlib.sha256(json.dumps([day, items]).encode()).hexdigest()[:16]


# --- the camera -------------------------------------------------------------
def _volunteer_for_oom() -> None:
    """Runs in the browser's process before it starts: if memory runs out,
    the kernel kills the highest score first. Make that us, not BirdNET."""
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("900")
    except OSError:
        pass


def shoot(args, eyebrow: str, headline: str, out_png: Path) -> None:
    cmd = ["nice", "-n", "10", str(args.shoot_python), str(args.shoot_script),
           "--url", args.base,
           "--width", str(args.shot_width), "--height", str(args.shot_height),
           "--dsf", str(args.dsf), "--collage-vh", str(args.collage_vh),
           # shoot.py draws --title as the small line and --subtitle as the headline
           "--title", eyebrow, "--subtitle", headline,
           # photograph the same window of hours the API was asked about
           "--window-hours", str(args.hours),
           "--out", str(out_png)]
    if args.headline_px:
        cmd += ["--headline-px", str(args.headline_px)]
    if args.lowercase:
        cmd += ["--lowercase"]
    if args.bird_names:
        cmd += ["--bird-names"]          # the collage's own name labels
    r = subprocess.run(cmd, cwd=str(args.shoot_script.parent), timeout=args.shoot_timeout,
                       preexec_fn=_volunteer_for_oom, capture_output=True, text=True)
    if r.returncode != 0 or not out_png.exists():
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError("shoot.py failed:\n    " + "\n    ".join(tail))


# --- cropping the screenshot ------------------------------------------------
def paper_colour(img: Image.Image) -> tuple[int, int, int]:
    """The page's background, sampled just inside the four corners."""
    w, h = img.size
    pts = [img.getpixel((x, y)) for x in (4, w - 5) for y in (4, h - 5)]
    return tuple(sorted(c[i] for c in pts)[len(pts) // 2] for i in range(3))


def ink_mask(img: Image.Image, paper, threshold: int = 22) -> Image.Image:
    """White wherever the page differs noticeably from bare paper."""
    diff = ImageChops.difference(img, Image.new("RGB", img.size, paper)).convert("L")
    return diff.point(lambda v: 255 if v > threshold else 0)


def content_rows(mask: Image.Image) -> list[bool]:
    w, h = mask.size
    data = mask.tobytes()
    return [bool(data[y * w:(y + 1) * w].strip(b"\x00")) for y in range(h)]


def split_page(img: Image.Image, paper, dsf: int):
    """Return (header_box, collage_box) as crop boxes, either may be None.

    The title sits at the top, then a wide band of empty page, then the
    collage. The first gap of at least 60 page-pixels separates them; gaps
    inside a packed collage are far smaller.
    """
    mask = ink_mask(img, paper)
    rows = content_rows(mask)
    ys = [y for y, on in enumerate(rows) if on]
    if not ys:
        return None, None
    first, last = ys[0], ys[-1]
    min_gap = 60 * dsf
    split_at = None
    run_start = None
    for y in range(first, last + 1):
        if not rows[y]:
            if run_start is None:
                run_start = y
        else:
            if run_start is not None and y - run_start >= min_gap:
                split_at = (run_start, y)
                break
            run_start = None

    def bbox(y0, y1):
        b = mask.crop((0, y0, img.width, y1)).getbbox()
        return None if b is None else (b[0], y0 + b[1], b[2], y0 + b[3])

    if split_at is None:                       # no title found: all collage
        return None, bbox(first, last + 1)
    return bbox(first, split_at[0]), bbox(split_at[1], last + 1)


def _fit(im: Image.Image, max_w: int, max_h: int) -> Image.Image:
    s = min(max_w / im.width, max_h / im.height)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))),
                     Image.LANCZOS)


def _font(kind: str, size: int):
    for f in FONT_FILES[kind]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def draw_title(page: Image.Image, eyebrow: str, top: int) -> int:
    """Draw the date line and TITLE_LINES centred from `top`; return the y
    just below them. Any line too wide for the page is shrunk to fit."""
    d = ImageDraw.Draw(page)
    max_w = PAGE_W - 2 * MARGIN_X
    y = top
    for text, style in [(eyebrow, "eyebrow")] + list(TITLE_LINES):
        kind, size, colour, space = TITLE_STYLES[style]
        font = _font(kind, size)
        while d.textlength(text, font=font) > max_w and size > 10:
            size -= 2
            font = _font(kind, size)
        x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
        y += space
        d.text(((PAGE_W - (x1 - x0)) // 2 - x0, y - y0), text, font=font, fill=colour)
        y += (y1 - y0) + max(6, size // 6)
    return y


def lift_off_paper(img: Image.Image) -> Image.Image:
    """If a picture came on a solid paper background (as the nest does), make
    that paper transparent so the picture sits straight on the page. Only paper
    connected to the edges is removed, so pale patches inside the picture stay;
    the edge is softened so straw and twigs don't look cut with scissors.
    Pictures that already have transparency are returned untouched."""
    img = img.convert("RGBA")
    if img.getextrema()[3][0] < 250:
        return img
    if img.width > 20 and img.height > 20:     # shave the outermost pixels,
        img = img.crop((2, 2, img.width - 2, img.height - 2))   # where edges fringe
    rgb = img.convert("RGB")
    w, h = rgb.size
    bg = paper_colour(rgb)
    # how far each pixel is from the paper colour, softened into an alpha ramp
    r, g, b = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).split()
    dist = ImageChops.lighter(ImageChops.lighter(r, g), b)
    t0, t1 = 14, 46
    soft = dist.point(lambda v: 0 if v <= t0 else 255 if v >= t1
                      else (v - t0) * 255 // (t1 - t0))
    # which paper is connected to the edges (flood fill from round the border)
    sentinel = (255, 0, 255)
    flood = rgb.copy()
    seeds = [(x, y) for x in (0, w // 4, w // 2, 3 * w // 4, w - 1) for y in (0, h - 1)]
    seeds += [(x, y) for x in (0, w - 1) for y in (h // 4, h // 2, 3 * h // 4)]
    for xy in seeds:
        px = flood.getpixel(xy)
        if px != sentinel and max(abs(a - c) for a, c in zip(px, bg)) <= t1:
            ImageDraw.floodfill(flood, xy, sentinel, thresh=90)
    outside = ImageChops.difference(flood, Image.new("RGB", flood.size, sentinel)) \
        .convert("L").point(lambda v: 255 if v == 0 else 0).filter(ImageFilter.MaxFilter(5))
    alpha = ImageChops.lighter(soft, ImageChops.invert(outside))
    img.putalpha(alpha)
    return img


def compose(shot: Image.Image, dsf: int, nest: Image.Image | None,
            empty: bool, eyebrow: str = "") -> Image.Image:
    shot = shot.convert("RGB")
    paper = paper_colour(shot)
    header_box, collage_box = split_page(shot, paper, dsf)
    page = Image.new("RGB", (PAGE_W, PAGE_H), paper)
    inner_w = PAGE_W - 2 * MARGIN_X
    y = MARGIN_TOP

    # the page's own title (header_box) is cut away; ours is drawn instead
    y = draw_title(page, eyebrow, y) + HEADER_GAP

    box_h = PAGE_H - MARGIN_BOTTOM - y
    if empty and nest is not None:             # a quiet day: the house-style nest
        art = lift_off_paper(_fit(nest, int(inner_w * 0.86), int(box_h * 0.8)))
        page.paste(art, ((PAGE_W - art.width) // 2, y + (box_h - art.height) // 2), art)
    elif collage_box:
        art = _fit(shot.crop(collage_box), inner_w, box_h)
        page.paste(art, ((PAGE_W - art.width) // 2, y + (box_h - art.height) // 2))
    return page


def quantize_spectra6(img: Image.Image) -> Image.Image:
    """Floyd-Steinberg dither to the six inks, as in the maker's display.py."""
    pal = Image.new("P", (1, 1))
    flat = [c for rgb in SPECTRA6 for c in rgb]
    flat += list(SPECTRA6[0]) * ((768 - len(flat)) // 3)
    pal.putpalette(flat[:768])
    return img.convert("RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")


def save_atomic(img: Image.Image, path: Path, **kw) -> None:
    """Write beside the target, then swap it in, so the Frame can never fetch
    a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=path.suffix)
    os.close(fd)
    try:
        img.save(tmp, **kw)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load_nest(path: Path) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGBA") if path.exists() else None
    except Exception:
        return None


def main() -> int:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=home / "birdframe",
                    help="Folder served on :8090 (install_frame_server.sh)")
    ap.add_argument("--base", default="http://localhost",
                    help="The station's own web address (default: this Pi)")
    ap.add_argument("--title", default="the birds of South Acre",
                    help="Headline for the web page while it is photographed (the frame "
                         "draws its own, from TITLE_LINES)")
    ap.add_argument("--subtitle", default=None,
                    help="The small line above it (default: today's date)")
    ap.add_argument("--lowercase", action="store_true", help="Headline in lower case")
    ap.add_argument("--bird-names", action="store_true",
                    help="Photograph the collage with its name labels showing")
    ap.add_argument("--headline-px", type=int, default=32,
                    help="Headline size on the collage page; 32 keeps it to one line")
    ap.add_argument("--rotate", type=int, choices=(90, 270), default=270,
                    help="Which way the portrait page is turned for the panel")
    ap.add_argument("--nest", type=Path, default=home / "birdframe-app" / "south-acre-nest.png",
                    help="House-style nest for a day with no birds yet")
    ap.add_argument("--hours", type=int, default=24,
                    help="How many hours of birds to show (e.g. --hours 96 to test with past days)")
    ap.add_argument("--timeout", type=int, default=45, help="API timeout, seconds")
    ap.add_argument("--quiet-start", type=int, default=22, help="Quiet-hours start (local hour)")
    ap.add_argument("--quiet-end", type=int, default=6, help="Quiet-hours end (local hour)")
    ap.add_argument("--force", action="store_true", help="Render even if unchanged / quiet hours")
    ap.add_argument("--state", type=Path, default=home / ".birdframe-state.json")
    # the camera
    ap.add_argument("--shoot-python", type=Path, default=home / "shootenv" / "bin" / "python")
    ap.add_argument("--shoot-script", type=Path, default=home / "BirdNET-Pi" / "frame" / "shoot.py")
    ap.add_argument("--shot-width", type=int, default=600)
    ap.add_argument("--shot-height", type=int, default=1000)
    ap.add_argument("--dsf", type=int, default=2,
                    help="Screenshot sharpness (2 = twice the pixels, shrunk down cleanly)")
    ap.add_argument("--collage-vh", type=float, default=75,
                    help="Collage height as %% of the page; shapes it as a tall oval")
    ap.add_argument("--shoot-timeout", type=int, default=300, help="Seconds before giving up")
    args = ap.parse_args()

    now = datetime.now()
    hour = now.hour
    qs, qe = args.quiet_start, args.quiet_end
    quiet = (qs <= hour or hour < qe) if qs > qe else (qs <= hour < qe)
    if quiet and not args.force:
        print(f"quiet hours ({hour:02d}:00) - skipping")
        return 0

    try:
        species = fetch_recent(args.base, args.hours, args.timeout)
    except Exception as e:
        print(f"error: could not reach the station's recent API: {e}", file=sys.stderr)
        return 1

    day = now.strftime("%Y-%m-%d")
    sig = signature(species, day)
    prev = ""
    if args.state.exists():
        try:
            prev = json.loads(args.state.read_text()).get("sig", "")
        except Exception:
            pass
    if sig == prev and not args.force:
        print(f"no change ({len(species)} species) - panel already current")
        return 0

    eyebrow = args.subtitle or now.strftime("%A %-d %B")
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        shot_path = Path(tmp) / "shot.png"
        try:
            shoot(args, eyebrow, args.title, shot_path)
        except Exception as e:
            # leave yesterday's good picture in place; the timer retries
            print(f"error: {e}", file=sys.stderr)
            return 1
        shot = Image.open(shot_path)
        shot.load()

    page = compose(shot, args.dsf, load_nest(args.nest), empty=not species,
                   eyebrow=eyebrow)
    panel = page.rotate(args.rotate, expand=True)          # 800x480 for the Frame
    save_atomic(panel, args.out / "today.jpg", format="JPEG", quality=90)
    save_atomic(page, args.out / "today-portrait.jpg", format="JPEG", quality=90)
    save_atomic(quantize_spectra6(page), args.out / "today-preview.png",
                format="PNG", optimize=True)
    # full-size collage on its own, for Pete's app (publish_web.py picks it up)
    collage_path = args.out / "today-collage.jpg"
    rgb = shot.convert("RGB")
    cbox = split_page(rgb, paper_colour(rgb), args.dsf)[1] if species else None
    if cbox:
        save_atomic(rgb.crop(cbox), collage_path, format="JPEG", quality=88)
    elif collage_path.exists():
        collage_path.unlink()
    args.state.write_text(json.dumps({"sig": sig, "when": time.time(),
                                      "species": len(species)}))
    print(f"rendered {len(species)} species in {time.time() - t0:.0f}s -> "
          f"{args.out / 'today.jpg'} (+ today-portrait.jpg, today-preview.png)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
