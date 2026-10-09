# main.py - South Acre bird frame
# Pimoroni Inky Frame 7.3" Spectra (Pico 2 W), Pimoroni MicroPython 1.27
#
# Each wake between 07:00 and 21:00 (every 30 minutes) it joins the wifi,
# sets its clock, fetches today.jpg from the listener Pi, and redraws the
# panel ONLY if the picture has changed - a redraw is the big battery cost.
# Then it powers down until the next wake. Overnight it sleeps through to 07:00.
#
# British Summer Time is worked out from the date: nothing to change in
# March or October.
#
# Needs secrets.py on the board containing WIFI_SSID and WIFI_PASSWORD.
# The picture is kept in the Frame's own memory (as Pimoroni's examples do),
# so the SD card isn't needed.

import gc
import os
import time
import machine
import network
import jpegdec
import inky_frame
from urllib import urequest
from picographics import PicoGraphics, DISPLAY_INKY_FRAME_SPECTRA_7

# ---- settings ---------------------------------------------------------------
IMAGE_URL    = "http://192.168.4.55:8090/today.jpg"   # the Pi's reserved address
WAKE_START   = 7      # first wake of the day (local time)
WAKE_END     = 21     # no wakes from this hour until WAKE_START
REFRESH_MIN  = 30     # minutes between wakes in the day
WIFI_TIMEOUT = 30     # seconds to wait for the wifi
# -----------------------------------------------------------------------------

IMG, TMP, SIG = "today.jpg", "today.tmp", "today.sig"

try:
    from secrets import WIFI_SSID, WIFI_PASSWORD
except ImportError:
    WIFI_SSID = WIFI_PASSWORD = None

try:
    import hashlib
except ImportError:
    hashlib = None


# ---- clock ------------------------------------------------------------------
def _weekday(y, m, d):
    """0 = Sunday ... 6 = Saturday (Sakamoto's method; no clock library needed)."""
    t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def _last_sunday(year, month):
    """Day of the month of the last Sunday in a 31-day month (March, October)."""
    return 31 - _weekday(year, month, 31)


def uk_offset(y, m, d, h):
    """1 during British Summer Time, else 0. The clocks change at 01:00 UTC on
    the last Sunday of March (forward) and of October (back)."""
    if 3 < m < 10:
        return 1
    if m < 3 or m > 10:
        return 0
    ls = _last_sunday(y, m)
    past_change = d > ls or (d == ls and h >= 1)
    return (1 if past_change else 0) if m == 3 else (0 if past_change else 1)


def now_local():
    """(clock_is_set, local hour, local minute) from the Pico's clock (UTC)."""
    y, m, d, _, h, mi, _, _ = machine.RTC().datetime()
    return y >= 2025, (h + uk_offset(y, m, d, h)) % 24, mi


# ---- wifi -------------------------------------------------------------------
def connect_wifi():
    try:
        import rp2
        rp2.country("GB")
    except Exception:
        pass
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t0 = time.time()
        while not wlan.isconnected():
            if time.time() - t0 > WIFI_TIMEOUT:
                print("wifi: no connection")
                return None
            inky_frame.led_wifi.on()
            time.sleep(0.2)
            inky_frame.led_wifi.off()
            time.sleep(0.2)
    inky_frame.led_wifi.on()
    print("wifi:", wlan.ifconfig()[0])
    return wlan


# ---- picture ----------------------------------------------------------------
def fetch(url):
    """Stream the picture into TMP. Returns its fingerprint, or None if what
    arrived isn't a JPEG (an error page, say) - the last picture then stays."""
    gc.collect()
    digest = hashlib.sha256() if hashlib else None
    buf = bytearray(1024)
    mv = memoryview(buf)
    size, head = 0, b""
    s = urequest.urlopen(url)
    try:
        with open(TMP, "wb") as f:
            while True:
                n = s.readinto(buf)
                if not n:
                    break
                if size == 0:
                    head = bytes(buf[:2])
                f.write(mv[:n])
                if digest:
                    digest.update(mv[:n])
                size += n
    finally:
        s.close()
    if head != b"\xff\xd8" or size < 1000:
        print("fetch: not a picture (%d bytes)" % size)
        os.remove(TMP)
        return None
    print("fetch: %d bytes" % size)
    return digest.digest() if digest else str(size).encode()


def last_sig():
    try:
        with open(SIG, "rb") as f:
            return f.read()
    except OSError:
        return None


def draw(path):
    gc.collect()
    graphics = PicoGraphics(DISPLAY_INKY_FRAME_SPECTRA_7)
    j = jpegdec.JPEG(graphics)
    j.open_file(path)
    j.decode()
    graphics.update()          # the slow part: ~30-40 s for a colour e-ink redraw


def update():
    # The first connection straight after joining the wifi sometimes fails on
    # the Pico; try three times, a few seconds apart, before giving up.
    sig = None
    for attempt in range(3):
        try:
            sig = fetch(IMAGE_URL)
            break
        except OSError as e:
            print("fetch attempt %d: %s" % (attempt + 1, e))
            try:
                os.remove(TMP)
            except OSError:
                pass
            time.sleep(3)
    if sig is None:
        return
    if sig == last_sig():
        print("picture unchanged - panel left as it is")
        os.remove(TMP)
        return
    draw(TMP)
    try:
        os.remove(IMG)
    except OSError:
        pass
    os.rename(TMP, IMG)
    with open(SIG, "wb") as f:
        f.write(sig)
    print("panel redrawn")


# ---- sleep ------------------------------------------------------------------
def minutes_to_next_wake():
    clock_ok, h, mi = now_local()
    if not clock_ok or WAKE_START <= h < WAKE_END:
        return REFRESH_MIN
    mins = (WAKE_START * 60 - (h * 60 + mi)) % (24 * 60)
    return mins or REFRESH_MIN


def run():
    try:
        inky_frame.pcf_to_pico_rtc()   # the battery-backed clock kept time while off
    except Exception:
        pass
    clock_ok, h, _ = now_local()
    if WIFI_SSID and WIFI_PASSWORD and (not clock_ok or WAKE_START <= h < WAKE_END):
        wlan = connect_wifi()
        if wlan:
            try:
                inky_frame.set_time()   # internet time -> both clocks
            except Exception as e:
                print("clock:", e)
            try:
                update()
            except Exception as e:
                print("update failed:", e)
            try:
                wlan.disconnect()
                wlan.active(False)
            except Exception:
                pass
    elif not (WIFI_SSID and WIFI_PASSWORD):
        print("no wifi details in secrets.py")
    inky_frame.led_wifi.off()
    mins = minutes_to_next_wake()
    print("sleeping %d min" % mins)
    # On batteries this powers the board off until the clock wakes it.
    # On USB it just waits, then the script ends (unplug/replug or Run to repeat).
    inky_frame.sleep_for(mins)


run()
