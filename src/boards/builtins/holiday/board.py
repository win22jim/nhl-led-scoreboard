"""Holiday board.

Renders a small themed icon on the left side and a "Happy {Holiday}" greeting
on the right when today is a recognized US or Canadian holiday.

Two run modes (config: ``skip_non_holidays``):
- ``true`` (default): on non-holidays the board returns immediately so the
  rotation moves on — the board essentially "disappears" outside holidays.
- ``false``: shows the configured ``non_holiday_message`` with a generic icon.

Country selection (config: ``country`` = ``us`` or ``ca``) picks the
holiday calendar. Shared holidays (Christmas, New Year, Easter, Halloween,
Valentine's) appear in both.

All icons are drawn from PIL primitives — no extra asset files required.
"""

import logging
from datetime import date, timedelta

from PIL import Image, ImageDraw

from boards.base_board import BoardBase
from boards.builtins._text import sanitize

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")


# ── Date helpers ────────────────────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """N-th occurrence of `weekday` (Mon=0…Sun=6) in `month`/`year`."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` in `month`/`year`."""
    # Find first weekday of next month, back up
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    # Step back to the requested weekday
    d = first_next - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm — vendor-free Easter computation."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _victoria_day(year: int) -> date:
    """Monday on or before May 24."""
    d = date(year, 5, 24)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    return d


# ── Icon drawing primitives ─────────────────────────────────────────────────
#
# Each icon function takes a PIL ImageDraw + (w, h) size and paints into the
# provided image. Icons are 14x14 by default; the renderer pastes them at the
# matrix's left edge.

ICON_SIZE = 14


def _new_image(bg=(0, 0, 0, 0)):
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), bg)
    return img, ImageDraw.Draw(img)


def icon_heart():
    img, d = _new_image()
    red = (235, 60, 70, 255)
    # Two circles for the lobes, a triangle for the point
    d.ellipse([1, 2, 8, 9], fill=red)
    d.ellipse([6, 2, 13, 9], fill=red)
    d.polygon([(1, 6), (13, 6), (7, 13)], fill=red)
    return img


def icon_tree():
    img, d = _new_image()
    green = (40, 170, 70, 255)
    brown = (110, 70, 30, 255)
    # Stacked triangles
    d.polygon([(7, 0), (2, 5), (12, 5)], fill=green)
    d.polygon([(7, 3), (1, 8), (13, 8)], fill=green)
    d.polygon([(7, 6), (0, 11), (14, 11)], fill=green)
    d.rectangle([6, 11, 8, 13], fill=brown)
    return img


def icon_pumpkin():
    img, d = _new_image()
    orange = (240, 130, 30, 255)
    green = (40, 130, 50, 255)
    d.ellipse([0, 3, 13, 13], fill=orange)
    # carved face
    d.rectangle([3, 6, 5, 8], fill=(20, 20, 20, 255))
    d.rectangle([8, 6, 10, 8], fill=(20, 20, 20, 255))
    d.rectangle([4, 10, 9, 11], fill=(20, 20, 20, 255))
    # stem
    d.rectangle([6, 1, 8, 4], fill=green)
    return img


def icon_flag_us():
    img, d = _new_image()
    red = (220, 30, 40, 255)
    white = (240, 240, 240, 255)
    blue = (30, 60, 160, 255)
    # 7 stripes alternating red/white
    for i in range(7):
        c = red if i % 2 == 0 else white
        d.rectangle([0, i * 2, 13, i * 2 + 1], fill=c)
    # blue canton with single white pixel "star cluster"
    d.rectangle([0, 0, 6, 6], fill=blue)
    d.point([(2, 2), (4, 2), (2, 4), (4, 4), (3, 3)], fill=white)
    return img


def icon_egg():
    img, d = _new_image()
    cream = (250, 230, 200, 255)
    d.ellipse([2, 1, 11, 13], fill=cream)
    # Decorative dots
    d.point([(5, 4), (8, 6), (4, 8), (9, 10), (6, 11)], fill=(220, 80, 120, 255))
    return img


def icon_shamrock():
    img, d = _new_image()
    green = (40, 160, 60, 255)
    dark = (50, 90, 30, 255)
    d.ellipse([1, 1, 8, 7], fill=green)
    d.ellipse([6, 1, 13, 7], fill=green)
    d.ellipse([3, 5, 11, 12], fill=green)
    d.rectangle([6, 11, 8, 13], fill=dark)
    return img


def icon_gift():
    img, d = _new_image()
    red = (200, 30, 60, 255)
    gold = (240, 200, 60, 255)
    d.rectangle([1, 3, 12, 13], fill=red)
    d.rectangle([1, 3, 12, 5], fill=(120, 20, 40, 255))  # lid
    d.rectangle([6, 3, 7, 13], fill=gold)                # vertical ribbon
    d.rectangle([1, 5, 12, 6], fill=gold)                # horizontal ribbon
    # Bow
    d.polygon([(5, 1), (7, 3), (9, 1), (8, 3)], fill=gold)
    return img


