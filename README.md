# The Birds of South Acre

A wireless garden bird register. A microphone under the eaves listens all day; a
Raspberry Pi identifies each singer with BirdNET; a battery e-ink frame on the
wall fills with woodblock illustrations of every bird heard; and this app shows
the same day on a phone, anywhere, with each bird's song.

## How the pieces fit

| Where | File | Job |
|---|---|---|
| Pi | `pi/render_frame.py` | photographs the station's collage every 15 min; writes the frame's picture and a full-size collage for the app |
| Pi | `pi/install_frame_server.sh` | the render timer, and the image server on :8090 the frame fetches from |
| Pi | `pi/publish_web.py` | every 15 min, if anything changed, sends today's birds to the `gh-pages` branch |
| Pi | `pi/install_birdweb.sh` | sets up the deploy key and the publishing timer |
| Frame | `frame/main.py` | wakes 07:00–21:00 every 30 min, fetches the picture, redraws only on change |
| Frame | `frame/test_draw.py` | draws `today.jpg` from the frame's memory, no wifi needed |
| GitHub Pages | `site/` | the app itself (installable on a phone's home screen, works offline) |

The Pi only ever sends: nothing at home is opened to the internet. `gh-pages`
always holds a single commit (each publish replaces the last), so the repository
stays small however long it runs.

## Privacy

This repository and the app are public. Song clips are published only while
BirdNET-Pi's privacy filter is on (`PRIVACY_THRESHOLD` above 0), which discards
any recording where it hears a human voice. No coordinates are published.

## Setting up the Pi (once)

From the laptop, copy `pi/publish_web.py` and `pi/install_birdweb.sh` to the Pi,
and replace `~/birdframe-app/render_frame.py` with `pi/render_frame.py`. Then on
the Pi:

```
bash install_birdweb.sh      # prints a deploy key: add it on GitHub (write access)
bash install_birdweb.sh      # second run: installs the timer, makes the first publish
```

Then **Settings → Pages → Deploy from a branch → `gh-pages` / (root)**. The app is
at `https://ianpeake-ai.github.io/south-acre-birds/`.

A custom address (e.g. `birds.example.com`): put the name in `site/CNAME`, and
add a CNAME record for it pointing at `ianpeake-ai.github.io`.

## Changing the app

Edit `site/` on `main`. The Pi picks up the new version on its next publish; to
see it straight away, run `python3 ~/birdframe-app/publish_web.py --force` on the Pi.
