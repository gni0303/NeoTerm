"""NeoTerm 1.0 - pixel-art cyberpunk serial terminal for Windows (serial ports, PX4 nsh, PowerShell).

Starts like PuTTY: a session configuration window (serial port, baud, data bits, parity, stop
bits, flow control, terminal options). OPEN connects and switches to the terminal window.

Neon green (fluorescent, glowing) is used for the frame and a few labels only; the rest is
steel / white / cyan / amber. Background: faint Matrix rain of random ASCII letters and digits.
A thin top toolbar shows the port, baud + frame format, the device behind the port, link state
and an RX/TX rate graph.

Terminal keys
  F2 configuration  F3 new session window (also Ctrl+Shift+N)  F4 display settings (UI scale, text)  F5 connect/disconnect  F6 TEXT/HEX view  F7 clear  F9 log to file
  F11 fullscreen  F1 help  Ctrl+= / Ctrl+- UI scale  Ctrl+C copy selection (else ^C), Ctrl+V paste
  mouse drag select, right click copy/paste, wheel / Shift+PgUp/PgDn scrollback
Configuration keys
  Tab next section, Up/Down select, Left/Right change, digits type a custom baud,
  R refresh ports, Enter OPEN, M MAVLink Console(nsh), P PowerShell, F11 fullscreen, Esc CANCEL
  PowerShell: P or the icon next to SESSION CONFIGURATION; while `claude` (orange on dark grey) or
  `codex` (violet) runs in it the UI takes that tool's colours, back to green when it exits or on F2

MAVLink Console(nsh): like QGroundControl's MAVLink Console. Connect the PX4 flight controller
by USB (or a MAVLink telemetry port), press the MAVLink Console(nsh) button and the nsh prompt
appears; keystrokes / output travel inside MAVLink SERIAL_CONTROL messages.
"""
import codecs
import ctypes
import json
import math
import os
import queue
import random
import re
import string
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ["SDL_IME_SHOW_UI"] = "1"
os.environ.setdefault("SDL_VIDEO_ALLOW_SCREENSAVER", "1")
os.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")   # fullscreen: Alt+Tab keeps it open
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pygame
import pyte
import serial
import serial.tools.list_ports
from wcwidth import wcwidth

APP = "NeoTerm"
VERSION = "1.0"
OLD_APP = "PixelTerm"             # earlier name: settings are carried over once
FPS = 24                         # idle animation rate (rain, glow pulse, graph)
BG_FPS = 10                      # animation rate while another window has the focus
HIDDEN_WAKE = 0.1                # minimized: nothing is drawn, the link is still serviced
MIN_FRAME = 1 / 120              # input / received data redraw immediately, capped at 120 fps
STREAM_FRAME = 1 / 30            # while data keeps streaming in, redraw at most 30 fps
STREAMING = 0.05                 # new data within 50 ms of the last data frame = a stream
GLOW_HZ = 12                     # neon halo (blur) rebuild rate
BREATH_S = 4.0                   # glow breathing period (s): halo slowly swells and fades
BREATH_MIN = 0.45                # halo strength at the bottom of a breath (1.0 at the top)
TEXT_GLOW = 120                   # neon text halo strength (255 = as strong as the frame lines)
KEY_REPEAT = (350, 30)           # hold delay, repeat interval (ms)
LOOPBACK = "loop://"              # pyserial loopback URL (not listed; tests open it)
SHELL_PORT = "shell://powershell"   # local Windows PowerShell through a ConPTY pseudo console

# ------------------------------------------------------------------ colours
BG = (3, 6, 8)
PANEL = (8, 12, 17)
TERM_BG = (4, 8, 10, 190)              # lets the matrix rain show faintly behind the text
NEON = (57, 255, 20)                   # fluorescent green (glow layer)
NEON_CORE = (160, 235, 145)            # hot core of neon lines / text
NEON_DIM = (22, 110, 32)
STEEL = (110, 130, 150)
STEEL_DIM = (42, 55, 68)
WHITE = (225, 232, 240)
HILITE = (14, 34, 22)                    # selected row in lists / popups
BTN_ON = (10, 40, 18)                    # focused / primary button fill
BOX_BG = (7, 11, 16)                     # help and settings boxes

# Colour themes. GREEN is NeoTerm's own. While an AI coding CLI runs in the PowerShell session the UI
# takes that tool's colours: CLAUDE (Claude Code orange on dark grey) for `claude`, CODEX (violet on
# dark indigo) for `codex`.
THEMES = {
    "GREEN": dict(BG=(3, 6, 8), PANEL=(8, 12, 17), TERM_BG=(4, 8, 10, 190), NEON=(57, 255, 20),
                  NEON_CORE=(160, 235, 145), NEON_DIM=(22, 110, 32), STEEL_DIM=(42, 55, 68),
                  HILITE=(14, 34, 22), BTN_ON=(10, 40, 18), BOX_BG=(7, 11, 16)),
    "CLAUDE": dict(BG=(20, 20, 20), PANEL=(30, 30, 30), TERM_BG=(24, 24, 24, 205), NEON=(217, 119, 87),
                   NEON_CORE=(242, 176, 148), NEON_DIM=(118, 62, 44), STEEL_DIM=(62, 62, 62),
                   HILITE=(56, 38, 31), BTN_ON=(66, 38, 28), BOX_BG=(28, 28, 28)),
    "CODEX": dict(BG=(12, 9, 20), PANEL=(21, 16, 33), TERM_BG=(15, 11, 25, 205), NEON=(168, 110, 255),
                  NEON_CORE=(214, 186, 255), NEON_DIM=(80, 50, 136), STEEL_DIM=(60, 52, 82),
                  HILITE=(44, 30, 72), BTN_ON=(54, 32, 92), BOX_BG=(19, 14, 30)),
}
# programs whose presence under the PowerShell session selects a theme (first match wins)
AGENT_THEMES = [("codex.exe", "CODEX"), ("claude.exe", "CLAUDE")]
THEME = "GREEN"


THEME_FADE = 0.8                 # seconds for a colour cross-fade between themes
THEME_FROM = "GREEN"             # theme a running fade comes from (THEME is where it goes)
THEME_MIX = 1.0                  # fade progress 0..1 (1 = THEME fully applied)

RAIN = {                         # matrix-rain letter colour per theme, kk = brightness 0..1
    "GREEN": lambda kk: (12 + 80 * kk ** 3, 30 + 150 * kk, 18 + 50 * kk ** 3),
    "CLAUDE": lambda kk: (28 + 150 * kk ** 1.5, 26 + 80 * kk ** 2, 24 + 52 * kk ** 2.5),
    "CODEX": lambda kk: (22 + 110 * kk ** 1.5, 14 + 70 * kk ** 2, 34 + 170 * kk ** 1.2),
}


def rain_color(level):
    """Matrix-rain letter colour for brightness level 1..10, mixed like the rest of the theme."""
    kk = level / 10.0
    a, b = RAIN[THEME_FROM](kk), RAIN[THEME](kk)
    return tuple(int(x + (y - x) * THEME_MIX) for x, y in zip(a, b))


def blend_colors(src, dst, w):
    """Set the module colour constants to src blended towards dst by w; the drawing code reads
    them at draw time, so every frame of a fade shows the in-between colours."""
    globals().update({k: tuple(int(round(x + (y - x) * w)) for x, y in zip(src[k], dst[k])) for k in dst})


def current_colors():
    return {k: globals()[k] for k in THEMES["GREEN"]}


def apply_theme(name):
    """Jump straight to a theme (no fade)."""
    global THEME, THEME_FROM, THEME_MIX
    THEME = THEME_FROM = name
    THEME_MIX = 1.0
    globals().update(THEMES[name])
FG = (205, 214, 224)
CYAN = (80, 200, 255)
AMBER = (255, 176, 60)
RED = (255, 70, 95)
YELLOW = (240, 220, 90)
SEL_BG = (40, 70, 100)
PALETTE = {
    "black": (25, 30, 36), "red": (255, 85, 100), "green": (90, 220, 120), "brown": (230, 200, 80),
    "yellow": (230, 200, 80), "blue": (90, 150, 255), "magenta": (210, 120, 255), "cyan": (80, 210, 230),
    "white": (205, 214, 224), "brightblack": (100, 112, 125), "brightred": (255, 140, 150),
    "brightgreen": (150, 240, 160), "brightbrown": (255, 235, 130), "brightyellow": (255, 235, 130),
    "brightblue": (150, 190, 255), "brightmagenta": (235, 175, 255), "brightcyan": (150, 240, 250),
    "brightwhite": (245, 248, 252),
}

# ------------------------------------------------------------------ 4x6 pixel font (UI)
# Compact UI font (was 5x7). Upper case is the default; lower case is used only where asked
# (pix_text(..., case=True)), e.g. the "MAVLink Console(nsh)" button.
_F = {
    "0": ".##. #..# #.## ##.# #..# .##.", "1": "..#. .##. ..#. ..#. ..#. .###",
    "2": ".##. #..# ...# ..#. .#.. ####", "3": "###. ...# .##. ...# ...# ###.",
    "4": "#..# #..# #..# #### ...# ...#", "5": "#### #... ###. ...# ...# ###.",
    "6": ".##. #... ###. #..# #..# .##.", "7": "#### ...# ..#. .#.. .#.. .#..",
    "8": ".##. #..# .##. #..# #..# .##.", "9": ".##. #..# #..# .### ...# .##.",
    "A": ".##. #..# #..# #### #..# #..#", "B": "###. #..# ###. #..# #..# ###.",
    "C": ".##. #..# #... #... #..# .##.", "D": "###. #..# #..# #..# #..# ###.",
    "E": "#### #... ###. #... #... ####", "F": "#### #... ###. #... #... #...",
    "G": ".##. #... #.## #..# #..# .###", "H": "#..# #..# #### #..# #..# #..#",
    "I": "### .#. .#. .#. .#. ###", "J": "..## ...# ...# ...# #..# .##.",
    "K": "#..# #.#. ##.. #.#. #..# #..#", "L": "#... #... #... #... #... ####",
    "M": "#...# ##.## #.#.# #...# #...# #...#", "N": "#..# ##.# #.## #..# #..# #..#",
    "O": ".##. #..# #..# #..# #..# .##.", "P": "###. #..# #..# ###. #... #...",
    "Q": ".##. #..# #..# #..# #.#. .#.#", "R": "###. #..# #..# ###. #.#. #..#",
    "S": ".### #... .##. ...# ...# ###.", "T": "### .#. .#. .#. .#. .#.",
    "U": "#..# #..# #..# #..# #..# .##.", "V": "#...# #...# #...# .#.#. .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# ##.## #...#", "X": "#..# #..# .##. .##. #..# #..#",
    "Y": "#.# #.# #.# .#. .#. .#.", "Z": "#### ...# ..#. .#.. #... ####",
    "a": ".... .##. ...# .### #..# .###", "b": "#... #... ###. #..# #..# ###.",
    "c": ".... .... .### #... #... .###", "d": "...# ...# .### #..# #..# .###",
    "e": ".... .##. #..# #### #... .###", "f": "..# .#. ### .#. .#. .#.",
    "g": ".... .### #..# .### ...# .##.", "h": "#... #... ###. #..# #..# #..#",
    "i": "# . # # # #", "j": "..# ... ..# ..# #.# .#.",
    "k": "#... #... #..# ###. #..# #..#", "l": "#. #. #. #. #. .#",
    "m": "..... ..... ##.#. #.#.# #.#.# #.#.#", "n": ".... .... ###. #..# #..# #..#",
    "o": ".... .... .##. #..# #..# .##.", "p": ".... ###. #..# ###. #... #...",
    "q": ".... .### #..# .### ...# ...#", "r": ".... .... #.## ##.. #... #...",
    "s": ".... .... .### ##.. ..## ###.", "t": ".#. .#. ### .#. .#. ..#",
    "u": ".... .... #..# #..# #..# .###", "v": ".... .... #..# #..# .##. .##.",
    "w": "..... ..... #...# #.#.# #.#.# .#.#.", "x": ".... .... #..# .##. .##. #..#",
    "y": ".... #..# #..# .### ...# .##.", "z": ".... .... #### ..#. .#.. ####",
    ":": ". # . . # .", ".": ". . . . . #", "-": "... ... ### ... ... ...",
    "/": "...# ...# ..#. .#.. #... #...", ">": "#.. .#. ..# .#. #.. ...",
    "<": "..# .#. #.. .#. ..# ...", "_": ".... .... .... .... .... ####",
    "[": "## #. #. #. #. ##", "]": "## .# .# .# .# ##", "+": "... .#. ### .#. ... ...",
    "=": "... ### ... ### ... ...", "%": "#..# ...# ..#. .#.. #... #..#",
    "!": "# # # # . #", "?": ".##. #..# ..#. .#.. .... .#..", "^": ".#. #.# ... ... ... ...",
    "*": "... #.# .#. #.# ... ...", "#": ".#.# #### .#.# #### .#.# ....",
    "|": "# # # # # #", "'": "# # . . . .", ",": ".. .. .. .. .# #.", "(": ".# #. #. #. #. .#",
    ")": "#. .# .# .# .# #.", "&": ".#.. #.#. .#.. #.## #..# .##.",
    "@": ".##. #..# #.## #.## #... .##.", "~": ".... .#.# #.#. .... .... ....",
    " ": ".. .. .. .. .. ..",
}
PIX = {k: v.split(" ") for k, v in _F.items()}
PIX[" "] = [".."] * 6
PIX_H = 6
_pix_cache = {}


def _pix_rows(ch):
    return PIX.get(ch) or PIX.get(ch.upper()) or PIX["?"]


def pix_glyph(ch, color, scale=1):
    key = (ch, color, scale)
    g = _pix_cache.get(key)
    if g is None:
        rows = _pix_rows(ch)
        g = pygame.Surface((len(rows[0]) * scale, len(rows) * scale), pygame.SRCALPHA)
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                if c == "#":
                    g.fill(color, (x * scale, y * scale, scale, scale))
        _pix_cache[key] = g
    return g


def pix_width(s, scale=1, case=False):
    s = s if case else s.upper()
    return sum((len(_pix_rows(c)[0]) + 1) * scale for c in s) - scale if s else 0


# Hi-res text layer. Text aimed at the low-res canvas is drawn instead onto a window-resolution
# HUD with Scale2x-smoothed glyphs: half-size pixels on curves and diagonals, same layout.
# Text aimed at the neon layer goes to a text-only glow layer (blurred halo, no blocky copy).
HUD = {"canvas": None, "hud": None, "neon": None, "neon_t": None, "sc": 1}
_hi_cache = {}


