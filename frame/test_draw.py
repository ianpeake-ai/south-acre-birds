# test_draw.py - draw today.jpg from the Frame's memory. No wifi, no Pi.
# Run it from Thonny (green Run button). The panel takes ~30-40 s to redraw.
import jpegdec
from picographics import PicoGraphics, DISPLAY_INKY_FRAME_SPECTRA_7

graphics = PicoGraphics(DISPLAY_INKY_FRAME_SPECTRA_7)
j = jpegdec.JPEG(graphics)
j.open_file("today.jpg")
j.decode()
print("decoded - now redrawing the panel (30-40 s)...")
graphics.update()
print("done")