def icon_poppy():
    img, d = _new_image()
    red = (200, 25, 40, 255)
    black = (10, 10, 10, 255)
    d.ellipse([1, 1, 13, 13], fill=red)
    d.ellipse([5, 5, 9, 9], fill=black)
    return img


def icon_maple_leaf():
    img, d = _new_image()
    red = (230, 40, 50, 255)
    # 11-point stylized maple leaf, drawn as a polygon. Approximate.
    pts = [
        (7, 0),  (8, 4), (12, 3), (10, 6), (13, 7),
        (10, 9), (11, 13), (7, 11), (3, 13), (4, 9),
        (1, 7), (4, 6), (2, 3), (6, 4),
    ]
    d.polygon(pts, fill=red)
    return img


def icon_clock():
    img, d = _new_image()
    yellow = (245, 210, 80, 255)
    black = (20, 20, 20, 255)
    d.ellipse([0, 0, 13, 13], fill=yellow, outline=black)
    d.line([(7, 7), (7, 2)], fill=black, width=1)
    d.line([(7, 7), (11, 9)], fill=black, width=1)
    d.point([(7, 7)], fill=black)
    return img


def icon_star():
    img, d = _new_image()
    gold = (245, 210, 80, 255)
    # 5-point star approximation
    pts = [(7, 0), (9, 5), (13, 5), (10, 8), (12, 13), (7, 10), (2, 13), (4, 8), (1, 5), (5, 5)]
    d.polygon(pts, fill=gold)
    return img


def icon_letter(ch: str, color):
    """Fallback icon — paint a single bold letter (used when a holiday has no
    natural primitive icon, e.g. MLK Day, Thanksgiving)."""
    img, d = _new_image()
    # No font dep here — fall back to colored block + small inner letter via
    # rectangles. We render the letter at render-time when we have access to
    # the matrix font; here we just paint a colored chip.
    d.rectangle([1, 1, 12, 12], fill=color, outline=(0, 0, 0, 255))
    return img


# ── Holiday definitions ─────────────────────────────────────────────────────
#
# Each entry has: id, label (display message), matches(d) -> bool, icon()
# Order matters: the first matching entry wins, so put more-specific holidays
# (e.g. Christmas Eve) before more-general ones (e.g. NHL puck fallback).

def _matches_fixed(m, day):
    return lambda d: d.month == m and d.day == day


def _matches_nth_weekday(month, weekday, n):
    return lambda d: d == _nth_weekday(d.year, month, weekday, n)


def _matches_last_weekday(month, weekday):
    return lambda d: d == _last_weekday(d.year, month, weekday)


_SHARED_HOLIDAYS = [
    {"id": "new_year_eve",  "label": "HAPPY NEW YEAR EVE", "matches": _matches_fixed(12, 31), "icon": icon_clock},
    {"id": "new_year",      "label": "HAPPY NEW YEAR",     "matches": _matches_fixed(1, 1),   "icon": icon_clock},
    {"id": "valentines",    "label": "HAPPY VALENTINES",   "matches": _matches_fixed(2, 14),  "icon": icon_heart},
    {"id": "st_patricks",   "label": "HAPPY ST PATTYS",    "matches": _matches_fixed(3, 17),  "icon": icon_shamrock},
    {"id": "easter",        "label": "HAPPY EASTER",       "matches": lambda d: d == _easter_sunday(d.year), "icon": icon_egg},
    {"id": "halloween",     "label": "HAPPY HALLOWEEN",    "matches": _matches_fixed(10, 31), "icon": icon_pumpkin},
    {"id": "christmas_eve", "label": "CHRISTMAS EVE",      "matches": _matches_fixed(12, 24), "icon": icon_tree},
    {"id": "christmas",     "label": "MERRY CHRISTMAS",    "matches": _matches_fixed(12, 25), "icon": icon_tree},
]

_US_ONLY = [
    {"id": "mlk_day",         "label": "MLK DAY",          "matches": _matches_nth_weekday(1, 0, 3),     "icon": lambda: icon_letter("M", (50, 100, 200, 255))},
    {"id": "presidents_day",  "label": "PRESIDENTS DAY",   "matches": _matches_nth_weekday(2, 0, 3),     "icon": icon_flag_us},
    {"id": "memorial_day",    "label": "MEMORIAL DAY",     "matches": _matches_last_weekday(5, 0),       "icon": icon_flag_us},
    {"id": "juneteenth",      "label": "JUNETEENTH",       "matches": _matches_fixed(6, 19),             "icon": lambda: icon_letter("J", (180, 70, 30, 255))},
    {"id": "independence",    "label": "HAPPY 4TH OF JULY","matches": _matches_fixed(7, 4),              "icon": icon_flag_us},
    {"id": "labor_day_us",    "label": "LABOR DAY",        "matches": _matches_nth_weekday(9, 0, 1),     "icon": lambda: icon_letter("L", (50, 130, 80, 255))},
    {"id": "veterans_day",    "label": "VETERANS DAY",     "matches": _matches_fixed(11, 11),            "icon": icon_flag_us},
    {"id": "thanksgiving_us", "label": "HAPPY THANKSGIVING","matches": _matches_nth_weekday(11, 3, 4),   "icon": lambda: icon_letter("T", (210, 130, 40, 255))},
]