def pix_glyph_hi(ch, color, sc):
    key = (ch, color, sc)
    g = _hi_cache.get(key)
    if g is None:
        g = pix_glyph(ch, color, 1)
        if sc >= 2:
            g = pygame.transform.scale2x(g)
            if sc == 4:
                g = pygame.transform.scale2x(g)
            elif sc != 2:
                g = pygame.transform.smoothscale(g, (g.get_width() * sc // 2, g.get_height() * sc // 2))
        _hi_cache[key] = g
    return g


def pix_text(surf, s, x, y, color, scale=1, case=False):
    if surf is HUD["neon"] and HUD["neon_t"] is not None:
        surf = HUD["neon_t"]
    elif surf is HUD["canvas"] and HUD["hud"] is not None:
        hud, sc = HUD["hud"], HUD["sc"] * scale
        for ch in (s if case else s.upper()):
            hud.blit(pix_glyph_hi(ch, color, sc), (x * HUD["sc"], y * HUD["sc"]))
            x += (len(_pix_rows(ch)[0]) + 1) * scale
        return x
    for ch in (s if case else s.upper()):
        g = pix_glyph(ch, color, scale)
        surf.blit(g, (x, y))
        x += g.get_width() + scale
    return x


def pix_fit(s, width):
    s = s.upper()
    if pix_width(s) <= width:
        return s
    while s and pix_width(s + "..") > width:
        s = s[:-1]
    return s + ".."


# small pixel-art quadcopter (top view) for the MAVLink console button
# R = rotor ring (a blade pair spins around it), P = prop hub, A = arm, B = body, C = FC LED
DRONE_ICON = [
    ".RRR...RRR.",
    "R.P.R.R.P.R",
    ".RRR...RRR.",
    "...A...A...",
    "....BBB....",
    "....BCB....",
    "....BBB....",
    "...A...A...",
    ".RRR...RRR.",
    "R.P.R.R.P.R",
    ".RRR...RRR.",
]
ROTOR_RING = [(1, 0), (2, 0), (3, 0), (4, 1), (3, 2), (2, 2), (1, 2), (0, 1)]   # clockwise
ROTORS = [(0, 0, 1), (6, 0, -1), (0, 8, -1), (6, 8, 1)]                        # x, y, spin dir

# matching pixel-art PowerShell tile (slanted ">_" console) for the config title bar
# R = neon frame (a bright pair sweeps round it), B = panel, P = prompt chevron, C = blinking cursor
PWSH_ICON = [
    "..RRRRRRRRR",
    "..RBBBBBBBR",
    "..RBPBBBBBR",
    ".RBBBPBBBR.",
    ".RBBBBPBBR.",
    ".RBBBPBBBR.",
    "RBBPBBCCCR.",
    "RBBBBBBBBR.",
    "RRRRRRRRRR.",
]


def _ring(icon):
    """Frame pixels of an icon in clockwise order (for the sweeping highlight)."""
    pts = [(x, y) for y, row in enumerate(icon) for x, ch in enumerate(row) if ch == "R"]
    cx = sum(x for x, _ in pts) / len(pts)
    cy = sum(y for _, y in pts) / len(pts)
    return sorted(pts, key=lambda q: math.atan2(q[1] - cy, q[0] - cx))


PWSH_RING = _ring(PWSH_ICON)


# ------------------------------------------------------------------ clipboard (Win32)
_u32, _k32 = ctypes.windll.user32, ctypes.windll.kernel32
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p


def clip_get():
    if not _u32.OpenClipboard(None):
        return ""
    try:
        h = _u32.GetClipboardData(13)
        if not h:
            return ""
        p = _k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            _k32.GlobalUnlock(h)
    finally:
        _u32.CloseClipboard()


def clip_set(text):
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _u32.OpenClipboard(None):
        return False
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(0x0002, size)
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        _k32.GlobalUnlock(h)
        return bool(_u32.SetClipboardData(13, h))
    finally:
        _u32.CloseClipboard()


# ------------------------------------------------------------------ settings / ports
def app_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        old = os.path.join(base, OLD_APP, "settings.json")
        if os.path.isfile(old):                     # keep the user's port / baud / window settings
            try:
                import shutil
                shutil.copyfile(old, os.path.join(d, "settings.json"))
            except OSError:
                pass
    return d


BAUDS = [300, 1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 115200, 230400, 460800,
         921600, 1000000, 2000000]
DEFAULTS = {
    "window": [1280, 760], "fullscreen": False, "scale": 2, "fs_scale": 0, "font_size": 9,
    "port": "", "baud": 115200, "bytesize": 8, "parity": "N", "stopbits": 1, "flow": "NONE",
    "enter": "CR", "backspace": "^H", "echo": False, "view": "TEXT", "encoding": "UTF-8",
    "implicit_cr": True, "reconnect": True, "glow": True, "scanlines": True, "rain": True,
    "proto": "RAW", "ui_rev": 2,
    # terminal text (F4 text settings)
    "term_font": "CONSOLAS", "font_weight": "NORMAL", "text_style": "LED", "line_gap": 0,
    "text_color": "DEFAULT",
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(os.path.join(app_dir(), "settings.json"), encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    s["scale"] = max(1, min(4, int(s.get("scale", 2))))
    s["fs_scale"] = max(0, min(6, int(s.get("fs_scale", 0))))     # 0 = auto from screen height
    if int(s.get("ui_rev", 0)) < 2:          # smaller UI text: terminal font 10 -> 9 once
        if int(s.get("font_size", 10)) >= 10:
            s["font_size"] = int(s["font_size"]) - 1
        s["ui_rev"] = 2
    if s.get("proto") not in ("RAW", "MAVLINK"):
        s["proto"] = "RAW"
    return s


def save_settings(s):
    try:
        with open(os.path.join(app_dir(), "settings.json"), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def frame_format(s):
    sb = {1: "1", 1.5: "1.5", 2: "2"}.get(s["stopbits"], str(s["stopbits"]))
    return "%d%s%s" % (s["bytesize"], s["parity"], sb)


# Windows reports localised port names; the UI is English only.
_KO_EN = [("표준 포트 유형", "Standard port types"), ("통신 포트", "Communications Port"),
          ("USB 직렬 장치", "USB Serial Device"), ("직렬 포트", "Serial Port"), ("직렬", "Serial"),
          ("포트", "Port"), ("장치", "Device"), ("가상", "Virtual"), ("표준", "Standard")]


def english_desc(p):
    desc = p.description or p.device
    for ko, en in _KO_EN:
        desc = desc.replace(ko, en)
    if not desc.isascii():
        man = p.manufacturer if (p.manufacturer and p.manufacturer.isascii()) else ""
        desc = (man + " Serial Port").strip() if man else "Serial Port (%s)" % p.device
        if p.vid is not None:
            desc += " %04X:%04X" % (p.vid, p.pid or 0)
    return desc


def list_ports():
    out = []
    try:
        for p in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device):
            out.append((p.device, english_desc(p)))
    except Exception:
        pass
    return out


def port_name(dev):
    return {LOOPBACK: "LOOP", SHELL_PORT: "PWSH"}.get(dev, dev)


def is_shell(dev):
    return dev == SHELL_PORT


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def descendant_names(pid):
    """Lower-case exe names of all processes below pid (one Toolhelp snapshot, ~1 ms)."""
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(2, 0)            # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return set()
    kids = {}
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
        while ok:
            kids.setdefault(e.th32ParentProcessID, []).append((e.th32ProcessID, e.szExeFile.lower()))
            ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    names, todo, seen = set(), [pid], {pid}
    while todo:
        for cpid, name in kids.get(todo.pop(), ()):
            if cpid not in seen:
                seen.add(cpid)
                names.add(name)
                todo.append(cpid)
    return names


class ShellPort:
    """Windows PowerShell in a ConPTY pseudo console, with the serial-port calls Link uses.
    ConPTY speaks VT100/UTF-8, so the output goes straight into the pyte terminal."""

    def __init__(self, rows=24, cols=80):
        from winpty import PtyProcess
        self.proc = PtyProcess.spawn(["powershell.exe", "-NoLogo"], cwd=os.path.expanduser("~"),
                                     dimensions=(rows, cols))
        self.size = (rows, cols)

    def read_some(self):
        return self.proc.read(4096).encode("utf-8")      # blocks; EOFError when the shell exits

    def running(self):
        """Names of the programs currently running inside the shell (e.g. claude.exe)."""
        try:
            return descendant_names(self.proc.pid)
        except Exception:
            return set()

    def write(self, data):
        self.proc.write(bytes(data).decode("utf-8", "replace"))
        return len(data)

    def resize(self, rows, cols):
        if (rows, cols) != self.size:
            self.size = (rows, cols)
            self.proc.setwinsize(rows, cols)

    def close(self):
        try:
            self.proc.close(force=True)
        except Exception:
            pass


# ------------------------------------------------------------------ serial link
class Link:
    def __init__(self):
        self.ser = None
        self.rx = queue.Queue()
        self.state = "CLOSED"       # CLOSED / OPEN / ERROR
        self.error = ""
        self.rx_total = self.tx_total = 0
        self._stop = threading.Event()
        self._thread = None
        self.size = (24, 80)        # terminal rows/cols, for the PowerShell pseudo console
        self.on_data = None         # called from the reader thread when bytes arrive (wakes the UI)
        self.exited = False         # the local shell process ended (typed exit)
        self.got_data = False       # something was received since open (the other end is alive)

    def open(self, s):
        self.close()
        self.exited = False
        self.got_data = False
        while True:                         # drop bytes left over from the previous port/session
            try:
                self.rx.get_nowait()
            except queue.Empty:
                break
        if is_shell(s["port"]):
            try:
                self.ser = ShellPort(*self.size)
            except Exception as e:
                self.ser, self.state, self.error = None, "ERROR", "PowerShell: %s" % e
                return False
            self.state, self.error = "OPEN", ""
            self._stop.clear()
            self._thread = threading.Thread(target=self._shell_reader, daemon=True)
            self._thread.start()
            return True
        par = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
               "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}[s["parity"]]
        stop = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}[s["stopbits"]]
        kw = dict(baudrate=int(s["baud"]), bytesize=int(s["bytesize"]), parity=par, stopbits=stop,
                  timeout=0.05, write_timeout=1.0, rtscts=s["flow"] == "RTS/CTS",
                  xonxoff=s["flow"] == "XON/XOFF", dsrdtr=s["flow"] == "DSR/DTR")
        try:
            self.ser = serial.serial_for_url(s["port"], **kw)
        except Exception as e:
            self.ser, self.state, self.error = None, "ERROR", str(e)
            return False
        self.state, self.error = "OPEN", ""
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return True

    def _reader(self):
        ser = self.ser
        while not self._stop.is_set():
            try:
                n = ser.in_waiting
                data = ser.read(n if n else 1)
            except Exception as e:
                if not self._stop.is_set():
                    self.state, self.error = "ERROR", str(e)
                break
            if data:
                self.rx_total += len(data)
                self.got_data = True
                self.rx.put(data)
                if self.on_data:
                    self.on_data()

    def _shell_reader(self):
        ser = self.ser
        while not self._stop.is_set():
            try:
                data = ser.read_some()
            except Exception:
                if not self._stop.is_set():          # shell exited: closed, not an error to retry
                    self.state, self.error, self.exited = "CLOSED", "PowerShell exited", True
                    if self.on_data:
                        self.on_data()
                break
            if data:
                self.rx_total += len(data)
                self.got_data = True
                self.rx.put(data)
                if self.on_data:
                    self.on_data()

    def resize(self, rows, cols):
        self.size = (rows, cols)
        if isinstance(self.ser, ShellPort):
            try:
                self.ser.resize(rows, cols)
            except Exception:
                pass

    def write(self, data):
        if self.state != "OPEN" or not data:
            return 0
        try:
            n = self.ser.write(data)
            self.tx_total += n or 0
            return n or 0
        except Exception as e:
            self.state, self.error = "ERROR", str(e)
            return 0

    def close(self):
        self._stop.set()
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self.ser, self._thread = None, None
        if self.state == "OPEN":
            self.state = "CLOSED"


# CSI sequences with a private marker pyte cannot parse: it drops the marker and runs the rest as a
# normal command, so modifyOtherKeys "ESC[>4;2m" turned underline on for everything after it.
# These are keyboard-protocol / identification requests (DA2, XTVERSION, modifyOtherKeys, kitty
# keyboard ESC[>1u ESC[<u ESC[=..u ESC[?u) with nothing to draw, so they are dropped before pyte.
_UNPARSED_CSI = re.compile(r"\x1b\[(?:[<=>][0-9;:]*|\?[0-9;:]*(?=u))[ -/]*[@-~]")
_CSI_TAIL = re.compile(r"\x1b(?:\[[0-9;:<=>?]*[ -/]*)?$")


class CsiFilter:
    """Removes _UNPARSED_CSI from a stream of text chunks; a sequence split across two chunks is
    held back until the rest arrives."""

    def __init__(self):
        self.carry = ""

    def __call__(self, text):
        text = self.carry + text
        self.carry = ""
        m = _CSI_TAIL.search(text[-64:])
        if m:
            cut = len(text) - (len(text[-64:]) - m.start())
            text, self.carry = text[:cut], text[cut:]
        return _UNPARSED_CSI.sub("", text)


class TermScreen(pyte.HistoryScreen):
    """HistoryScreen whose scrollback lines share identical Char objects. pyte makes a new Char
    for every cell written; interning them when a line scrolls into history cuts the scrollback
    memory several times (most cells repeat: same letter, same colours)."""
    _chars = {}

    def resize(self, lines=None, columns=None):
        """pyte drops rows from the TOP when the screen gets shorter, which wipes the text of a
        nearly empty screen. Like other terminals, drop only as many top rows as needed to keep
        the cursor on screen (they go to the scrollback) and cut blank rows at the bottom."""
        lines = lines or self.lines
        if lines < self.lines:
            drop = max(0, self.cursor.y + 1 - lines)
            buf = self.buffer
            for y in range(drop):
                self.history.top.append(buf[y])
            kept = {y - drop: buf[y] for y in range(drop, drop + lines) if y in buf}
            buf.clear()
            buf.update(kept)
            self.cursor.y -= drop
            self.lines = lines
            self.dirty.update(range(lines))
            self.set_margins()
        super().resize(lines, columns)

    shift = 0                   # full-screen scrolls since the renderer last looked
    shift_invalid = False       # a scroll the row cache cannot follow (scroll region)

    def index(self):
        top, bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        into_history = self.cursor.y == bottom
        before = set(self.dirty) if into_history else None
        super().index()
        if into_history:
            if self.history.top:
                line, cache = self.history.top[-1], self._chars
                if len(cache) > 20000:
                    cache.clear()
                for k, ch in line.items():
                    line[k] = cache.setdefault(ch, ch)
            # pyte marks every row dirty on a scroll. For a full-screen scroll the rows just move
            # up one, so keep the real dirty rows (moved up) + the new bottom row, and count the
            # shift: the renderer moves its cached rows instead of re-rendering the whole screen.
            if (top, bottom) == (0, self.lines - 1):
                self.dirty.clear()
                self.dirty.update(r - 1 for r in before if r > 0)
                self.dirty.add(bottom)
                self.shift += 1
            else:
                self.shift_invalid = True


class HexLog:
    """RX/TX bytes grouped into lines: new line on direction change, full line or a 30 ms gap."""

    def __init__(self, maxlines=20000):
        self.lines = deque(maxlen=maxlines)   # [direction, t_first, bytearray, t_last]
        self.per_line = 16

    def add(self, direction, data, now):
        for b in data:
            last = self.lines[-1] if self.lines else None
            if (last is None or last[0] != direction or len(last[2]) >= self.per_line
                    or now - last[3] > 0.03):
                last = [direction, now, bytearray(), now]
                self.lines.append(last)
            last[2].append(b)
            last[3] = now

    def clear(self):
        self.lines.clear()


# ------------------------------------------------------------------ MAVLink shell (nsh)
# Same mechanism as QGroundControl's MAVLink Console: nsh input/output travels inside
# SERIAL_CONTROL (#126) messages with device SERIAL_CONTROL_DEV_SHELL (10).
MAV_CRC_EXTRA = {0: 50, 126: 220}                 # HEARTBEAT, SERIAL_CONTROL
SC_DEV_SHELL = 10
SC_REPLY, SC_RESPOND, SC_EXCLUSIVE, SC_MULTI = 1, 2, 4, 16
SC_DATA = 70
MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID = 6, 8


def x25crc(data, crc=0xFFFF):
    for b in data:
        tmp = (b ^ crc) & 0xFF
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


class MavShell:
    """MAVLink v2 framing for the PX4 shell. feed() takes raw link bytes and returns nsh output."""
    SYSID, COMPID = 255, 190                      # GCS / MAV_COMP_ID_MISSIONPLANNER
    HB_TIMEOUT = 3.0

    def __init__(self):
        self.reset()

    def reset(self):
        self.buf = bytearray()
        self.seq = 0
        self.target = 0                           # learned from the vehicle heartbeat
        self.vehicle = None                       # (sysid, compid)
        self.last_hb = 0.0
        self.next_hb_tx = 0.0
        self.opened_at = time.time()
        self.greeted = False
        self.frames = self.crc_errors = 0

    def alive(self, now=None):
        return self.vehicle is not None and (now or time.time()) - self.last_hb < self.HB_TIMEOUT

    # ---- encode
    def frame(self, msgid, payload):
        payload = payload.rstrip(b"\0") or b"\0"          # MAVLink 2 trailing-zero truncation
        hdr = bytes([len(payload), 0, 0, self.seq, self.SYSID, self.COMPID,
                     msgid & 0xFF, (msgid >> 8) & 0xFF, (msgid >> 16) & 0xFF])
        self.seq = (self.seq + 1) & 0xFF
        crc = x25crc(bytes([MAV_CRC_EXTRA[msgid]]), x25crc(hdr + payload))
        return b"\xfd" + hdr + payload + bytes([crc & 0xFF, crc >> 8])

    def heartbeat(self):
        return self.frame(0, struct.pack("<IBBBBB", 0, MAV_TYPE_GCS, MAV_AUTOPILOT_INVALID, 0, 0, 3))

    def serial_control(self, data, flags):
        out = bytearray()
        chunks = [data[i:i + SC_DATA] for i in range(0, len(data), SC_DATA)] or [b""]
        for ch in chunks:
            p = (struct.pack("<IHBBB", 0, 0, SC_DEV_SHELL, flags, len(ch)) + bytes(ch).ljust(SC_DATA, b"\0")
                 + bytes([self.target, 0]))
            out += self.frame(126, p)
        return bytes(out)

    def shell_write(self, data):
        return self.serial_control(bytes(data), SC_RESPOND | SC_EXCLUSIVE | SC_MULTI)

    def shell_close(self):
        return self.serial_control(b"", 0)                 # no RESPOND flag -> PX4 closes the shell

    def tick(self, now):
        """Bytes to send now: 1 Hz GCS heartbeat (also wakes PX4's USB MAVLink) + the first newline
        that makes PX4 create the shell and print the prompt."""
        out = b""
        if now >= self.next_hb_tx:
            self.next_hb_tx = now + 1.0
            out += self.heartbeat()
        if not self.greeted and (self.vehicle is not None or now - self.opened_at > self.HB_TIMEOUT):
            self.greeted = True
            out += self.shell_write(b"\n")
        return out

    # ---- decode
    def feed(self, data, now=None):
        now = now or time.time()
        b = self.buf
        b += data
        out = bytearray()
        i, end = 0, len(b)
        # Only HEARTBEAT and SERIAL_CONTROL matter and both are CRC-checked. Everything else
        # (telemetry, junk, false start bytes inside payloads) is stepped over one byte at a time,
        # so a false start byte can never swallow a real frame behind it.
        nfd = nfe = -2                                  # cached next 0xFD / 0xFE positions
        while i < end:
            if nfd != -1 and nfd < i:
                nfd = b.find(b"\xfd", i)
            if nfe != -1 and nfe < i:
                nfe = b.find(b"\xfe", i)
            starts = [x for x in (nfd, nfe) if x >= i]
            if not starts:
                i = end
                break
            i = min(starts)
            if b[i] == 0xFE:                                # MAVLink 1
                if end - i < 6:
                    break
                n = 8 + b[i + 1]
                msgid, sysid, compid, body = b[i + 5], b[i + 3], b[i + 4], 6
            else:                                           # MAVLink 2
                if end - i < 10:
                    break
                if b[i + 2] & ~1:                           # unknown incompat flags: not a frame
                    i += 1
                    continue
                n = 12 + b[i + 1] + (13 if b[i + 2] & 1 else 0)
                msgid = b[i + 7] | (b[i + 8] << 8) | (b[i + 9] << 16)
                sysid, compid, body = b[i + 5], b[i + 6], 10
            extra = MAV_CRC_EXTRA.get(msgid)
            if extra is None:
                i += 1
                continue
            if end - i < n:
                break
            plen = b[i + 1]
            c = i + body + plen
            crc = x25crc(bytes([extra]), x25crc(b[i + 1:c]))
            if crc != (b[c] | (b[c + 1] << 8)):
                self.crc_errors += 1
                i += 1                                      # resync on the next start byte
                continue
            self.handle(msgid, sysid, compid, bytes(b[i + body:c]), out, now)
            self.frames += 1
            i += n
        del b[:i]
        if len(b) > 4096:
            b.clear()
        return bytes(out)

    def handle(self, msgid, sysid, compid, p, out, now):
        if msgid == 0:
            p = p.ljust(9, b"\0")
            _, mtype, autopilot = struct.unpack_from("<IBB", p)
            if autopilot != MAV_AUTOPILOT_INVALID and mtype != MAV_TYPE_GCS:
                if self.vehicle is None or self.vehicle[0] == sysid:
                    self.vehicle, self.target, self.last_hb = (sysid, compid), sysid, now
        elif msgid == 126:
            p = p.ljust(9 + SC_DATA, b"\0")
            _, _, dev, flags, count = struct.unpack_from("<IHBBB", p)
            if dev == SC_DEV_SHELL and flags & SC_REPLY:
                out += p[9:9 + min(count, SC_DATA)]


# ------------------------------------------------------------------ configuration model
FIELDS = ["BAUD RATE", "DATA BITS", "PARITY", "STOP BITS", "FLOW CONTROL", "ENTER SENDS",
          "BACKSPACE", "LOCAL ECHO", "IMPLICIT CR", "VIEW", "ENCODING", "AUTO RECONNECT"]
CHOICES = {
    "DATA BITS": ("bytesize", [5, 6, 7, 8]),
    "PARITY": ("parity", ["N", "E", "O", "M", "S"]),
    "STOP BITS": ("stopbits", [1, 1.5, 2]),
    "FLOW CONTROL": ("flow", ["NONE", "RTS/CTS", "XON/XOFF", "DSR/DTR"]),
    "ENTER SENDS": ("enter", ["CR", "LF", "CR+LF"]),
    "BACKSPACE": ("backspace", ["^H", "^?"]),
    "LOCAL ECHO": ("echo", [False, True]),
    "IMPLICIT CR": ("implicit_cr", [True, False]),
    "VIEW": ("view", ["TEXT", "HEX"]),
    "ENCODING": ("encoding", ["UTF-8", "CP949", "ASCII"]),
    "AUTO RECONNECT": ("reconnect", [True, False]),
}
PARITY_NAME = {"N": "NONE", "E": "EVEN", "O": "ODD", "M": "MARK", "S": "SPACE"}
CFG_SIZE = (430, 300)            # configuration canvas (pixels before scaling)
PORT_SCAN_S = 2.0                # automatic COM port re-scan interval in the configuration window
MAV_LABEL = "MAVLink Console(nsh)"
BTN_OPEN, BTN_CANCEL, BTN_MAV = 0, 1, 2
BTN_ORDER = [BTN_MAV, BTN_OPEN, BTN_CANCEL]      # left -> right on screen

REPEAT_TERM = {pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT,
               pygame.K_RIGHT, pygame.K_PAGEUP, pygame.K_PAGEDOWN, pygame.K_HOME, pygame.K_END,
               pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB, pygame.K_SPACE}
REPEAT_CFG = {pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_BACKSPACE}
SPECIAL = {
    pygame.K_TAB: b"\t", pygame.K_ESCAPE: b"\x1b", pygame.K_UP: b"\x1b[A", pygame.K_DOWN: b"\x1b[B",
    pygame.K_RIGHT: b"\x1b[C", pygame.K_LEFT: b"\x1b[D", pygame.K_HOME: b"\x1b[H", pygame.K_END: b"\x1b[F",
    pygame.K_INSERT: b"\x1b[2~", pygame.K_DELETE: b"\x1b[3~", pygame.K_PAGEUP: b"\x1b[5~",
    pygame.K_PAGEDOWN: b"\x1b[6~",
}
TOOL_H, HINT_H, PAD = 16, 11, 6
RATE_BIN, RATE_HIST = 0.1, 160     # toolbar graph: 100 ms bins, 16 s of history
GRAPH_W = 132                      # toolbar graph width (canvas pixels)
# terminal text styles: LED dot-matrix texture, SMOOTH anti-aliased, PIXEL Scale2x pixels, SHARP 1-px aliased
TEXT_STYLES = {"LED": "led", "SMOOTH": "smooth", "PIXEL": "dot", "SHARP": "fine"}
WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
TERM_FONTS = {                     # name: (regular file, bold file or None = synthetic bold)
    "CONSOLAS": ("consola.ttf", "consolab.ttf"),
    "CASCADIA MONO": ("CascadiaMono.ttf", None),
    "COURIER NEW": ("cour.ttf", "courbd.ttf"),
    "LUCIDA CONSOLE": ("lucon.ttf", None),
}
TEXT_COLORS = {"DEFAULT": (205, 214, 224), "WHITE": (240, 244, 248), "GREEN": (120, 255, 120),
               "AMBER": (255, 190, 80), "CYAN": (110, 215, 255)}
FONT_SIZES = list(range(6, 25))


def font_file(name):
    p = os.path.join(WIN_FONTS, name) if name else None
    return p if p and os.path.isfile(p) else None


def term_fonts():
    """Monospace fonts present on this PC (Consolas is on every Windows 10/11)."""
    return [n for n, (f, _) in TERM_FONTS.items() if font_file(f)] or ["CONSOLAS"]


# text settings popup rows: label, settings key, options (callable = computed when opened)
TEXT_ROWS = [("UI SCALE", "ui_scale", None), ("FONT", "term_font", term_fonts), ("SIZE", "font_size", lambda: FONT_SIZES),
             ("WEIGHT", "font_weight", lambda: ["NORMAL", "BOLD"]),
             ("STYLE", "text_style", lambda: list(TEXT_STYLES)),
             ("LINE GAP", "line_gap", lambda: [0, 1, 2, 3, 4]),
             ("TEXT COLOR", "text_color", lambda: list(TEXT_COLORS))]
LED_GAP = 150                      # alpha of the gaps between LED dots (255 = no texture)
RAIN_CHARS = string.ascii_letters + string.digits + "#$%&*+-=<>/\\|{}[]@!?:;~^"


class App:
    def __init__(self, headless=False):
        self.s = load_settings()
        pygame.init()
        pygame.key.set_repeat(*KEY_REPEAT)
        self.held = set()                  # keys currently down (to tell repeats from presses)
        self.no_repeat = set()             # keys held across a window switch: never repeat
        self.quiet_send = False
        pygame.display.set_caption(APP)
        pygame.display.set_icon(self.make_icon(32))
        self.load_fonts()
        self.link = Link()
        self.mav = None                    # MavShell while in MAVLink console (nsh) mode
        self.hex = HexLog()
        self.hex_scroll = 0
        self.rates = deque([(0, 0)] * RATE_HIST, maxlen=RATE_HIST)   # bytes per RATE_BIN
        self.gscale = 64.0                 # graph auto-scale (bytes per bin), eased
        self.bins = 0                      # bins so far (grid scroll phase)
        self.bin_rx = self.bin_tx = 0
        self.bin_t = time.time()
        self.rate_rx = self.rate_tx = 0
        self.ports = list_ports()
        if self.ports and self.s["port"] not in [p for p, _ in self.ports]:
            self.s["port"] = self.ports[0][0]
        self.vt = None
        self.session_started = False
        self.help = False
        self.claude_on = False              # Claude Code running in the PowerShell session
        self.next_claude_check = 0.0
        self.textset = False                # text settings popup (F4)
        self.textset_row = 0
        self.textset_rects = {}
        self.toast, self.toast_until = "", 0.0
        self.sel, self.dragging = None, False
        self.composing = ""
        self.log = None
        self.want_open = False
        self.next_retry = 0.0
        self.cfg_error = ""
        self.last_click = (0, 0.0)
        self.enter_config()

    # ================================================================ common
    def load_fonts(self):
        size = int(self.s["font_size"])
        sc = self.sc
        name = self.s.get("term_font", "CONSOLAS")
        reg, bd = TERM_FONTS.get(name, TERM_FONTS["CONSOLAS"])
        mono = font_file(reg) or font_file("consola.ttf") or pygame.font.match_font("couriernew")
        mono_bold = font_file(bd) if font_file(reg) else font_file("consolab.ttf")
        heavy = self.s.get("font_weight") == "BOLD"
        base = mono_bold if (heavy and mono_bold) else mono       # BOLD weight: real bold face
        fake = heavy and not mono_bold                              # ...or synthetic bold
        wide = (font_file("malgunbd.ttf") if heavy else None) or font_file("malgun.ttf") \
            or pygame.font.match_font("malgungothic") or mono

        def make(path, px, bold=False):
            try:
                f = pygame.font.Font(path, px)
            except Exception:
                f = pygame.font.Font(None, px)
            f.set_bold(bool(bold))
            return f

        self.font = make(base, size, fake)
        self.font_b = make(mono_bold or mono, size, not mono_bold)   # SGR bold cells
        self.font_w = make(wide, size)
        self.font_rain = make(font_file("consola.ttf") or mono, 8)
        self.cell_w = self.font.size("M")[0]
        self.cell_h = self.font.get_linesize() + int(self.s.get("line_gap", 0))
        # terminal text is rendered at window resolution (anti-aliased) onto the HUD layer
        self.font_hi = make(base, size * sc, fake)
        self.font_hi_b = make(mono_bold or mono, size * sc, not mono_bold)
        self.font_hi_w = make(wide, size * sc)
        # symbols the monospace / Korean fonts lack (Claude Code's ❯ ⏵ ⚠ ◐ ...): Segoe UI Symbol
        self.font_hi_sym = make(font_file("seguisym.ttf") or wide, size * sc)
        self._has = {}
        self.text_style = TEXT_STYLES.get(self.s.get("text_style"), "led")
        self.text_fg = TEXT_COLORS.get(self.s.get("text_color"), FG)
        self.font_sc = sc
        self.glyphs = {}
        self.rain_cache = {}

    def make_icon(self, size):
        ic = pygame.Surface((16, 16), pygame.SRCALPHA)
        ic.fill((6, 10, 14, 255), (0, 1, 16, 14))
        pygame.draw.rect(ic, NEON, (0, 1, 16, 14), 1)
        for (x, y) in ((3, 5), (4, 6), (5, 7), (4, 8), (3, 9)):
            ic.set_at((x, y), NEON_CORE)
        ic.fill(CYAN, (7, 10, 5, 1))
        return pygame.transform.scale(ic, (size, size))

    @property
    def sc(self):
        """Pixel scale in effect. Windowed: the user's scale. Fullscreen: its own scale (Ctrl +/-
        while fullscreen), auto = about 360 canvas rows like a normal window, so the UI keeps its
        proportions on a big screen instead of shrinking into a sea of empty space."""
        size = getattr(self, "fs_size", None)
        if not self.s["fullscreen"] or not size:
            sc = self.s["scale"]
            if getattr(self, "mode", "") == "config":        # fixed-size window must fit the desktop
                sc = min(sc, self.max_window_scale())
            return max(1, sc)
        W, H = size
        sc = int(self.s.get("fs_scale", 0)) or max(self.s["scale"], H // 360)
        if getattr(self, "mode", "") != "term":              # config panel must fit on screen
            sc = min(sc, W // CFG_SIZE[0], H // CFG_SIZE[1])
        return max(1, sc)

    def set_canvas(self, w, h):
        self.cw, self.ch = w, h
        self.canvas = pygame.Surface((w, h))
        self.neon = pygame.Surface((w, h))
        self.neon_t = pygame.Surface((w, h))          # text glow (blurred passes only)
        sc = self.sc
        if getattr(self, "font_sc", sc) != sc:
            self.load_fonts()
        self.hud = pygame.Surface((w * sc, h * sc), pygame.SRCALPHA)
        HUD.update(canvas=self.canvas, hud=self.hud, neon=self.neon, neon_t=self.neon_t, sc=sc)
        self.scan = None
        if sc >= 2:
            self.scan = pygame.Surface((w * sc, h * sc))      # multiplied: = black at alpha 50
            self.scan.fill((255, 255, 255))
            for y in range(sc - 1, h * sc, sc):
                self.scan.fill((205, 205, 205), (0, y, w * sc, 1))
        self.rain_init()

    def offset(self):
        sc = self.sc
        return ((self.win.get_width() - self.cw * sc) // 2, (self.win.get_height() - self.ch * sc) // 2)

    def to_canvas(self, pos):
        sc = self.sc
        ox, oy = self.offset()
        return ((pos[0] - ox) // sc, (pos[1] - oy) // sc)

    def notify(self, msg):
        self.toast, self.toast_until = msg, time.time() + 2.0

    # ---------------------------------------------------------------- neon + rain
    def neon_line(self, a, b, core=True):
        pygame.draw.line(self.neon, NEON, a, b)
        if core:
            pygame.draw.line(self.canvas, NEON_CORE, a, b)

    def neon_text(self, s, x, y, scale=1, case=False):
        """Fluorescent text: green glow from the neon layer, bright core on the canvas."""
        pix_text(self.neon, s, x, y, NEON, scale, case)
        return pix_text(self.canvas, s, x, y, NEON_CORE, scale, case)

    def draw_logo(self, x, h, t, text=True):
        """Blinking program icon (the window icon's ">_" console, small) + "NeoTerm".
        h = bar height; returns the x after the text."""
        on = (t % 2.4) < 1.5                           # slow blink: lit 1.5 s, dim 0.9 s
        c, nl = self.canvas, self.neon
        iy = (h - 11) // 2
        frame = NEON_CORE if on else NEON_DIM
        c.fill((6, 10, 14), (x, iy, 13, 11))
        pygame.draw.rect(c, frame, (x, iy, 13, 11), 1)
        chev = NEON_CORE if on else NEON_DIM
        for px, py in ((3, 3), (4, 4), (5, 5), (4, 6), (3, 7)):
            c.set_at((x + px, iy + py), chev)
        if on:
            pygame.draw.rect(nl, NEON, (x, iy, 13, 11), 1)
            for px, py in ((3, 3), (4, 4), (5, 5), (4, 6), (3, 7)):
                nl.set_at((x + px, iy + py), NEON)
            c.fill(CYAN, (x + 7, iy + 7, 4, 1))
            nl.fill(CYAN, (x + 7, iy + 7, 4, 1))
        if not text:
            return x + 13
        return self.neon_text("NeoTerm", x + 17, (h - PIX_H) // 2, case=True)

    def neon_frame(self, r, corner=5, brackets=True):
        c = corner
        pts = [(r.left + c, r.top), (r.right - c, r.top), (r.right, r.top + c), (r.right, r.bottom - c),
               (r.right - c, r.bottom), (r.left + c, r.bottom), (r.left, r.bottom - c), (r.left, r.top + c)]
        pygame.draw.lines(self.neon, NEON_DIM, True, pts)
        pygame.draw.lines(self.canvas, NEON_DIM, True, pts)
        if brackets:
            for (px, py, dx, dy) in ((r.left, r.top, 1, 1), (r.right, r.top, -1, 1),
                                     (r.left, r.bottom, 1, -1), (r.right, r.bottom, -1, -1)):
                self.neon_line((px + dx * 2, py + dy * 10), (px + dx * 2, py + dy * 2))
                self.neon_line((px + dx * 2, py + dy * 2), (px + dx * 10, py + dy * 2))
            mid = r.centerx
            self.neon_line((mid - 32, r.top), (mid + 32, r.top))
            self.neon_line((mid - 32, r.bottom), (mid + 32, r.bottom))

    def apply_glow(self, t):
        if not self.s["glow"]:
            self.canvas.blit(self.neon, (0, 0), special_flags=pygame.BLEND_ADD)
            return
        # The blurred halo is the expensive part, so it is rebuilt at GLOW_HZ and reused in between;
        # the sharp neon lines are still added every frame.
        halo = getattr(self, "_halo", None)
        if halo is None or halo.get_size() != (self.cw, self.ch) or t >= self._halo_next:
            w, h = self.cw, self.ch
            # breathing: smooth inhale/exhale (sin^2 eases in and out at both ends)
            breath = math.sin(math.pi * (t % BREATH_S) / BREATH_S) ** 2
            pulse = BREATH_MIN + (1 - BREATH_MIN) * breath
            k = int(255 * pulse * 0.72)        # near halo strength
            kf = int(190 * pulse)              # wide halo strength (255 = full)
            # Everything is blurred and mixed at 1/3 size; only the result is scaled up once.
            sw, sh = max(1, w // 3), max(1, h // 3)
            near = pygame.transform.smoothscale(self.neon, (sw, sh))
            txt = pygame.transform.smoothscale(self.neon_t, (sw, sh))   # text glows softer
            txt.fill((TEXT_GLOW, TEXT_GLOW, TEXT_GLOW), special_flags=pygame.BLEND_MULT)
            near.blit(txt, (0, 0), special_flags=pygame.BLEND_ADD)
            far = pygame.transform.smoothscale(near, (max(1, w // 10), max(1, h // 10)))
            far = pygame.transform.smoothscale(far, (sw, sh))
            near.fill((k, k, k), special_flags=pygame.BLEND_MULT)
            far.fill((kf, kf, kf), special_flags=pygame.BLEND_MULT)
            far.blit(far, (0, 0), special_flags=pygame.BLEND_ADD)
            far.blit(near, (0, 0), special_flags=pygame.BLEND_ADD)
            far.blit(near, (0, 0), special_flags=pygame.BLEND_ADD)
            halo = pygame.transform.smoothscale(far, (w, h))
            self._halo, self._halo_next = halo, t + 1.0 / GLOW_HZ
        self.canvas.blit(halo, (0, 0), special_flags=pygame.BLEND_ADD)
        self.canvas.blit(self.neon, (0, 0), special_flags=pygame.BLEND_ADD)

    def rain_init(self):
        self.drops = [[x, random.uniform(0, self.ch * 1.2), random.uniform(18, 55), random.randint(6, 16),
                       [random.choice(RAIN_CHARS) for _ in range(20)]]
                      for x in range(2, self.cw, 7)]
        self.rain_t = time.time()

    def rain_glyph(self, ch, level):
        key = (ch, level)
        g = self.rain_cache.get(key)
        if g is None:
            g = self.font_rain.render(ch, False, rain_color(level))
            self.rain_cache[key] = g
        return g

    def draw_rain(self, c, now, area):
        dt = min(0.1, now - self.rain_t)
        self.rain_t = now
        step = 9
        clip = c.get_clip()
        c.set_clip(area)
        for d in self.drops:
            d[1] += d[2] * dt
            if d[1] - d[3] * step > self.ch:
                d[1] = random.uniform(-80, 0)
                d[2] = random.uniform(18, 55)
                d[3] = random.randint(6, 16)
            if random.random() < 0.15:          # characters keep flickering to new random symbols
                d[4][random.randrange(len(d[4]))] = random.choice(RAIN_CHARS)
            head = int(d[1] // step)
            for i in range(d[3]):
                y = (head - i) * step
                level = max(1, 10 - i * 10 // d[3])
                c.blit(self.rain_glyph(d[4][(head - i) % len(d[4])], level), (d[0], y))
        c.set_clip(clip)

    def present(self):
        sc = self.sc
        ox, oy = self.offset()
        size = (self.cw * sc, self.ch * sc)
        W, H = self.win.get_size()
        if (ox, oy) == (0, 0) and size == (W, H):
            pygame.transform.scale(self.canvas, size, self.win)     # straight into the window
            if self.scan is not None and self.s["scanlines"]:
                self.win.blit(self.scan, (0, 0), special_flags=pygame.BLEND_MULT)
            self.win.blit(self.hud, (0, 0))
        else:
            if ox > 0:                                  # letterbox borders only
                self.win.fill((0, 0, 0), (0, 0, ox, H))
                self.win.fill((0, 0, 0), (ox + size[0], 0, W - ox - size[0], H))
            if oy > 0:
                self.win.fill((0, 0, 0), (0, 0, W, oy))
                self.win.fill((0, 0, 0), (0, oy + size[1], W, H - oy - size[1]))
            out = getattr(self, "_out", None)
            if out is None or out.get_size() != size:
                out = self._out = pygame.Surface(size)
            pygame.transform.scale(self.canvas, size, out)
            if self.scan is not None and self.s["scanlines"]:
                out.blit(self.scan, (0, 0), special_flags=pygame.BLEND_MULT)
            out.blit(self.hud, (0, 0))
            self.win.blit(out, (ox, oy))
        pygame.display.flip()

    def compose(self):
        """Pixel canvas scaled up, CRT scanlines, then the smooth hi-res text / graph layer."""
        sc = self.sc
        out = pygame.transform.scale(self.canvas, (self.cw * sc, self.ch * sc))
        if self.scan is not None and self.s["scanlines"]:
            out.blit(self.scan, (0, 0), special_flags=pygame.BLEND_MULT)
        out.blit(self.hud, (0, 0))
        return out

    def begin_frame(self):
        self.canvas.fill(BG)
        self.neon.fill((0, 0, 0))
        self.neon_t.fill((0, 0, 0))
        self.hud.fill((0, 0, 0, 0))

    def hud_fill(self, color, rect):
        sc = self.sc
        r = pygame.Rect(rect)
        self.hud.fill(color, (r.x * sc, r.y * sc, r.w * sc, r.h * sc))

    # ================================================================ configuration window
    def enter_config(self):
        self.mode = "config"
        self.set_theme("GREEN")                    # the configuration window is always NeoTerm green
        self.next_claude_check = 0.0
        self.no_repeat = set(getattr(self, "held", ()))
        self.cfg_snapshot = dict(self.s)
        self.ports = list_ports()
        self.focus = "ports"               # ports / fields / buttons
        want = self.s.get("serial_port", "") if is_shell(self.s["port"]) else self.s["port"]
        self.port_sel = next((i for i, (p, _) in enumerate(self.ports) if p == want), 0)
        self.field = 0
        self.button = 0                    # 0 OPEN, 1 CANCEL
        self.baud_edit = ""
        self.port_scroll = 0
        self.next_port_scan = time.time() + PORT_SCAN_S
        self.refresh_flash = 0.0
        self.cfg_window()

    def cfg_window(self):
        """Configuration window: fixed size, or (F11) fullscreen like the terminal - the canvas then
        covers the whole screen (title bar full width, rain everywhere) and the panel is centred."""
        if self.s["fullscreen"]:
            self.win = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            W, H = self.fs_size = self.win.get_size()
            sc = self.sc
            self.set_canvas(max(CFG_SIZE[0], W // sc), max(CFG_SIZE[1], H // sc))
        else:
            sc = self.sc
            self.win = pygame.display.set_mode((CFG_SIZE[0] * sc, CFG_SIZE[1] * sc))
            self.set_canvas(*CFG_SIZE)
        pygame.display.set_caption("%s %s Configuration" % (APP, VERSION))
        self.cfg_layout()

    def cfg_layout(self):
        w, h = CFG_SIZE
        bx = (self.cw - w) // 2                      # panel origin: 0,0 in a window, centred fullscreen
        by = max(0, (self.ch - h) // 2)
        self.r_frame = pygame.Rect(bx + 8, by + 22, w - 16, h - 30)
        self.r_ports = pygame.Rect(bx + 18, by + 46, 178, 150)
        self.r_fields = pygame.Rect(bx + 210, by + 46, 202, len(FIELDS) * 12 + 4)
        self.r_open = pygame.Rect(bx + w - 18 - 150, by + h - 34, 70, 16)
        self.r_cancel = pygame.Rect(bx + w - 18 - 70, by + h - 34, 70, 16)
        self.r_refresh = pygame.Rect(self.r_ports.right - 56, self.r_ports.y - 15, 56, 12)
        mw = len(DRONE_ICON[0]) + 5 + pix_width(MAV_LABEL, case=True) + 14
        self.r_mav = pygame.Rect(bx + 18, by + h - 34, mw, 16)
        x = 6 + 17 + pix_width("NeoTerm", case=True) + 12 + pix_width("SESSION CONFIGURATION") + 10
        self.r_pwsh = pygame.Rect(x - 2, 0, len(PWSH_ICON[0]) + 6, 15)   # title-bar PowerShell icon

    def refresh_ports(self, manual=False):
        """Re-scan COM ports; keeps the selected port and reports plugged/unplugged ones."""
        cur = self.ports[self.port_sel][0] if self.ports else ""
        old = {p for p, _ in self.ports}
        self.ports = list_ports()
        new = {p for p, _ in self.ports}
        self.port_sel = next((i for i, (p, _) in enumerate(self.ports) if p == cur), 0)
        added, removed = sorted(new - old), sorted(old - new)
        if added:
            self.notify("NEW PORT: " + ", ".join(added))
        elif removed:
            self.notify("PORT REMOVED: " + ", ".join(removed))
        elif manual:
            self.notify("PORTS REFRESHED - %d FOUND" % len(self.ports))
        if manual:
            self.refresh_flash = time.time() + 0.3
        self.next_port_scan = time.time() + PORT_SCAN_S
        return bool(added or removed)

    def field_value(self, name):
        s = self.s
        if name == "BAUD RATE":
            return (self.baud_edit + "_") if self.baud_edit else str(s["baud"])
        if name == "PARITY":
            return PARITY_NAME[s["parity"]]
        key, _ = CHOICES[name]
        v = s[key]
        if isinstance(v, bool):
            return "ON" if v else "OFF"
        return str(v)

    def cycle(self, name, d):
        s = self.s
        if name == "BAUD RATE":
            self.baud_edit = ""
            if s["baud"] in BAUDS:
                i = BAUDS.index(s["baud"]) + d
            else:
                i = next((k for k, b in enumerate(BAUDS) if b > s["baud"]), len(BAUDS)) + (0 if d > 0 else -1)
            s["baud"] = BAUDS[max(0, min(len(BAUDS) - 1, i))]
            return
        key, opts = CHOICES[name]
        i = opts.index(s[key]) if s[key] in opts else 0
        s[key] = opts[(i + d) % len(opts)]

    def commit_baud(self):
        if self.baud_edit:
            try:
                b = int(self.baud_edit)
                if 50 <= b <= 12000000:
                    self.s["baud"] = b
            except ValueError:
                pass
            self.baud_edit = ""

    def eat_shortcut_text(self):
        """A letter shortcut (P, M) also produces a text-input event for the same keystroke, which
        arrives in the freshly opened terminal and would be typed into the shell / nsh. The next
        text event is dropped - judged by order, not by a short timer (opening PowerShell or
        re-creating the window can take a while): it is always the one that comes before the next
        key press, so any later key press cancels the drop."""
        self.eat_text = True
        self.eat_text_until = time.time() + 3.0

    def cfg_open(self, proto="RAW"):
        self.commit_baud()
        if proto == "SHELL":
            if self.ports:
                self.s["serial_port"] = self.ports[self.port_sel][0]
            self.s["port"], proto = SHELL_PORT, "RAW"
        elif not self.ports:
            self.cfg_error = "No serial port found - plug in a device (auto scan 2s)"
            return
        else:
            self.s["port"] = self.s["serial_port"] = self.ports[self.port_sel][0]
        self.s["proto"] = proto
        save_settings(self.s)
        self.mav_close()
        if not self.link.open(self.s):
            self.cfg_error = self.link.error or "Cannot open port"
            return
        self.cfg_error = ""
        self.want_open = True
        if self.vt is not None:             # reopened from F2: start with a clean screen
            self.clear_terminal()
        self.enter_terminal()
        self.mav_start()
        port = port_name(self.s["port"])
        self.notify("POWERSHELL" if is_shell(self.s["port"]) else
                    ("MAVLINK CONSOLE %s" if self.mav else "OPENED %s") % port)

    # ---- MAVLink console (nsh)
    def mav_start(self):
        """Begin a MAVLink shell session on the open link when proto is MAVLINK."""
        if self.s["proto"] != "MAVLINK":
            self.mav = None
            pygame.display.set_caption(APP)
            return
        self.mav = MavShell()
        pygame.display.set_caption(APP + " - MAVLink Console (nsh)")
        if self.vt is not None:
            self.stream.feed("\x1b[36m[MAVLINK] %s  waiting for PX4 heartbeat...\x1b[0m\r\n"
                             % port_name(self.s["port"]))
        self.mav_tick(time.time())

    def mav_tick(self, now):
        if self.mav is None or self.link.state != "OPEN":
            return
        out = self.mav.tick(now)
        if out:
            self.bin_tx += self.link.write(out)

    def mav_close(self):
        if self.mav is not None and self.link.state == "OPEN":
            self.link.write(self.mav.shell_close())
        self.mav = None

    def cfg_cancel(self):
        if self.session_started:
            keep = self.s["fullscreen"]             # CANCEL undoes line settings, not F11
            self.s.update(self.cfg_snapshot)
            self.s["fullscreen"] = keep
            self.enter_terminal(reopen=False)
            return True
        return False                        # nothing to go back to: quit (like PuTTY)

    def cfg_key(self, e):
        k = e.key
        shift = e.mod & pygame.KMOD_SHIFT
        if k == pygame.K_F3 or ((e.mod & pygame.KMOD_CTRL) and shift and k == pygame.K_n):
            self.new_session(); return True
        if k == pygame.K_F11 or (k == pygame.K_RETURN and e.mod & pygame.KMOD_ALT):
            self.s["fullscreen"] = not self.s["fullscreen"]
            save_settings(self.s)
            self.cfg_window()
            return True
        if e.mod & pygame.KMOD_CTRL and k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.step_scale(+1); return True
        if e.mod & pygame.KMOD_CTRL and k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.step_scale(-1); return True
        if e.mod & pygame.KMOD_CTRL and k in (pygame.K_0, pygame.K_KP0):
            self.scale_reset(); return True
        if k == pygame.K_ESCAPE:
            return self.cfg_cancel()
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.focus == "buttons" and self.button == BTN_CANCEL:
                return self.cfg_cancel()
            self.cfg_open("MAVLINK" if (self.focus == "buttons" and self.button == BTN_MAV) else "RAW")
            return True
        if k == pygame.K_p and not (self.focus == "fields" and FIELDS[self.field] == "BAUD RATE"):
            self.eat_shortcut_text()
            self.cfg_open("SHELL")
            return True
        if k == pygame.K_m and not (self.focus == "fields" and FIELDS[self.field] == "BAUD RATE"):
            self.eat_shortcut_text()
            self.focus, self.button = "buttons", BTN_MAV
            self.cfg_open("MAVLINK")
            return True
        if k == pygame.K_TAB:
            self.commit_baud()
            order = ["ports", "fields", "buttons"]
            self.focus = order[(order.index(self.focus) + (-1 if shift else 1)) % 3]
            return True
        if (k == pygame.K_F5 or k == pygame.K_r) and not (self.focus == "fields" and FIELDS[self.field] == "BAUD RATE"):
            self.refresh_ports(manual=True)
            return True
        if self.focus == "ports":
            if k == pygame.K_UP:
                self.port_sel = max(0, self.port_sel - 1)
            elif k == pygame.K_DOWN:
                self.port_sel = max(0, min(len(self.ports) - 1, self.port_sel + 1))
            elif k == pygame.K_RIGHT:
                self.focus = "fields"
        elif self.focus == "fields":
            name = FIELDS[self.field]
            if k == pygame.K_UP:
                self.commit_baud(); self.field = (self.field - 1) % len(FIELDS)
            elif k == pygame.K_DOWN:
                self.commit_baud(); self.field = (self.field + 1) % len(FIELDS)
            elif k == pygame.K_LEFT:
                self.cycle(name, -1)
            elif k == pygame.K_RIGHT:
                self.cycle(name, 1)
            elif name == "BAUD RATE" and k == pygame.K_BACKSPACE:
                self.baud_edit = self.baud_edit[:-1]
            elif name == "BAUD RATE" and e.unicode and e.unicode.isdigit() and len(self.baud_edit) < 8:
                self.baud_edit += e.unicode
        else:
            if k in (pygame.K_LEFT, pygame.K_RIGHT):
                i = BTN_ORDER.index(self.button) + (1 if k == pygame.K_RIGHT else -1)
                self.button = BTN_ORDER[i % len(BTN_ORDER)]
        return True

    def cfg_click(self, pos, button):
        x, y = self.to_canvas(pos)
        now = time.time()
        if self.r_ports.collidepoint(x, y):
            self.focus = "ports"
            i = self.port_scroll + (y - self.r_ports.y - 14) // 12
            if 0 <= i < len(self.ports) and y >= self.r_ports.y + 14:
                double = self.last_click[0] == i and now - self.last_click[1] < 0.4
                self.port_sel = i
                self.last_click = (i, now)
                if double:
                    self.cfg_open()
            return True
        if self.r_fields.collidepoint(x, y):
            self.commit_baud()
            self.focus = "fields"
            i = (y - self.r_fields.y - 2) // 12
            if 0 <= i < len(FIELDS):
                self.field = i
                if x > self.r_fields.x + 96:
                    self.cycle(FIELDS[i], -1 if button == 3 or x < self.r_fields.x + 112 else 1)
            return True
        if self.r_refresh.collidepoint(x, y):
            self.refresh_ports(manual=True)
            return True
        if self.r_open.collidepoint(x, y):
            self.focus, self.button = "buttons", BTN_OPEN
            self.cfg_open()
            return True
        if self.r_pwsh.collidepoint(x, y):
            self.cfg_open("SHELL")
            return True
        if self.r_mav.collidepoint(x, y):
            self.focus, self.button = "buttons", BTN_MAV
            self.cfg_open("MAVLINK")
            return True
        if self.r_cancel.collidepoint(x, y):
            self.focus, self.button = "buttons", BTN_CANCEL
            return self.cfg_cancel()
        return True

    def draw_config(self, t):
        c, w, h = self.canvas, self.cw, self.ch
        self.begin_frame()
        if self.s["rain"]:
            self.draw_rain(c, t, pygame.Rect(0, 15, w, h - 15))
        panel = pygame.Surface(self.r_frame.size, pygame.SRCALPHA)
        panel.fill((5, 9, 12, 200))
        c.blit(panel, self.r_frame.topleft)
        # title bar
        c.fill(PANEL, (0, 0, w, 15))
        c.fill(STEEL_DIM, (0, 15, w, 1))
        x = self.draw_logo(6, 15, t)
        self.r_pwsh.x = pix_text(c, "SESSION CONFIGURATION", x + 12, 4, WHITE) + 6
        self.draw_pwsh_button(t)
        clock = datetime.now().strftime("%H:%M:%S")
        pix_text(c, clock, w - 8 - pix_width(clock), 4, STEEL)
        self.neon_frame(self.r_frame)
        # --- serial line list
        r = self.r_ports
        focus = self.focus == "ports"
        pix_text(c, "SERIAL LINE", r.x, r.y - 12, NEON_CORE if focus else STEEL)
        if focus:
            pix_text(self.neon, "SERIAL LINE", r.x, r.y - 12, NEON)
        c.fill((6, 10, 14), r)
        pygame.draw.rect(c, NEON_DIM if focus else STEEL_DIM, r, 1)
        pix_text(c, "PORT    DEVICE", r.x + 4, r.y + 4, STEEL)
        visible = (r.h - 16) // 12
        self.port_scroll = max(0, min(self.port_sel - visible + 1, len(self.ports) - visible)) if self.port_sel >= visible else 0
        for row in range(visible):
            i = self.port_scroll + row
            if i >= len(self.ports):
                if i == 0:
                    pix_text(c, "NO SERIAL PORTS", r.x + 7, r.y + 15, STEEL_DIM)
                break
            dev, desc = self.ports[i]
            y = r.y + 15 + row * 12
            sel = i == self.port_sel
            if sel:
                c.fill(HILITE, (r.x + 2, y - 2, r.w - 4, 11))
                c.fill(NEON_CORE, (r.x + 2, y - 2, 2, 11))
                self.neon.fill(NEON, (r.x + 2, y - 2, 2, 11))
            name = port_name(dev)
            pix_text(c, name, r.x + 7, y, WHITE if sel else FG)
            pix_text(c, pix_fit(desc, r.w - 58), r.x + 52, y, CYAN if sel else STEEL)
        pix_text(c, "AUTO SCAN 2S   R/F5 REFRESH", r.x, r.bottom + 4, STEEL_DIM)
        pix_text(c, "DOUBLE CLICK A PORT TO OPEN", r.x, r.bottom + 14, STEEL_DIM)
        pix_text(c, "CTRL +/- UI SCALE  F11 FULLSCREEN", r.x, r.bottom + 24, STEEL_DIM)
        # refresh button (top right of the list)
        b = self.r_refresh
        hot = time.time() < self.refresh_flash
        c.fill((12, 40, 20) if hot else (8, 18, 13), b)
        pygame.draw.rect(c, NEON_CORE if hot else NEON_DIM, b, 1)
        pygame.draw.rect(self.neon, NEON if hot else NEON_DIM, b, 1)
        self.neon_text("REFRESH", b.centerx - pix_width("REFRESH") // 2, b.y + 3)
        # --- settings
        r = self.r_fields
        focus = self.focus == "fields"
        pix_text(c, "LINE SETTINGS", r.x, r.y - 12, NEON_CORE if focus else STEEL)
        if focus:
            pix_text(self.neon, "LINE SETTINGS", r.x, r.y - 12, NEON)
        c.fill((6, 10, 14), r)
        pygame.draw.rect(c, NEON_DIM if focus else STEEL_DIM, r, 1)
        for i, name in enumerate(FIELDS):
            y = r.y + 4 + i * 12
            sel = focus and i == self.field
            if sel:
                c.fill(HILITE, (r.x + 2, y - 2, r.w - 4, 11))
            pix_text(c, name, r.x + 6, y, WHITE if sel else STEEL)
            val = self.field_value(name)
            colr = WHITE if sel else (CYAN if i < 5 else FG)
            pix_text(c, "<", r.x + 104, y, NEON_CORE if sel else STEEL_DIM)
            pix_text(c, val, r.x + 114, y, colr)
            pix_text(c, ">", r.right - 10, y, NEON_CORE if sel else STEEL_DIM)
            if sel:
                pix_text(self.neon, "<", r.x + 104, y, NEON)
                pix_text(self.neon, ">", r.right - 10, y, NEON)
        # --- summary + error
        dev = self.ports[self.port_sel][0] if self.ports else "-"
        summary = "%s  %d %s  FLOW %s" % (port_name(dev), self.s["baud"], frame_format(self.s), self.s["flow"])
        fx, fb = self.r_frame.x + 10, self.r_frame.bottom
        pix_text(c, summary, fx, fb - 62, CYAN)
        if self.cfg_error:
            pix_text(c, pix_fit("ERROR: " + self.cfg_error, self.r_frame.w - 24), fx, fb - 48, RED)
        elif time.time() < self.toast_until:
            pix_text(c, self.toast, fx, fb - 48, YELLOW)
        # --- buttons
        self.draw_mav_button(c, t)
        for idx, (rect, label) in enumerate(((self.r_open, "OPEN"), (self.r_cancel, "CANCEL"))):
            focused = self.focus == "buttons" and self.button == idx
            if idx == 0:
                c.fill(BTN_ON if focused else (8, 24, 14), rect)
                pygame.draw.rect(self.neon, NEON, rect, 1)
                pygame.draw.rect(c, NEON_CORE if focused else NEON, rect, 1)
                tx = rect.centerx - pix_width(label) // 2
                self.neon_text(label, tx, rect.y + 5)
            else:
                c.fill((20, 26, 34) if focused else (12, 16, 22), rect)
                pygame.draw.rect(c, WHITE if focused else STEEL, rect, 1)
                pix_text(c, label, rect.centerx - pix_width(label) // 2, rect.y + 5, WHITE if focused else STEEL)
        self.apply_glow(t)

    def draw_drone(self, x, y, t):
        """Pixel-art quadcopter: neon rotors, steel arms, blinking flight-controller LED."""
        c, nl = self.canvas, self.neon
        led = CYAN if int(t * 2) % 2 == 0 else RED
        for yy, row in enumerate(DRONE_ICON):
            for xx, p in enumerate(row):
                pos = (x + xx, y + yy)
                if p == "R":
                    c.set_at(pos, NEON_DIM)
                    nl.set_at(pos, NEON_DIM)
                elif p == "P":
                    c.set_at(pos, WHITE)
                elif p == "A":
                    c.set_at(pos, STEEL)
                elif p == "B":
                    c.set_at(pos, (150, 165, 180))
                elif p == "C":
                    c.set_at(pos, led)
                    nl.set_at(pos, led)
        step = int(t * 14)
        for rx, ry, d in ROTORS:                       # bright blade pair sweeping round each ring
            for k in (0, 4):
                px, py = ROTOR_RING[(step * d + k) % 8]
                c.set_at((x + rx + px, y + ry + py), NEON_CORE)
                nl.set_at((x + rx + px, y + ry + py), NEON)

    def draw_pwsh(self, x, y, t):
        """Pixel-art PowerShell tile in the drone icon's style: neon frame with a sweeping
        highlight, white prompt chevron, blinking cyan cursor."""
        c, nl = self.canvas, self.neon
        cur = int(t * 2) % 2 == 0
        for yy, row in enumerate(PWSH_ICON):
            for xx, p in enumerate(row):
                pos = (x + xx, y + yy)
                if p == "R":
                    c.set_at(pos, NEON_DIM)
                    nl.set_at(pos, NEON_DIM)
                elif p == "B":
                    c.set_at(pos, (16, 34, 58))
                elif p == "P":
                    c.set_at(pos, WHITE)
                elif p == "C":
                    c.set_at(pos, CYAN if cur else (16, 34, 58))
                    if cur:
                        nl.set_at(pos, CYAN)
        n = len(PWSH_RING)
        step = int(t * 14)
        for k in (0, n // 2):                          # bright pair running round the frame
            for tail in (0, 1):
                px, py = PWSH_RING[(step + k - tail) % n]
                c.set_at((x + px, y + py), NEON_CORE if tail == 0 else NEON)
                nl.set_at((x + px, y + py), NEON)

    def draw_pwsh_button(self, t):
        r = self.r_pwsh                                # bare icon, no tile / frame
        self.draw_pwsh(r.x + 3, r.y + (r.h - len(PWSH_ICON)) // 2, t)

    def draw_mav_button(self, c, t):
        r = self.r_mav
        focused = self.focus == "buttons" and self.button == BTN_MAV
        c.fill(BTN_ON if focused else (8, 24, 14), r)
        pygame.draw.rect(self.neon, NEON, r, 1)
        pygame.draw.rect(c, NEON_CORE if focused else NEON, r, 1)
        iy = r.y + (r.h - len(DRONE_ICON)) // 2
        self.draw_drone(r.x + 7, iy, t)
        tx = r.x + 7 + len(DRONE_ICON[0]) + 5
        pix_text(self.neon, MAV_LABEL, tx, r.y + 5, NEON, case=True)
        pix_text(c, MAV_LABEL, tx, r.y + 5, NEON_CORE, case=True)

    # ================================================================ terminal window
    def enter_terminal(self, reopen=False):
        self.mode = "term"
        self.next_claude_check = 0.0               # back from F2: pick the Claude colours up again
        self.no_repeat = set(getattr(self, "held", ()))
        pygame.display.set_caption(APP + (" - MAVLink Console (nsh)" if self.mav else ""))
        if self.s["fullscreen"]:
            self.win = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            self.fs_size = self.win.get_size()
        else:
            self.win = pygame.display.set_mode(tuple(self.s["window"]), pygame.RESIZABLE)
        self.term_layout()
        if self.vt is None:
            self.vt = TermScreen(self.cols, self.rows, history=5000, ratio=0.12)
            self.stream = pyte.Stream(self.vt)
            # answer terminal queries (cursor position etc.) from the local shell's pseudo console
            self.vt.write_process_input = lambda d: (self.link.write(d.encode()) if is_shell(self.s["port"]) else 0)
        else:
            self.vt.resize(self.rows, self.cols)
        self.link.resize(self.rows, self.cols)
        self.apply_vt_modes()
        self.reset_decoder()
        self.session_started = True
        pygame.key.start_text_input()

    def clear_terminal(self):
        """Empty screen, scrollback and HEX log (F7, and OPEN from the configuration window)."""
        self.vt.reset()
        self.apply_vt_modes()
        self.hex.clear()
        self.hex_scroll = 0
        self.sel = None
        self.composing = ""

    def term_layout(self):
        W, H = self.win.get_size()
        sc = self.sc
        self.set_canvas(max(200, W // sc), max(140, H // sc))
        self.term_rect = pygame.Rect(PAD + 3, TOOL_H + 5, self.cw - 2 * (PAD + 3),
                                     self.ch - TOOL_H - HINT_H - 10)
        self.cols = max(20, self.term_rect.w // self.cell_w)
        self.rows = max(5, self.term_rect.h // self.cell_h)
        self.term_panel = pygame.Surface(self.term_rect.size, pygame.SRCALPHA)
        self.term_panel.fill(TERM_BG)

    def max_window_scale(self):
        """Largest scale at which the fixed-size configuration window still fits on the desktop."""
        try:
            dw, dh = pygame.display.get_desktop_sizes()[0]
        except Exception:
            return 4
        return max(1, min(4, dw // CFG_SIZE[0], (dh - 60) // CFG_SIZE[1]))

    def step_scale(self, step):
        """UI scale (Ctrl +/- or the F4 popup): windowed changes the window scale, fullscreen its
        own remembered scale (AUTO fits it to the screen height). Works in both windows."""
        if self.s["fullscreen"]:
            self.s["fs_scale"] = max(1, min(6, self.sc + step))
        else:
            top = self.max_window_scale() if self.mode == "config" else 4
            self.s["scale"] = max(1, min(top, self.sc + step))
        self.apply_scale()

    def scale_reset(self):
        """Back to the default UI scale: 2x in a window, AUTO in fullscreen."""
        self.s["scale"], self.s["fs_scale"] = DEFAULTS["scale"], DEFAULTS["fs_scale"]
        self.apply_scale()

    def apply_scale(self):
        save_settings(self.s)
        if self.mode == "config":
            self.cfg_window()
        else:
            self.term_relayout()
        self.notify("UI SCALE %s" % self.scale_label())

    def scale_label(self):
        if self.s["fullscreen"] and not int(self.s.get("fs_scale", 0)):
            return "AUTO %dX" % self.sc
        return "%dX" % self.sc

    def term_relayout(self):
        self.term_layout()
        self.vt.resize(self.rows, self.cols)
        self.link.resize(self.rows, self.cols)
        self.sel = None

    def apply_vt_modes(self):
        if self.vt is None:
            return
        if self.s["implicit_cr"] and not is_shell(self.s["port"]):
            self.vt.set_mode(pyte.modes.LNM)
        else:
            self.vt.reset_mode(pyte.modes.LNM)

    def reset_decoder(self):
        self.csi_filter = CsiFilter()
        enc = {"UTF-8": "utf-8", "CP949": "cp949", "ASCII": "latin-1"}[self.s["encoding"]]
        if is_shell(self.s["port"]):
            enc = "utf-8"                               # ConPTY always speaks UTF-8
        self.decoder = codecs.getincrementaldecoder(enc)(errors="replace")
        self.encoding = enc

    def connect(self):
        save_settings(self.s)
        self.reset_decoder()
        self.apply_vt_modes()
        ok = self.link.open(self.s)
        self.want_open = ok or self.s["reconnect"]
        self.notify("OPENED %s" % port_name(self.s["port"]) if ok else
                    ("OPEN FAILED - RETRYING" if self.want_open else "OPEN FAILED"))
        self.next_retry = time.time() + 1.0
        if ok:
            self.mav_start()

    def disconnect(self):
        self.want_open = False
        if self.mav is not None and self.link.state == "OPEN":
            self.link.write(self.mav.shell_close())
        self.link.close()
        self.link.state = "CLOSED"
        self.notify("DISCONNECTED")

    def send(self, data):
        if not data:
            return
        # A plain keystroke changes nothing on screen until the device echoes it, so the main loop
        # skips the redraw (quiet_send) and draws the moment the echo arrives -> lower latency.
        self.quiet_send = (self.scrolled_lines() == 0 and self.sel is None and self.s["view"] == "TEXT"
                           and not (self.local_echo() and self.mav is None) and self.link.state == "OPEN")
        self.to_bottom()
        self.sel = None
        if self.mav is not None:                       # nsh input goes inside SERIAL_CONTROL frames
            if self.link.write(self.mav.shell_write(data)):
                self.bin_tx += len(data)
                self.hex.add("TX", data, time.time())
                if self.log:
                    self.log.write(data)
            elif self.link.state != "OPEN":
                self.notify("NOT CONNECTED - F5 CONNECT / F2 CONFIG")
            return
        n = self.link.write(data)
        if n:
            self.bin_tx += n
            self.hex.add("TX", data[:n], time.time())
            if self.log:
                self.log.write(data[:n])
            if self.local_echo() and self.vt is not None and self.mav is None:
                self.stream.feed(bytes(data[:n]).decode(self.encoding, "replace"))
        elif self.link.state != "OPEN":
            self.notify("NOT CONNECTED - F5 CONNECT / F2 CONFIG")

    def send_text(self, text):
        self.send(text.encode(self.encoding, errors="replace"))

    def local_echo(self):
        return self.s["echo"] and not is_shell(self.s["port"])   # the shell echoes by itself

    def enter_bytes(self):
        if is_shell(self.s["port"]):
            return b"\r"
        if self.mav is not None:
            return b"\n"                                # nsh line ending (same as QGC)
        return {"CR": b"\r", "LF": b"\n", "CR+LF": b"\r\n"}[self.s["enter"]]

    def set_theme(self, name):
        """Start a smooth cross-fade from whatever is on screen now (even mid-fade) to the theme."""
        global THEME, THEME_FROM, THEME_MIX
        if name == THEME:
            return
        THEME_FROM, THEME, THEME_MIX = THEME, name, 0.0
        self.theme_fade = (current_colors(), time.time())

    def step_theme(self, now):
        """Advance a running colour fade by one frame (smoothstep ease in / out)."""
        fade = getattr(self, "theme_fade", None)
        if not fade:
            return
        global THEME_MIX
        start, t0 = fade
        p = min(1.0, (now - t0) / THEME_FADE)
        e = p * p * (3 - 2 * p)
        blend_colors(start, THEMES[THEME], e)
        THEME_MIX = e
        step = int(e * 16)                         # matrix-rain letters re-rendered in 16 steps
        if step != getattr(self, "_rain_step", None):
            self._rain_step = step
            self.rain_cache = {}
        self._halo = None
        if hasattr(self, "term_panel"):
            self.term_panel.fill(TERM_BG)
        if p >= 1.0:
            self.theme_fade = None

    def update_claude_theme(self, now):
        """AI CLI colours while `codex` or `claude` runs in the PowerShell terminal (checked twice a
        second); NeoTerm green again when it exits (Ctrl+C, /exit) or in the configuration window."""
        if now < self.next_claude_check:
            return False
        self.next_claude_check = now + 0.5
        want = "GREEN"
        if self.mode == "term" and self.link.state == "OPEN" and isinstance(self.link.ser, ShellPort):
            running = self.link.ser.running()
            want = next((theme for exe, theme in AGENT_THEMES if exe in running), "GREEN")
        self.agent_theme = want
        self.claude_on = want == "CLAUDE"
        if want != THEME:
            self.set_theme(want)
            return True
        return False
        self.next_claude_check = now + 0.5
        on = False
        if self.mode == "term" and self.link.state == "OPEN" and isinstance(self.link.ser, ShellPort):
            on = "claude.exe" in self.link.ser.running()
        self.claude_on = on
        want = "CLAUDE" if on else "GREEN"
        if want != THEME:
            self.set_theme(want)
            return True
        return False

    def pump_serial(self):
        """Drain received bytes into the terminal. Returns True when something arrived."""
        now = time.time()
        got = self.update_claude_theme(now)
        try:
            while True:
                data = self.link.rx.get_nowait()
                got = True
                self.bin_rx += len(data)
                if self.mav is not None:
                    had = self.mav.alive(now)
                    data = self.mav.feed(data, now)
                    if not had and self.mav.alive(now) and self.vt is not None:
                        sysid, compid = self.mav.vehicle
                        self.stream.feed("\x1b[32m[MAVLINK] PX4 heartbeat  SYS %d  COMP %d  - nsh shell\x1b[0m\r\n"
                                         % (sysid, compid))
                        if self.mav.greeted:            # vehicle came back (reboot / link loss): new shell
                            self.link.write(self.mav.shell_write(b"\n"))
                    if not data:
                        continue
                self.hex.add("RX", data, now)
                if self.log:
                    self.log.write(data)
                if self.vt is not None:
                    text = self.csi_filter(self.decoder.decode(bytes(data)))
                    if text:
                        self.stream.feed(text)
        except queue.Empty:
            pass
        self.mav_tick(now)
        if self.link.exited and self.link.rx.empty():
            self.link.exited = False
            self.want_open = False
            self.link.close()
            if self.vt is not None:
                self.stream.feed("\r\n\x1b[33m[POWERSHELL] exited - F5 to start a new shell\x1b[0m\r\n")
            self.notify("POWERSHELL EXITED")
            got = True
        if (self.link.state == "ERROR" and self.want_open and self.s["reconnect"]
                and self.session_started and now >= self.next_retry):
            self.next_retry = now + 1.0
            if self.link.open(self.s):
                self.notify("PORT BACK - REOPENED")
                if self.mav is not None:
                    self.mav_start()
        while now - self.bin_t >= RATE_BIN:
            self.rates.append((self.bin_rx, self.bin_tx))
            self.bins += 1
            last = list(self.rates)[-int(round(1 / RATE_BIN)):]
            self.rate_rx = sum(r for r, _ in last)            # bytes in the last second
            self.rate_tx = sum(x for _, x in last)
            self.bin_rx = self.bin_tx = 0
            self.bin_t += RATE_BIN
            if now - self.bin_t > 2:                          # long stall: do not replay
                self.bin_t = now
        return got

    def toggle_log(self):
        if self.log:
            self.log.close()
            self.log = None
            self.notify("LOG CLOSED")
            return
        d = os.path.join(os.path.expanduser("~"), "Documents", APP)
        os.makedirs(d, exist_ok=True)
        self.log = open(os.path.join(d, "log_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M%S")), "ab")
        self.notify("LOGGING TO DOCUMENTS/NEOTERM")

    def to_bottom(self):
        if self.vt is None:
            return
        h = self.vt.history
        guard = 0
        while h.position < h.size and guard < 10000:
            before = h.position
            self.vt.next_page()
            guard += 1
            if h.position == before:
                break
        self.hex_scroll = 0

    def scrolled_lines(self):
        if self.s["view"] == "HEX":
            return self.hex_scroll
        h = self.vt.history
        return max(0, h.size - h.position)

    def scroll(self, up, amount=1):
        if self.s["view"] == "HEX":
            self.hex_scroll = max(0, min(max(0, len(self.hex.lines) - 1),
                                         self.hex_scroll + (3 * amount if up else -3 * amount)))
        else:
            for _ in range(amount):
                (self.vt.prev_page if up else self.vt.next_page)()

    def mouse_tracking(self):
        """True when the program in the terminal asked for mouse events (ESC[?1000h / 1002h / 1003h),
        as full-screen TUIs like Claude Code and Codex do."""
        m = self.vt.mode if self.vt is not None else ()
        return any((n << 5) in m for n in (1000, 1002, 1003))

    def mouse_wheel_to_app(self, up, pos):
        """Forward a wheel notch to a mouse-tracking program (it scrolls its own view, e.g. the
        Claude / Codex conversation) instead of scrolling NeoTerm's scrollback. Returns True if sent."""
        if (self.link.state != "OPEN" or self.s["view"] != "TEXT" or self.scrolled_lines()
                or not self.mouse_tracking()):
            return False
        col, row = self.cell_at(pos)
        btn = 64 if up else 65
        if (1006 << 5) in self.vt.mode:                    # SGR encoding (all modern TUIs)
            seq = "\x1b[<%d;%d;%dM" % (btn, col + 1, row + 1)
        else:                                              # classic X10 encoding
            seq = "\x1b[M" + chr(32 + btn) + chr(33 + min(col, 222)) + chr(33 + min(row, 222))
        n = self.link.write(seq.encode("latin-1"))
        if n:
            self.bin_tx += n
            self.hex.add("TX", seq.encode("latin-1"), time.time())
        return bool(n)

    def cell_at(self, pos):
        x, y = self.to_canvas(pos)
        x -= self.term_rect.x
        y -= self.term_rect.y
        return (max(0, min(self.cols - 1, x // self.cell_w)), max(0, min(self.rows - 1, y // self.cell_h)))

    def hex_visible(self):
        lines = self.hex.lines
        end = len(lines) - self.hex_scroll
        start = max(0, end - self.rows)
        return [lines[i] for i in range(start, max(start, end))]

    def hex_format(self, line):
        d, t0, data, _ = line
        ts = datetime.fromtimestamp(t0).strftime("%H:%M:%S.") + "%03d" % int((t0 % 1) * 1000)
        return "%s %s  %-47s  %s" % (ts, d, " ".join("%02X" % b for b in data),
                                    "".join(chr(b) if 32 <= b < 127 else "." for b in data))

    def selection_text(self):
        if not self.sel:
            return ""
        (c0, r0), (c1, r1) = sorted(self.sel, key=lambda p: (p[1], p[0]))
        if self.s["view"] == "HEX":
            return "\r\n".join(self.hex_format(l) for l in self.hex_visible()[r0:r1 + 1])
        out = []
        for r in range(r0, r1 + 1):
            line = self.vt.buffer[r]
            a = c0 if r == r0 else 0
            b = c1 if r == r1 else self.cols - 1
            out.append("".join(line[x].data for x in range(a, b + 1)).rstrip())
        return "\r\n".join(out)

    def in_selection(self, col, row):
        if not self.sel:
            return False
        (c0, r0), (c1, r1) = sorted(self.sel, key=lambda p: (p[1], p[0]))
        if row < r0 or row > r1:
            return False
        if self.s["view"] == "HEX" or r0 < row < r1:
            return True
        if r0 == r1:
            return c0 <= col <= c1
        return col >= c0 if row == r0 else col <= c1

    def copy(self):
        t = self.selection_text()
        if t and clip_set(t):
            self.notify("COPIED %d CHARS" % len(t))
        self.sel = None

    def paste(self):
        t = clip_get()
        if t:
            t = t.replace("\r\n", "\n").replace("\r", "\n")
            self.send_text(t.replace("\n", self.enter_bytes().decode()))

    def new_session(self):
        """Open another NeoTerm window (starts at its session configuration), like PuTTY's
        New Session. Each window is its own process, so sessions run side by side."""
        save_settings(self.s)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, os.path.abspath(__file__)]
        env = dict(os.environ)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"     # the exe child unpacks on its own, outliving us
        try:
            subprocess.Popen(cmd, cwd=os.path.dirname(cmd[-1]), close_fds=True, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            self.notify("NEW SESSION WINDOW")
        except OSError as e:
            self.notify("NEW SESSION FAILED: %s" % e)

    def term_key(self, e):
        k, mod = e.key, e.mod
        ctrl, shift, alt = mod & pygame.KMOD_CTRL, mod & pygame.KMOD_SHIFT, mod & pygame.KMOD_ALT
        if k == pygame.K_F3 or (ctrl and shift and k == pygame.K_n):
            self.new_session(); return True
        if self.textset:
            return self.textset_key(k)
        if k == pygame.K_F4:
            self.open_textset(); return True
        if k == pygame.K_F1:
            self.help = not self.help; return True
        if k == pygame.K_F2:
            self.help = False; self.enter_config(); return True
        if k == pygame.K_F5:
            (self.disconnect if (self.link.state == "OPEN" or self.want_open) else self.connect)(); return True
        if k == pygame.K_F6:
            self.s["view"] = "HEX" if self.s["view"] == "TEXT" else "TEXT"; self.sel = None
            save_settings(self.s); return True
        if k == pygame.K_F7:
            self.clear_terminal(); return True
        if k == pygame.K_F9:
            self.toggle_log(); return True
        if k == pygame.K_F11 or (k == pygame.K_RETURN and alt):
            self.s["fullscreen"] = not self.s["fullscreen"]; save_settings(self.s); self.enter_terminal(); return True
        if ctrl and k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.step_scale(+1); return True
        if ctrl and k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.step_scale(-1); return True
        if ctrl and k in (pygame.K_0, pygame.K_KP0):
            self.scale_reset(); return True
        # Ctrl+C copies when text is selected (like Windows Terminal) and otherwise still sends
        # ^C (interrupt) to the device / shell; Ctrl+V pastes. Ctrl+Shift+C/V always copy/paste.
        if ctrl and k == pygame.K_c and (shift or (self.sel and self.sel[0] != self.sel[1])):
            self.copy(); return True
        if ctrl and k == pygame.K_v:
            self.paste(); return True
        if shift and k == pygame.K_PAGEUP:
            self.scroll(True, 4); return True
        if shift and k == pygame.K_PAGEDOWN:
            self.scroll(False, 4); return True
        if self.help:
            if k == pygame.K_ESCAPE:
                self.help = False
            return True
        if self.composing:
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.send(self.enter_bytes()); return True
        if k == pygame.K_BACKSPACE:
            shell = is_shell(self.s["port"])          # PSReadLine: DEL = backspace, ^H = delete word
            self.send(b"\x7f" if shell or self.s["backspace"] == "^?" else b"\x08"); return True
        if k in SPECIAL:
            self.send(SPECIAL[k]); return True
        if ctrl and pygame.K_a <= k <= pygame.K_z:
            self.send(bytes([k - pygame.K_a + 1])); return True
        if ctrl and k == pygame.K_SPACE:
            self.send(b"\x00"); return True
        return True

    # ---------------------------------------------------------------- terminal drawing
    def color(self, name, default, bold=False):
        if name == "default":
            return default
        if name in PALETTE:
            if bold and not name.startswith("bright") and ("bright" + name) in PALETTE:
                return PALETTE["bright" + name]
            return PALETTE[name]
        try:
            return (int(name[0:2], 16), int(name[2:4], 16), int(name[4:6], 16))
        except Exception:
            return default

    def has_glyph(self, font, ch):
        """False when the font would draw its 'missing glyph' box for ch."""
        k = (id(font), ch)
        r = self._has.get(k)
        if r is None:
            tk = (id(font), None)
            tofu = self._has.get(tk)
            if tofu is None:
                tofu = self._has[tk] = pygame.image.tobytes(font.render("￿", False, (255, 255, 255)), "RGB")
            r = self._has[k] = pygame.image.tobytes(font.render(ch, False, (255, 255, 255)), "RGB") != tofu
        return r

    def block_glyph(self, ch, color):
        """Block elements U+2580-U+259F drawn as exact cell fractions, so logos and bars built from
        them (Claude Code's banner, progress bars) join up seamlessly whatever the font."""
        sc = self.sc
        W, H = self.cell_w * sc, self.cell_h * sc
        g = pygame.Surface((W, H), pygame.SRCALPHA)
        o = ord(ch)
        quad = {0x2596: 4, 0x2597: 8, 0x2598: 1, 0x2599: 13, 0x259A: 9, 0x259B: 7, 0x259C: 11,
                0x259D: 2, 0x259E: 6, 0x259F: 14}
        hw, hh = W // 2, H // 2
        if o == 0x2580:                                   # upper half
            g.fill(color, (0, 0, W, hh))
        elif 0x2581 <= o <= 0x2588:                       # lower 1/8 .. full
            n = o - 0x2580
            g.fill(color, (0, H - H * n // 8, W, H * n // 8))
        elif 0x2589 <= o <= 0x258F:                       # left 7/8 .. 1/8
            n = 0x2590 - o
            g.fill(color, (0, 0, W * n // 8, H))
        elif o == 0x2590:                                 # right half
            g.fill(color, (hw, 0, W - hw, H))
        elif 0x2591 <= o <= 0x2593:                       # light / medium / dark shade
            g.fill(tuple(color[:3]) + ((o - 0x2590) * 64,), (0, 0, W, H))
        elif o == 0x2594:                                 # upper 1/8
            g.fill(color, (0, 0, W, max(1, H // 8)))
        elif o == 0x2595:                                 # right 1/8
            g.fill(color, (W - max(1, W // 8), 0, max(1, W // 8), H))
        elif o in quad:                                   # quadrants: 1 UL, 2 UR, 4 LL, 8 LR
            q = quad[o]
            for bit, r in ((1, (0, 0, hw, hh)), (2, (hw, 0, W - hw, hh)),
                           (4, (0, hh, hw, H - hh)), (8, (hw, hh, W - hw, H - hh))):
                if q & bit:
                    g.fill(color, r)
        return g

    def glyph(self, ch, color, bold=False, wide=False):
        """Terminal glyph for the HUD layer (window resolution), styled by the STYLE setting."""
        key = (ch, color, bold, wide)
        g = self.glyphs.get(key)
        if g is None:
            sc = self.sc
            cjk = wide or ord(ch) >= 0x1100
            try:
                style = self.text_style
                font = self.font_hi_w if cjk else (self.font_hi_b if bold else self.font_hi)
                if 0x2580 <= ord(ch) <= 0x259F:
                    g = self.block_glyph(ch, color)
                    if style == "led" and sc >= 2:
                        g.blit(self.led_mask(g.get_size()), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                elif not self.has_glyph(font, ch):
                    # not in the terminal font: try the other installed fonts, fitted to the cell
                    for alt in (self.font_hi_sym, self.font_hi_w, self.font_hi):
                        if alt is not font and self.has_glyph(alt, ch):
                            font = alt
                            break
                    g = font.render(ch, True, color)
                    cw, chh = self.cell_w * (2 if wide else 1) * sc, self.cell_h * sc
                    if g.get_width() > cw or g.get_height() > chh:
                        k = min(cw / g.get_width(), chh / g.get_height())
                        g = pygame.transform.smoothscale(g, (max(1, int(g.get_width() * k)),
                                                             max(1, int(g.get_height() * k))))
                elif style == "dot" and not cjk and sc >= 2:
                    # pixel-size dots (one canvas pixel), corners rounded by Scale2x
                    g = (self.font_b if bold else self.font).render(ch, False, color)
                    g = pygame.transform.scale2x(g)
                    if sc == 4:
                        g = pygame.transform.scale2x(g)
                    elif sc != 2:
                        g = pygame.transform.scale(g, (g.get_width() * sc // 2, g.get_height() * sc // 2))
                else:
                    g = font.render(ch, style == "smooth", color)
                    if style == "led" and sc >= 2:
                        # LED-matrix texture: dim one pixel of each 2x2 dot -> visible dot grid
                        g = g.convert_alpha() if pygame.display.get_surface() else g
                        mask = self.led_mask(g.get_size())
                        g.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            except Exception:
                g = self.font_hi.render("?", False, color)
            if len(self.glyphs) > 8000:
                self.glyphs.clear()
            self.glyphs[key] = g
        return g

    def led_mask(self, size):
        m = getattr(self, "_led_mask", None)
        if m is None or m.get_width() < size[0] or m.get_height() < size[1]:
            w, h = max(size[0], 64), max(size[1], 64)
            m = pygame.Surface((w, h), pygame.SRCALPHA)
            m.fill((255, 255, 255, 255))
            for yy in range(1, h, 2):
                m.fill((255, 255, 255, LED_GAP), (0, yy, w, 1))
            for xx in range(1, w, 2):
                for yy in range(0, h, 2):
                    m.set_at((xx, yy), (255, 255, 255, LED_GAP + (255 - LED_GAP) // 2))
            self._led_mask = m
        return m.subsurface((0, 0) + tuple(size))

    def put(self, ch, color, x, y, bold=False, wide=False):
        """Draw one terminal character cell (canvas coordinates) on the hi-res HUD."""
        sc = self.sc
        g = self.glyph(ch, color, bold, wide)
        cw = self.cell_w * (2 if wide else 1) * sc
        self.hud.blit(g, (x * sc + (cw - g.get_width()) // 2, y * sc + (self.cell_h * sc - g.get_height()) // 2))

    def draw_text_view(self, c, t):
        """Terminal text. Each screen row is rendered once into a cached hi-res strip (glyphs) plus
        a short list of canvas fills (backgrounds / underlines) and only re-rendered when pyte marks
        it dirty or the view changes, so an idle or slowly scrolling screen costs almost nothing."""
        vt = self.vt
        cw, chh = self.cell_w, self.cell_h
        ox, oy = self.term_rect.topleft
        sc = self.sc
        key = (self.cols, self.rows, sc, cw, chh, self.sel, self.scrolled_lines(), id(self.glyphs))
        cache = getattr(self, "_rows", None)
        shift, invalid = getattr(vt, "shift", 0), getattr(vt, "shift_invalid", False)
        vt.shift, vt.shift_invalid = 0, False
        if (cache is None or self._rows_key != key or len(cache) != self.rows or invalid
                or shift >= self.rows or (shift and self.scrolled_lines())):
            cache = self._rows = [None] * self.rows
            self._rows_key = key
            todo = range(self.rows)
        else:
            if shift:                                   # screen scrolled: move cached rows up
                cache[:] = cache[shift:] + [None] * shift
            todo = [r for r in vt.dirty if 0 <= r < self.rows] + [r for r in range(self.rows) if cache[r] is None]
        vt.dirty.clear()
        for row in set(todo):
            cache[row] = self.render_row(row)
        hud = self.hud
        for row in range(self.rows):
            strip, fills = cache[row]
            y = oy + row * chh
            for colr, x, w, yo, h in fills:
                c.fill(colr, (ox + x, y + yo, w, h))
            if strip is not None:
                hud.blit(strip, (ox * sc, y * sc))

        if self.link.state == "OPEN" and not vt.cursor.hidden and self.scrolled_lines() == 0:
            cx, cy = ox + vt.cursor.x * cw, oy + vt.cursor.y * chh
            sc = self.sc
            if self.composing:
                g = self.font_hi_w.render(self.composing, False, WHITE)
                self.hud.fill(SEL_BG, (cx * sc, cy * sc, g.get_width() + 2 * sc, chh * sc))
                self.hud.blit(g, (cx * sc + sc, cy * sc + (chh * sc - g.get_height()) // 2))
            elif int(t * 2) % 2 == 0:
                self.hud_fill(CYAN, (cx, cy, cw, chh))       # block cursor covers the glyph,
                under = vt.buffer[vt.cursor.y][vt.cursor.x].data
                if under.strip():                            # which is redrawn inverted
                    self.put(under, BG, cx, cy)
            else:
                self.hud_fill(CYAN, (cx, cy + chh - 2, cw, 2))
            try:
                sc = self.sc
                ox2, oy2 = self.offset()
                pygame.key.set_text_input_rect(pygame.Rect(ox2 + cx * sc, oy2 + (cy + chh) * sc, cw * sc, chh * sc))
            except Exception:
                pass

    def render_row(self, row):
        """One terminal row -> (hi-res glyph strip or None, [(color, x, w, y_off, h) canvas fills])."""
        vt = self.vt
        cw, chh, sc = self.cell_w, self.cell_h, self.sc
        line = vt.buffer[row]
        strip, fills = None, []
        # the glyph strip only spans up to the last visible character (most lines are short)
        last = max((k for k, ch in line.items() if k < self.cols and ch.data.strip()), default=-1)
        col = 0
        while col < self.cols:
            cell = line[col]
            ch = cell.data
            if ch == "":
                col += 1
                continue
            wide = len(ch) == 1 and ord(ch) >= 0x1100 and wcwidth(ch) == 2
            span = 2 if wide else 1
            fg = self.color(cell.fg, self.text_fg, cell.bold)
            bg = self.color(cell.bg, None)
            if cell.reverse:
                fg, bg = (bg or BG), fg
            if self.sel and self.in_selection(col, row):
                bg, fg = SEL_BG, WHITE
            x = col * cw
            if bg is not None:
                fills.append((bg, x, cw * span, 0, chh))
            if ch != " ":
                if strip is None:
                    strip = pygame.Surface((min(self.cols, last + 2) * cw * sc, chh * sc), pygame.SRCALPHA)
                g = self.glyph(ch, fg, cell.bold, wide)
                w = cw * span * sc
                strip.blit(g, (x * sc + (w - g.get_width()) // 2, (chh * sc - g.get_height()) // 2))
            if cell.underscore:
                fills.append((fg, x, cw * span, chh - 1, 1))
            col += span
        return strip, fills

    def draw_hex_view(self, c):
        per = 16 if self.cols >= 82 else 8
        self.hex.per_line = per
        cw, chh = self.cell_w, self.cell_h
        ox, oy = self.term_rect.topleft
        for row, line in enumerate(self.hex_visible()):
            y = oy + row * chh
            if self.in_selection(0, row):
                c.fill(SEL_BG, (ox, y, self.cols * cw, chh))
            d, t0, data, _ = line
            ts = datetime.fromtimestamp(t0).strftime("%H:%M:%S.") + "%03d " % int((t0 % 1) * 1000)
            main = CYAN if d == "RX" else AMBER
            x = ox
            for part, colr in ((ts, STEEL), (d + "  ", main)):
                for ch in part:
                    self.put(ch, colr, x, y); x += cw
            for i in range(per):
                if i < len(data):
                    for ch in "%02X" % data[i]:
                        self.put(ch, main, x, y); x += cw
                    x += cw
                else:
                    x += 3 * cw
            x += cw
            for b in data:
                ch = chr(b) if 32 <= b < 127 else "."
                self.put(ch, FG if ch != "." else STEEL_DIM, x, y); x += cw

    def link_status(self):
        """CONNECTED      the port is open and the other end has answered (data received; for the
                          MAVLink console a PX4 heartbeat; PowerShell as soon as it prints)
        CONNECTING     port open but nothing heard yet, or retrying after the port went away
        DISCONNECTED   closed by the user;  ERROR  open failed and auto reconnect is off"""
        st = self.link.state
        if st == "OPEN":
            heard = self.mav.alive() if self.mav is not None else self.link.got_data
            return "CONNECTED" if heard else "CONNECTING"
        if st == "ERROR":
            return "CONNECTING" if self.want_open else "ERROR"
        return "DISCONNECTED"

    def draw_connecting(self, label, x, y, ly, t):
        """Loading-bar style: grey letters turn green one by one from the left, then start over."""
        c = self.canvas
        period, hold = 1.4, 0.25                     # sweep time, pause with everything lit
        p = (t % (period + hold)) / period
        n = len(label)
        lit = min(n, int(p * (n + 1)))
        lamp = NEON_CORE if lit >= n else (NEON_DIM if lit else STEEL)
        c.fill(lamp, (x, ly, 4, 4))
        if lit >= n:
            self.neon.fill(NEON, (x - 1, ly - 1, 6, 6))
        x += 7
        for i, ch in enumerate(label):
            if i < lit:                                  # lit part glows; the leading letter is brightest
                pix_text(self.neon, ch, x, y, NEON)
                x = pix_text(c, ch, x, y, NEON_CORE if (i == lit - 1 or lit >= n) else tuple(int(v * 0.8) for v in NEON_CORE))
            else:
                x = pix_text(c, ch, x, y, STEEL)
        return x                                         # same advance as pix_text(label)

    def draw_toolbar(self, c, t):
        """Top bar. Everything is measured first; when the window is too narrow the least important
        items are dropped (graph, rates, clock, logo text, line format, nsh detail) so nothing
        overlaps, and the device description only fills the space that is left."""
        y = (TOOL_H - PIX_H) // 2
        ly = y + 1                                  # status lamp top
        W = self.cw
        c.fill(PANEL, (0, 0, W, TOOL_H))
        c.fill(STEEL_DIM, (0, TOOL_H, W, 1))
        state = self.link_status()
        label = {"CONNECTING": "CONNECTING..."}.get(state, state)
        lamp = {"CONNECTED": NEON_CORE, "ERROR": RED}.get(state, STEEL)
        port = port_name(self.s["port"]) or "-"
        sub = "LOCAL SHELL" if is_shell(self.s["port"]) else "%d %s" % (self.s["baud"], frame_format(self.s))
        if self.mav is not None:
            nsh = ("PX4 SYS %d" % self.mav.vehicle[0]) if self.mav.alive() else "NO HEARTBEAT"
        clock = datetime.now().strftime("%H:%M:%S")
        rx_txt, tx_txt = fmt_rate(self.rate_rx), fmt_rate(self.rate_tx)
        rates = "RX %s TX %s" % (rx_txt, tx_txt)
        icon_w = len(DRONE_ICON[0]) + 4
        width = {
            "logo_text": 4 + pix_width("NeoTerm", case=True),
            "sub": pix_width(sub) + 8,
            "mav_text": (pix_width("NSH") + 4 + pix_width(nsh) + 8) if self.mav is not None else 0,
            "clock": pix_width(clock) + 13,
            "graph": GRAPH_W + 10,
            "rates": pix_width("RX 000.0K TX 000.0K") + 8,       # fixed slot: no jumping
        }
        fixed = (4 + 13 + 6 + 7 + 7 + pix_width(label) + 8 + pix_width(port) + 6 + 7
                 + ((icon_w + 7) if self.mav is not None else 0))
        show = dict.fromkeys(width, True)
        for k in ("graph", "rates", "clock", "logo_text", "sub", "mav_text"):     # drop order
            if fixed + sum(width[n] for n in width if show[n]) <= W:
                break
            show[k] = False
        # ---- left side
        x = self.draw_logo(4, TOOL_H, t, show["logo_text"]) + 6
        c.fill(STEEL_DIM, (x, 3, 1, TOOL_H - 6)); x += 6
        if state == "CONNECTED":
            c.fill(NEON_CORE, (x, ly, 4, 4)); self.neon.fill(NEON, (x - 1, ly - 1, 6, 6))
            x = self.neon_text(label, x + 7, y) + 8
        elif state == "CONNECTING":
            x = self.draw_connecting(label, x, y, ly, t) + 8
        else:
            c.fill(lamp, (x, ly, 4, 4))
            x = pix_text(c, label, x + 7, y, lamp) + 8
        right_edge = W - 5
        if show["clock"]:
            right_edge -= width["clock"]
        if show["graph"]:
            right_edge -= width["graph"]
        if show["rates"]:
            right_edge -= width["rates"]
        room = right_edge - x - (show["sub"] and width["sub"]) - 13 \
            - ((icon_w + (width["mav_text"] if show["mav_text"] else 0)) if self.mav is not None else 0)
        x = pix_text(c, pix_fit(port, max(10, room)) if pix_width(port) > room else port, x, y, WHITE) + 6
        if show["sub"]:
            x = pix_text(c, sub, x, y, CYAN) + 8
        c.fill(STEEL_DIM, (x, 3, 1, TOOL_H - 6)); x += 6
        if self.mav is not None:
            self.draw_drone(x, (TOOL_H - len(DRONE_ICON)) // 2, t)
            x += icon_w
            if show["mav_text"]:
                if self.mav.alive():
                    x = self.neon_text("NSH", x, y) + 4
                    x = pix_text(c, nsh, x, y, WHITE) + 8
                else:
                    x = pix_text(c, "NSH", x, y, STEEL) + 4
                    x = pix_text(c, nsh, x, y, AMBER if int(t * 2) % 2 else STEEL) + 8
            c.fill(STEEL_DIM, (x, 3, 1, TOOL_H - 6)); x += 6
        # ---- right side, from the right edge inwards
        rx = W - 5
        if show["clock"]:
            rx -= pix_width(clock)
            pix_text(c, clock, rx, y, STEEL)
            rx -= 10
        if show["graph"]:
            rx -= GRAPH_W
            self.draw_graph(c, pygame.Rect(rx, 1, GRAPH_W, TOOL_H - 1), t)
            rx -= 8
        if show["rates"]:
            rx -= pix_width(rates)
            e = pix_text(c, "RX ", rx, y, STEEL)
            e = pix_text(c, rx_txt, e, y, CYAN)
            e = pix_text(c, " TX ", e, y, STEEL)
            pix_text(c, tx_txt, e, y, AMBER)
        desc = next((d for p, d in self.ports if p == self.s["port"]), "")
        free = right_edge - x - 8
        if desc and free > 20:
            pix_text(c, pix_fit(desc, free) if pix_width(desc) > free else desc, x, y, STEEL)

    def draw_graph(self, c, r, t):
        """CyberMon-style live waveform at window resolution: RX (cyan) above the axis, TX (amber)
        below, filled envelopes, oscillating carrier lines, scrolling grid and bright head dots."""
        sc = self.sc
        W, H = r.w * sc, r.h * sc
        g = pygame.Surface((W, H))
        g.fill((5, 9, 13))
        # grid: vertical time ticks scroll left with the data, horizontal amplitude lines
        cols, rows = 12, 4
        cwid = W / cols
        off = ((self.bins + (time.time() - self.bin_t) / RATE_BIN) / (RATE_HIST / cols) % 1.0) * cwid
        minor, major = (20, 34, 44), (34, 54, 68)
        x = W - 1 - off
        while x >= 0:
            pygame.draw.line(g, minor, (int(x), 0), (int(x), H))
            x -= cwid
        for k in range(rows + 1):
            y = min(H - 1, int(H * k / rows))
            pygame.draw.line(g, major if k in (0, rows) else minor, (0, y), (W, y))
        mid = H / 2
        half = H / 2 - 2
        data = list(self.rates)
        peak = max(max(a, b) for a, b in data)
        target = max(64.0, peak * 1.2)
        self.gscale += (target - self.gscale) * (0.25 if target > self.gscale else 0.04)
        s = self.gscale
        cnt = len(data)
        step = (W - 1) / (cnt - 1)
        ph0 = self.bins * 0.9
        env_r, env_t, car_r, car_t = [], [], [], []
        for i, (a, b) in enumerate(data):
            x = i * step
            er = min(1.0, a / s) * half
            et = min(1.0, b / s) * half
            env_r.append((x, mid - er))
            env_t.append((x, mid + et))
            osc = 0.55 + 0.45 * math.sin((i + ph0) * 0.9 - t * 9)
            car_r.append((x, mid - er * osc))
            car_t.append((x, mid + et * (1.45 - osc)))
        pygame.draw.polygon(g, (0, 40, 54), [(0, mid)] + env_r + [(W - 1, mid)])
        pygame.draw.polygon(g, (54, 34, 0), [(0, mid)] + env_t + [(W - 1, mid)])
        pygame.draw.line(g, (40, 64, 80), (0, int(mid)), (W, int(mid)))
        pygame.draw.aalines(g, (30, 95, 125), False, env_r)
        pygame.draw.aalines(g, (125, 85, 30), False, env_t)
        pygame.draw.aalines(g, CYAN, False, car_r)
        pygame.draw.aalines(g, AMBER, False, car_t)
        for (hx, hy), col in ((env_r[-1], CYAN), (env_t[-1], AMBER)):
            g.fill(WHITE, (int(hx) - 3, int(hy) - 1, 3, 3))
            pygame.draw.circle(g, col, (int(hx) - 2, int(hy)), 3, 1)
        pygame.draw.rect(g, (34, 54, 68), g.get_rect(), 1)
        self.hud.blit(g, (r.x * sc, r.y * sc))
        # soft glow of the carriers on the (low-res) neon layer
        for pts, col in ((car_r, (0, 70, 100)), (car_t, (100, 60, 0))):
            pygame.draw.lines(self.neon, col, False, [(r.x + px / sc, r.y + py / sc) for px, py in pts[::3]])

    def draw_hints(self, c):
        y = self.ch - HINT_H + 2
        c.fill(PANEL, (0, self.ch - HINT_H, self.cw, HINT_H))
        c.fill(STEEL_DIM, (0, self.ch - HINT_H - 1, self.cw, 1))
        items = [("F2", "CONFIG"), ("F3", "NEW SESSION"), ("F4", "DISPLAY"), ("F5", "DISCONNECT" if (self.link.state == "OPEN" or self.want_open) else "CONNECT"),
                 ("F6", self.s["view"]), ("F7", "CLEAR"), ("F9", "LOG ON" if self.log else "LOG"), ("F1", "HELP")]
        right = "RX %s  TX %s" % (fmt_bytes(self.link.rx_total), fmt_bytes(self.link.tx_total))
        if self.scrolled_lines():
            right = "SCROLL -%d   %s" % (self.scrolled_lines(), right)
        toast = time.time() < self.toast_until
        if toast:
            right = self.toast
        item_w = [pix_width(k) + 4 + pix_width(v) + 10 for k, v in items]
        limit = self.cw - 6 - pix_width(right) - 8
        if not toast and 6 + sum(item_w[:4]) > limit:
            right, limit = "", self.cw - 6                  # narrow: hints matter more than counters
        x = 6
        self.hint_rects = {}
        for (k, v), iw in zip(items, item_w):
            if x + iw - 10 > limit:
                break                                       # no room: drop the remaining hints
            x0 = x
            x = pix_text(c, k, x, y, NEON_CORE if k in ("F2", "F3", "F5") else STEEL)
            if k in ("F2", "F3", "F5"):
                pix_text(self.neon, k, x - pix_width(k), y, NEON)
            x = pix_text(c, v, x + 4, y, AMBER if (k == "F9" and self.log) else WHITE) + 10
            self.hint_rects[k] = pygame.Rect(x0 - 2, self.ch - HINT_H, x - x0 - 6, HINT_H)
        if right:
            if pix_width(right) > self.cw - 12:
                right = pix_fit(right, self.cw - 12)
            pix_text(c, right, self.cw - 6 - pix_width(right), y, YELLOW if toast else STEEL)

    def draw_help(self, c):
        rows = ["F1  HELP", "F2  SESSION CONFIGURATION", "F3  NEW SESSION WINDOW", "F4  DISPLAY SETTINGS (UI SCALE, FONT, SIZE)", "F5  CONNECT / DISCONNECT", "F6  TEXT / HEX VIEW",
                "F7  CLEAR SCREEN", "F9  LOG TO DOCUMENTS/NEOTERM", "F11 FULLSCREEN",
                "CTRL + / -  UI SCALE", "DRAG SELECT, RIGHT CLICK COPY/PASTE",
                "CTRL+C / V  COPY (SELECTION) / PASTE", "WHEEL, SHIFT+PGUP/DN  SCROLL",
                "SHIFT+WHEEL  SCROLLBACK WHILE A TUI USES THE MOUSE"]
        w = max(pix_width(x) for x in rows) + 20
        h = len(rows) * 10 + 22
        x, y = (self.cw - w) // 2, (self.ch - h) // 2
        c.fill(BOX_BG, (x, y, w, h))
        self.hud_fill(BOX_BG, (x + 1, y + 1, w - 2, h - 2))     # hide terminal text under the box
        pygame.draw.rect(c, NEON_CORE, (x, y, w, h), 1)
        pygame.draw.rect(self.neon, NEON, (x, y, w, h), 1)
        self.neon_text("// CONTROLS", x + 10, y + 6)
        ver = "NEOTERM V%s" % VERSION
        pix_text(c, ver, x + w - 10 - pix_width(ver), y + 6, STEEL_DIM)
        for i, row in enumerate(rows):
            pix_text(c, row, x + 10, y + 18 + i * 10, WHITE)

    # ---------------------------------------------------------------- text settings popup
    def open_textset(self):
        self.textset = not self.textset
        self.help = False
        self.sel = None

    def textset_change(self, row, step):
        _, key, opts = TEXT_ROWS[row]
        if key == "ui_scale":
            self.step_scale(step)
            return
        opts = opts()
        cur = self.s.get(key, DEFAULTS[key])
        i = opts.index(cur) if cur in opts else 0
        self.s[key] = opts[max(0, min(len(opts) - 1, i + step))] if key == "font_size" else opts[(i + step) % len(opts)]
        self.apply_text_settings()

    def apply_text_settings(self):
        """Fonts, cell size and terminal geometry follow the text settings at once (live preview)."""
        save_settings(self.s)
        self.load_fonts()
        if self.mode == "term":
            self.term_relayout()

    def textset_reset(self):
        for _, key, _ in TEXT_ROWS:
            if key != "ui_scale":
                self.s[key] = DEFAULTS[key]
        self.load_fonts()
        self.scale_reset()

    def textset_key(self, k):
        n = len(TEXT_ROWS)
        if k in (pygame.K_ESCAPE, pygame.K_F4, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.textset = False
        elif k == pygame.K_UP:
            self.textset_row = (self.textset_row - 1) % n
        elif k in (pygame.K_DOWN, pygame.K_TAB):
            self.textset_row = (self.textset_row + 1) % n
        elif k in (pygame.K_LEFT, pygame.K_MINUS, pygame.K_KP_MINUS):
            self.textset_change(self.textset_row, -1)
        elif k in (pygame.K_RIGHT, pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.textset_change(self.textset_row, 1)
        elif k == pygame.K_r:
            self.textset_reset()
        return True

    def textset_click(self, pos, button):
        r = self.textset_rects
        if r.get("close") and r["close"].collidepoint(pos):
            self.textset = False
        elif r.get("reset") and r["reset"].collidepoint(pos):
            self.textset_reset()
        elif r.get("panel") and not r["panel"].collidepoint(pos):
            self.textset = False                        # click outside closes
        else:
            for i in range(len(TEXT_ROWS)):
                row = r.get(("row", i))
                if row and row.collidepoint(pos):
                    self.textset_row = i
                    if pos[0] >= r[("val", i)].x:
                        left = pos[0] < r[("val", i)].centerx or button == 3
                        self.textset_change(i, -1 if left else 1)
                    break

    def draw_textset(self, c, t):
        rows = TEXT_ROWS
        row_h = 13
        w = min(self.cw - 16, max(236, 34 * self.cell_w + 24))     # preview line must fit
        prev_h = max(24, self.cell_h * 2 + 10)
        h = 26 + len(rows) * row_h + 8 + prev_h + 30
        x, y = (self.cw - w) // 2, max(TOOL_H + 4, (self.ch - h) // 2)
        panel = pygame.Rect(x, y, w, h)
        self.textset_rects = {"panel": panel}
        c.fill(BOX_BG, panel)
        self.hud_fill(BOX_BG, panel.inflate(-2, -2))      # hide terminal text under the box
        self.neon_frame(panel, corner=4, brackets=False)
        self.neon_text("// DISPLAY SETTINGS", x + 10, y + 7)
        hint = "UP/DN  LT/RT  R RESET  ESC"
        pix_text(c, hint, panel.right - 10 - pix_width(hint), y + 7, STEEL_DIM)
        yy = y + 22
        for i, (label, key, opts) in enumerate(rows):
            sel = i == self.textset_row
            rr = pygame.Rect(x + 6, yy - 3, w - 12, row_h - 1)
            self.textset_rects[("row", i)] = rr
            if sel:
                c.fill(HILITE, rr)
                c.fill(NEON_CORE, (rr.x, rr.y, 2, rr.h))
                self.neon.fill(NEON, (rr.x, rr.y, 2, rr.h))
            pix_text(c, label, x + 12, yy, WHITE if sel else STEEL)
            val = self.scale_label() if key == "ui_scale" else self.s.get(key, DEFAULTS[key])
            txt = ("%d PX" % val) if key in ("font_size", "line_gap") else str(val)
            vx = x + 104
            vr = pygame.Rect(vx, yy - 3, w - 6 - (vx - x) - 6, row_h - 1)
            self.textset_rects[("val", i)] = vr
            pix_text(c, "<", vx, yy, NEON_CORE if sel else STEEL_DIM)
            pix_text(c, ">", vr.right - 6, yy, NEON_CORE if sel else STEEL_DIM)
            colr = TEXT_COLORS.get(val, CYAN) if key == "text_color" else CYAN
            if key == "text_color":
                c.fill(colr, (vx + 10, yy, 6, PIX_H))
                pix_text(c, txt, vx + 20, yy, colr)
            else:
                pix_text(c, txt, vx + (vr.w - pix_width(txt)) // 2, yy, WHITE if sel else CYAN)
            yy += row_h
        # live preview in the real terminal renderer (glyph cache = exactly what the terminal shows)
        pv = pygame.Rect(x + 8, yy + 4, w - 16, prev_h)
        c.fill(TERM_BG[:3] if len(TERM_BG) > 3 else TERM_BG, pv)
        pygame.draw.rect(c, STEEL_DIM, pv, 1)
        sc = self.sc
        self.hud.set_clip(pygame.Rect((pv.x + 1) * sc, (pv.y + 1) * sc, (pv.w - 2) * sc, (pv.h - 2) * sc))
        samples = [[("nsh> ", self.text_fg), ("ver all", WHITE)],
                   [("HW: ", self.text_fg), ("PX4_FMU_V5 ", CYAN), ("[OK] ", PALETTE.get("green", NEON)),
                    ("한글 0123", self.text_fg)]]
        ly = pv.y + 4
        for line in samples:
            cx = pv.x + 4
            for text, colr in line:
                for ch in text:
                    wide = ord(ch) >= 0x1100 and wcwidth(ch) == 2
                    if ch != " ":
                        self.put(ch, colr, cx, ly, False, wide)
                    cx += self.cell_w * (2 if wide else 1)
            ly += self.cell_h
        self.hud.set_clip(None)
        # buttons
        by = panel.bottom - 20
        rb = pygame.Rect(x + 10, by, 60, 14)
        cb = pygame.Rect(panel.right - 70, by, 60, 14)
        self.textset_rects["reset"], self.textset_rects["close"] = rb, cb
        c.fill((12, 16, 22), rb)
        pygame.draw.rect(c, STEEL, rb, 1)
        pix_text(c, "RESET", rb.centerx - pix_width("RESET") // 2, rb.y + 4, STEEL)
        c.fill(BTN_ON, cb)
        pygame.draw.rect(c, NEON_CORE, cb, 1)
        pygame.draw.rect(self.neon, NEON, cb, 1)
        self.neon_text("CLOSE", cb.centerx - pix_width("CLOSE") // 2, cb.y + 4)

    def draw_terminal(self, t):
        c = self.canvas
        self.begin_frame()
        if self.s["rain"]:
            self.draw_rain(c, t, pygame.Rect(0, TOOL_H + 1, self.cw, self.ch - TOOL_H - HINT_H - 2))
        c.blit(self.term_panel, self.term_rect.topleft)
        self.neon_frame(self.term_rect.inflate(6, 6))
        if self.s["view"] == "HEX":
            self.draw_hex_view(c)
        else:
            self.draw_text_view(c, t)
        if self.link.state != "OPEN" and not self.vt.display[0].strip() and not self.hex.lines:
            msg = "NO LINK  -  F5 CONNECT  /  F2 CONFIGURATION"
            pix_text(c, msg, (self.cw - pix_width(msg)) // 2, self.ch // 2, STEEL)
        self.draw_toolbar(c, t)
        self.draw_hints(c)
        if self.help:
            self.draw_help(c)
        if self.textset:
            self.draw_textset(c, t)
        self.apply_glow(t)

    # ================================================================ events / loop
    def handle(self, e):
        if e.type == pygame.QUIT:
            return False
        if e.type == pygame.KEYUP:
            self.held.discard(e.key)
            self.no_repeat.discard(e.key)
            return True
        if e.type in (pygame.WINDOWFOCUSLOST, pygame.WINDOWFOCUSGAINED):
            self.held.clear()                  # KEYUPs are lost while another window has focus
            self.no_repeat.clear()
            return True
        if e.type == pygame.KEYDOWN:
            repeat = e.key in self.held
            self.held.add(e.key)
            if repeat:
                ctrl_shift = (e.mod & pygame.KMOD_CTRL) and (e.mod & pygame.KMOD_SHIFT)
                allowed = REPEAT_CFG if self.mode == "config" else REPEAT_TERM
                if e.key in self.no_repeat or e.key not in allowed or ctrl_shift:
                    return True            # held F-keys / Enter-to-open / paste etc. fire once only
        if self.mode == "config":
            if e.type == pygame.KEYDOWN:
                return self.cfg_key(e)
            if e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
                return self.cfg_click(e.pos, e.button)
            if e.type == pygame.MOUSEBUTTONDOWN and e.button in (4, 5) and self.focus == "ports":
                self.port_sel = max(0, min(len(self.ports) - 1, self.port_sel + (-1 if e.button == 4 else 1)))
            return True
        if e.type == pygame.VIDEORESIZE and not self.s["fullscreen"]:
            self.s["window"] = [e.w, e.h]
            save_settings(self.s)
            self.term_relayout()
        elif e.type == pygame.TEXTEDITING:
            if not getattr(self, "eat_text", False):
                self.composing = e.text
        elif e.type == pygame.TEXTINPUT:
            self.composing = ""
            if getattr(self, "eat_text", False) and time.time() < self.eat_text_until:
                self.eat_text = False              # the shortcut letter that opened this terminal
            elif not self.help and not self.textset:
                self.send_text(e.text)
        elif e.type == pygame.KEYDOWN:
            self.eat_text = False                  # a new key press: the shortcut's text event is past
            return self.term_key(e)
        elif e.type == pygame.MOUSEBUTTONDOWN and self.textset:
            if e.button in (1, 3):
                self.textset_click(self.to_canvas(e.pos), e.button)
        elif e.type == pygame.MOUSEBUTTONDOWN:
            hit = getattr(self, "hint_rects", {}).get("F3")
            if e.button == 1 and hit is not None and hit.collidepoint(self.to_canvas(e.pos)):
                self.new_session()                     # click "F3 NEW SESSION" in the bottom bar
            elif e.button == 1:
                c = self.cell_at(e.pos)
                self.sel, self.dragging = (c, c), True
            elif e.button == 3:
                self.copy() if (self.sel and self.sel[0] != self.sel[1]) else self.paste()
            elif e.button in (4, 5):
                if not (pygame.key.get_mods() & pygame.KMOD_SHIFT) and self.mouse_wheel_to_app(e.button == 4, e.pos):
                    pass                                   # the program in the terminal scrolls itself
                else:
                    self.scroll(e.button == 4)            # NeoTerm's own scrollback (also Shift+wheel)
        elif e.type == pygame.MOUSEMOTION and self.dragging and not self.textset:
            self.sel = (self.sel[0], self.cell_at(e.pos))
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.dragging = False
            if self.sel and self.sel[0] == self.sel[1]:
                self.sel = None
        return True

    def render(self):
        t = time.time()
        self.step_theme(t)
        if self.mode == "config":
            self.draw_config(t)
        else:
            self.draw_terminal(t)
        self.present()

    def run(self):
        # Low-latency loop: keys are sent the moment they arrive and the screen is redrawn as soon
        # as input or received data shows up (max 120 fps); otherwise it animates at FPS.
        running = True
        last = 0.0
        dirty = False
        focused, hidden = True, False
        last_rx = 0.0                           # time of the previous frame that showed new data
        rx_pending = False
        wake = pygame.event.custom_type()
        self._wake_pending = threading.Event()

        def on_data():                          # reader thread: bytes arrived -> wake the loop once
            if not self._wake_pending.is_set():
                self._wake_pending.set()
                try:
                    pygame.event.post(pygame.event.Event(wake))
                except Exception:
                    pass
        self.link.on_data = on_data
        while running:
            # Wait inside SDL (no busy polling) until an input event, received data (wake event)
            # or the next animation frame. Minimized: nothing is drawn; unfocused: BG_FPS.
            now = time.perf_counter()
            if hidden:
                due = now + HIDDEN_WAKE
            elif dirty:
                due = last + (STREAM_FRAME if now - last_rx < STREAMING else MIN_FRAME)
            else:
                due = last + 1 / (FPS if focused else BG_FPS)
            ms = int((due - now) * 1000)
            events = [pygame.event.wait(ms)] if ms >= 1 else []
            events += pygame.event.get()
            for e in events:
                if e.type == pygame.NOEVENT:
                    continue
                if e.type == wake:
                    self._wake_pending.clear()
                    continue
                if e.type == pygame.WINDOWFOCUSLOST:
                    focused = False
                elif e.type == pygame.WINDOWFOCUSGAINED:
                    focused = True
                elif e.type in (pygame.WINDOWMINIMIZED, pygame.WINDOWHIDDEN):
                    hidden = True
                elif e.type in (pygame.WINDOWRESTORED, pygame.WINDOWSHOWN, pygame.WINDOWMAXIMIZED,
                                pygame.WINDOWEXPOSED):
                    hidden = False
                self.quiet_send = False
                if not self.handle(e):
                    running = False
                    break
                if not (self.quiet_send and e.type in (pygame.TEXTINPUT, pygame.KEYDOWN)):
                    dirty = True
            if not running:
                break
            if self.mode == "config" and time.time() >= self.next_port_scan:
                self.refresh_ports()            # picks up USB-serial devices plugged in/out
            if self.pump_serial():
                dirty = rx_pending = True
            now = time.perf_counter()
            # a lone echo is drawn at once; a continuous stream is drawn at STREAM_FRAME
            gap = STREAM_FRAME if now - last_rx < STREAMING else MIN_FRAME
            if hidden:
                dirty = False
            elif (dirty and now - last >= gap) or now - last >= 1 / (FPS if focused else BG_FPS):
                if rx_pending:
                    last_rx, rx_pending = now, False
                last = now
                dirty = False
                self.render()
        self.mav_close()                    # tell PX4 to close the nsh session
        self.link.close()
        if self.log:
            self.log.close()
        save_settings(self.s)
        pygame.quit()


def fmt_rate(b):
    if b >= 1048576:
        return "%.1fM" % (b / 1048576)
    if b >= 1024:
        return "%.1fK" % (b / 1024)
    return "%dB" % b


def fmt_bytes(b):
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024 or unit == "GB":
            return ("%d%s" if unit == "B" else "%.1f%s") % (b, unit)
        b /= 1024


def main():
    app = App()
    try:
        app.run()
    finally:                                    # even after an error: free the COM port and end
        try:                                    # the PowerShell child instead of leaving it behind
            app.link.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
