#!/usr/bin/env python3
"""
publish_web.py - sends today's birds from the listener Pi to Pete's app.

Runs on the Pi every 15 minutes (birdweb-publish.timer). Each run it:
    1. asks the station which birds have been heard today
    2. builds a small bundle: today.json, a thumbnail of each bird (from the
       house-style illustrations), the best clip of each bird's song, the
       day's collage, and the empty nest
    3. if anything has changed since the last run, pushes the bundle - plus the
       app itself, taken from the repository's main branch - to the
       gh-pages branch as ONE commit, replacing the last. GitHub Pages
       publishes it a minute later.

Nothing at home is opened to the internet: the Pi only ever sends.

Song clips are only published while BirdNET-Pi's privacy filter is on
(PRIVACY_THRESHOLD above 0 in /etc/birdnet/birdnet.conf), so a recording
that caught voices is never put online.

    python3 publish_web.py              # normal run (what the timer does)
    python3 publish_web.py --force      # push even if nothing has changed
    python3 publish_web.py --dry-run    # build the bundle locally, don't push
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(HOME / "birdframe-app"))      # reuse the frame renderer's helpers
import render_frame as rf                              # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFilter   # noqa: E402

# ---- settings ---------------------------------------------------------------
REPO = "git@github-birdweb:ianpeake-ai/south-acre-birds.git"   # ssh alias -> deploy key
APP_BRANCH = "main"            # the app's own files live in site/ on this branch
PAGES_BRANCH = "gh-pages"      # what GitHub Pages publishes
BASE = "http://localhost"
ILLUSTRATIONS = HOME / "BirdNET-Pi/avian/assets/illustrations"
FRAME_OUT = HOME / "birdframe"
NEST = HOME / "birdframe-app/south-acre-nest.png"
DB = HOME / "BirdNET-Pi/scripts/birds.db"
EXTRACTED = HOME / "BirdSongs/Extracted/By_Date"
CONF = Path("/etc/birdnet/birdnet.conf")
WORK = HOME / "birdweb"
STATE = HOME / ".birdweb-state.json"

THUMB_PX = 240                 # longest side of each bird's thumbnail
CLIP_MAX_BYTES = 1_500_000     # skip anything implausibly large

# Our own birds, named as we know them.
RESIDENTS = {
    "gallus-gallus": ("The hens", "heard as Red Junglefowl"),
    "anas-platyrhynchos": ("The ducks", "or a passing Mallard"),
}
# -----------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, flush=True)


def read_conf() -> dict:
    conf = {}
    try:
        for line in CONF.read_text().splitlines():
            m = re.match(r'\s*([A-Z_]+)=(.*)$', line)
            if m:
                conf[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return conf


def privacy_filter_on(conf: dict) -> bool:
    try:
        return float(conf.get("PRIVACY_THRESHOLD", "0") or 0) > 0
    except ValueError:
        return False


def fetch_recent(hours: int = 24) -> dict:
    url = f"{BASE}/avian/api/birdnet-api.php?action=recent&hours={hours}"
    req = urllib.request.Request(url, headers={"User-Agent": "SouthAcre-web/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read(2_000_000))


def db_facts(day: str) -> tuple[dict, set]:
    """First time each bird was heard today, and which birds are first-ever records."""
    first, new = {}, set()
    if not DB.exists():
        return first, new
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        try:
            for sci, t in con.execute(
                    "SELECT Sci_Name, MIN(Time) FROM detections WHERE Date = ? GROUP BY Sci_Name",
                    (day,)):
                first[sci] = str(t)[:5]
            for (sci,) in con.execute(
                    "SELECT Sci_Name FROM detections GROUP BY Sci_Name HAVING MIN(Date) = ?",
                    (day,)):
                new.add(sci)
        finally:
            con.close()
    except sqlite3.Error as e:
        log(f"(database not read: {e})")
    return first, new


def hhmm(stamp) -> str | None:
    m = re.search(r"(\d{2}:\d{2})", str(stamp or ""))
    return m.group(1) if m else None


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:10]


# ---- pictures ---------------------------------------------------------------
def thumbnail(slug: str, cache: Path) -> Path | None:
    out = cache / f"{slug}.webp"
    src = ILLUSTRATIONS / f"{slug}.png"
    if not src.exists():
        return None
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    im = Image.open(src).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if box:
        im = im.crop(box)
    s = THUMB_PX / max(im.size)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    im.save(out, "WEBP", quality=82, method=4)
    return out


def lift_fast(img: Image.Image, work_px: int = 260) -> Image.Image:
    """lift_off_paper for big pictures: the slow 'which paper touches the edge'
    flood runs on a small copy, the soft edge is worked out at full size."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    bg = rf.paper_colour(rgb)
    r, g, b = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).split()
    dist = ImageChops.lighter(ImageChops.lighter(r, g), b)
    t0, t1 = 14, 46
    soft = dist.point(lambda v: 0 if v <= t0 else 255 if v >= t1 else (v - t0) * 255 // (t1 - t0))
    s = work_px / max(w, h)
    small = rgb.resize((max(1, round(w * s)), max(1, round(h * s))), Image.BILINEAR)
    sw, sh = small.size
    sentinel = (255, 0, 255)
    for xy in [(x, y) for x in range(0, sw, max(1, sw // 8)) for y in (0, sh - 1)] + \
              [(x, y) for x in (0, sw - 1) for y in range(0, sh, max(1, sh // 8))]:
        px = small.getpixel(xy)
        if px != sentinel and max(abs(a - c) for a, c in zip(px, bg)) <= t1:
            ImageDraw.floodfill(small, xy, sentinel, thresh=60)
    outside = ImageChops.difference(small, Image.new("RGB", small.size, sentinel)) \
        .convert("L").point(lambda v: 255 if v == 0 else 0).filter(ImageFilter.MaxFilter(3))
    # grow the outside a little so the birds' own edges are decided by colour, not the small copy
    outside = outside.resize((w, h), Image.BILINEAR).point(lambda v: 255 if v > 0 else 0)
    alpha = ImageChops.lighter(soft, ImageChops.invert(outside))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def collage(cache: Path, day: str) -> Path | None:
    src = FRAME_OUT / "today-collage.jpg"
    if not src.exists():
        return None
    if datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d") != day:
        return None                       # yesterday's picture: leave it out
    out = cache / f"collage-{file_hash(src)}.webp"
    if not out.exists():
        for old in cache.glob("collage-*.webp"):
            old.unlink()
        art = lift_fast(Image.open(src))
        box = art.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
        if box:
            art = art.crop(box)
        if max(art.size) > 1400:
            s = 1400 / max(art.size)
            art = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
        art.save(out, "WEBP", quality=80, method=4)
    return out


def nest(cache: Path) -> Path | None:
    out = cache / "nest.webp"
    if not NEST.exists():
        return None
    if not out.exists() or out.stat().st_mtime < NEST.stat().st_mtime:
        art = rf.lift_off_paper(Image.open(NEST))
        if max(art.size) > 900:
            s = 900 / max(art.size)
            art = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
        art.save(out, "WEBP", quality=82, method=4)
    return out


# ---- song clips -------------------------------------------------------------
def find_clip(top_file: str, com: str) -> Path | None:
    if not top_file:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})-birdnet", top_file)
    if not m:
        return None
    day_dir = EXTRACTED / m.group(1)
    folder = com.replace("'", "").replace(" ", "_")
    p = day_dir / folder / top_file
    if p.exists():
        return p
    hits = list(day_dir.glob(f"*/{top_file}"))
    return hits[0] if hits else None


# ---- the bundle -------------------------------------------------------------
def build(out: Path, clips_ok: bool) -> dict:
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    api = fetch_recent()
    species = api.get("species", [])
    first, new = db_facts(day)

    cache = WORK / "cache"
    (cache / "birds").mkdir(parents=True, exist_ok=True)
    data = out / "data"
    (data / "birds").mkdir(parents=True, exist_ok=True)
    (data / "clips").mkdir(parents=True, exist_ok=True)

    rows = []
    for s in species:
        sci, com = s.get("sci", ""), s.get("com", "")
        slug = rf.slugify(sci)
        row = {"com": com, "sci": sci, "slug": slug, "n": int(s.get("n") or 0),
               "first": first.get(sci), "last": hhmm(s.get("last_seen")),
               "first_ever": sci in new, "resident": slug in RESIDENTS,
               "img": None, "clip": None}
        if slug in RESIDENTS:
            row["com"], row["sci"] = RESIDENTS[slug]
        t = thumbnail(slug, cache / "birds")
        if t:
            shutil.copy2(t, data / "birds" / t.name)
            row["img"] = f"data/birds/{t.name}"
        if clips_ok:
            c = find_clip(s.get("top_file", ""), com)
            if c and c.stat().st_size <= CLIP_MAX_BYTES:
                name = f"{day}-{slug}{c.suffix.lower()}"
                shutil.copy2(c, data / "clips" / name)
                row["clip"] = f"data/clips/{name}"
        rows.append(row)
    rows.sort(key=lambda r: (r["first"] or r["last"] or "99:99", r["com"]))

    doc = {
        "date": day,
        "date_label": now.strftime("%A %-d %B"),
        "scope": "today" if api.get("reset_at_midnight") else "last 24 hours",
        "collage": None, "nest": None,
        "species": rows,
    }
    if rows:
        c = collage(cache, day)
        if c:
            shutil.copy2(c, data / c.name)
            doc["collage"] = f"data/{c.name}"
    n = nest(cache)
    if n:
        shutil.copy2(n, data / n.name)
        doc["nest"] = f"data/{n.name}"

    # the signature ignores the clock, so an unchanged day isn't re-pushed
    files = sorted(p for p in data.rglob("*") if p.is_file())
    sig_src = json.dumps(doc, sort_keys=True) + "".join(f"{p.name}:{file_hash(p)}" for p in files)
    doc["updated_label"] = now.strftime("%H:%M")
    (data / "today.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    return {"sig": hashlib.sha256(sig_src.encode()).hexdigest()[:16], "doc": doc}


def git(*args, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True, timeout=180)


def app_files(dest: Path) -> None:
    """Copy the app (site/ on the main branch) into the bundle."""
    src = WORK / "src"
    if not (src / ".git").exists():
        if src.exists():
            shutil.rmtree(src)
        git("clone", "--depth", "1", "--branch", APP_BRANCH, REPO, str(src), cwd=WORK)
    else:
        git("fetch", "--depth", "1", "origin", APP_BRANCH, cwd=src)
        git("reset", "--hard", f"origin/{APP_BRANCH}", cwd=src)
    site = src / "site"
    if not site.is_dir():
        raise RuntimeError("no site/ folder on the main branch")
    for p in site.iterdir():
        target = dest / p.name
        if p.is_dir():
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            shutil.copy2(p, target)


def push(bundle: Path, label: str) -> None:
    git("init", "-q", "-b", PAGES_BRANCH, cwd=bundle)
    git("config", "user.name", "South Acre ear", cwd=bundle)
    git("config", "user.email", "south-acre-ear@users.noreply.github.com", cwd=bundle)
    git("add", "-A", cwd=bundle)
    git("commit", "-q", "-m", label, cwd=bundle)
    r = git("push", "-q", "--force", REPO, f"{PAGES_BRANCH}:{PAGES_BRANCH}", cwd=bundle, check=False)
    if r.returncode != 0:
        raise RuntimeError("push failed: " + (r.stderr or r.stdout).strip()[-400:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="push even if nothing has changed")
    ap.add_argument("--dry-run", action="store_true", help="build into ~/birdweb/preview, don't push")
    ap.add_argument("--clips-without-privacy-filter", action="store_true",
                    help="publish song clips even with the privacy filter off (not advised)")
    args = ap.parse_args()

    WORK.mkdir(exist_ok=True)
    conf = read_conf()
    clips_ok = privacy_filter_on(conf) or args.clips_without_privacy_filter
    if not clips_ok:
        log("song clips left out: the station's privacy filter is off")

    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=WORK) as tmp:
        bundle = Path(tmp) / "site"
        bundle.mkdir()
        try:
            built = build(bundle, clips_ok)
        except Exception as e:
            log(f"error: could not build today's bundle: {e}")
            return 1
        doc = built["doc"]
        prev = ""
        if STATE.exists():
            try:
                prev = json.loads(STATE.read_text()).get("sig", "")
            except Exception:
                pass
        if built["sig"] == prev and not (args.force or args.dry_run):
            log(f"no change ({len(doc['species'])} birds) - app already current")
            return 0
        try:
            app_files(bundle)
        except Exception as e:
            log(f"error: could not fetch the app from GitHub: {e}")
            return 1
        if args.dry_run:
            dest = WORK / "preview"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(bundle, dest)
            log(f"dry run: {len(doc['species'])} birds -> {dest}")
            return 0
        try:
            push(bundle, f"Birds of {doc['date']} at {doc['updated_label']}")
        except Exception as e:
            log(f"error: {e}")
            return 1
    STATE.write_text(json.dumps({"sig": built["sig"], "when": time.time(),
                                 "birds": len(doc["species"])}))
    log(f"published {len(doc['species'])} birds in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