_CA_ONLY = [
    {"id": "family_day_ca",   "label": "FAMILY DAY",       "matches": _matches_nth_weekday(2, 0, 3),     "icon": lambda: icon_letter("F", (50, 100, 200, 255))},
    {"id": "victoria_day",    "label": "VICTORIA DAY",     "matches": lambda d: d == _victoria_day(d.year), "icon": icon_maple_leaf},
    {"id": "canada_day",      "label": "HAPPY CANADA DAY", "matches": _matches_fixed(7, 1),              "icon": icon_maple_leaf},
    {"id": "civic_holiday",   "label": "CIVIC HOLIDAY",    "matches": _matches_nth_weekday(8, 0, 1),     "icon": icon_maple_leaf},
    {"id": "labour_day_ca",   "label": "LABOUR DAY",       "matches": _matches_nth_weekday(9, 0, 1),     "icon": lambda: icon_letter("L", (50, 130, 80, 255))},
    {"id": "thanksgiving_ca", "label": "HAPPY THANKSGIVING","matches": _matches_nth_weekday(10, 0, 2),   "icon": lambda: icon_letter("T", (210, 130, 40, 255))},
    {"id": "remembrance_day", "label": "REMEMBRANCE DAY",  "matches": _matches_fixed(11, 11),            "icon": icon_poppy},
    {"id": "boxing_day",      "label": "BOXING DAY",       "matches": _matches_fixed(12, 26),            "icon": icon_gift},
]


def _calendar_for(country: str):
    """Country-specific holidays first (so Veterans Day vs Remembrance Day
    resolve correctly on Nov 11), then shared holidays as fallback."""
    country = (country or "us").lower()
    if country == "ca":
        return _CA_ONLY + _SHARED_HOLIDAYS
    return _US_ONLY + _SHARED_HOLIDAYS


def detect_holiday(country: str, today: date):
    for h in _calendar_for(country):
        try:
            if h["matches"](today):
                return h
        except Exception:
            # A misbehaving date function should never bring down the rotation.
            continue
    return None


# ── Board ───────────────────────────────────────────────────────────────────


class HolidayBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.country = str(self.get_config_value("country", "us")).lower()
        if self.country not in ("us", "ca"):
            self.country = "us"
        self.skip_non_holidays = bool(self.get_config_value("skip_non_holidays", True))
        # Sanitize the user-configurable message so curly quotes pasted from
        # other apps don't render as glyph boxes on the matrix.
        self.non_holiday_message = sanitize(self.get_config_value("non_holiday_message", "HAVE A GREAT DAY"))
        self.duration = max(2, int(self.get_config_value("duration", 8)))

        self.font = data.config.layout.font
        try:
            self.font_large = data.config.layout.font_large
        except AttributeError:
            self.font_large = self.font

    def render(self):
        try:
            today = date.today()
            holiday = detect_holiday(self.country, today)
            if not holiday and self.skip_non_holidays:
                # Sleep briefly so we don't busy-loop the rotation. The handler
                # in boards.py advances past us either way.
                self.sleepEvent.wait(0.1)
                return
            self._render_holiday(holiday)
        except Exception as e:
            debug.error(f"HolidayBoard: render failed: {e}", exc_info=True)
            # Don't leave a partially-painted frame on the matrix.
            try:
                self.matrix.clear()
                self.matrix.render()
            except Exception:
                pass

    def _render_holiday(self, holiday):
        self.matrix.clear()
        if holiday:
            label = holiday.get("label", "")
            icon_img = None
            try:
                icon_img = holiday["icon"]()
            except Exception as e:
                debug.warning(f"HolidayBoard: icon draw failed for {holiday.get('id')}: {e}")
        else:
            label = self.non_holiday_message
            icon_img = icon_star()  # neutral generic icon

        # Layout: icon on left at y=1, text wraps to the right of the icon.
        if icon_img is not None:
            try:
                self.matrix.draw_image((1, 1), icon_img)
            except Exception as e:
                debug.warning(f"HolidayBoard: draw_image failed: {e}")

        text_x = ICON_SIZE + 3
        # 64-wide matrix has ~46px usable for text in this layout; 18 chars in
        # the 04B_24 font fit comfortably. Wrap by word for longer greetings.
        line_budget = max(8, (self.matrix.width - text_x - 1) // 4)
        words = label.split()
        lines, cur = [], ""
        for w in words:
            candidate = (cur + " " + w).strip()
            if len(candidate) > line_budget and cur:
                lines.append(cur)
                cur = w
            else:
                cur = candidate
        if cur:
            lines.append(cur)
        lines = lines[:3]
        # Vertically center the lines next to the icon.
        total = len(lines) * 7
        y0 = max(1, (self.matrix.height - total) // 2)
        for i, ln in enumerate(lines):
            self.matrix.draw_text((text_x, y0 + i * 7), ln, font=self.font, fill=(255, 230, 120))

        self.matrix.render()
        self.sleepEvent.wait(self.duration)
