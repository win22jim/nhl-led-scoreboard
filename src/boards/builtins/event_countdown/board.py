"""Event Countdown board.

User-configurable countdown to a custom event. Configure via the dashboard:
- *Title*: short label shown beside the countdown (e.g. "MY BIRTHDAY")
- *Target Date*: YYYY-MM-DD (date of the event)
- *Target Time* (optional): HH:MM (24h). If set, "today" rendering shows
  hours/minutes remaining instead of "TODAY".
- *Icon*: pick from a built-in set of LED-friendly icons (star, heart,
  gift, trophy, calendar, clock, fire, rocket, puck, balloon, flag, none).
  Emoji/photo support is intentionally avoided here — pixel-font matrices
  can't render proper emoji and arbitrary user images would need image
  upload + storage.
- *Skip if more than N days away* (0 = never skip)
- *Skip after event has passed* (default true)
- *Display Duration* (seconds)

Failure policy: all parsing/render paths are wrapped in try/except. A
misconfigured date never crashes the rotation — the board just renders an
empty state or skips.
"""

import logging
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw

from boards.base_board import BoardBase
from boards.builtins._text import sanitize

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

ICON_SIZE = 14


# ── Icon drawing primitives ─────────────────────────────────────────────────
#
# Each returns a 14x14 RGBA PIL image. All shapes are composed of basic
# rectangles/ellipses/polygons so they render crisply at this size.

def _new(bg=(0, 0, 0, 0)):
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), bg)
    return img, ImageDraw.Draw(img)


def _icon_star():
    img, d = _new()
    gold = (245, 210, 80, 255)
    pts = [(7, 0), (9, 5), (13, 5), (10, 8), (12, 13), (7, 10), (2, 13), (4, 8), (1, 5), (5, 5)]
    d.polygon(pts, fill=gold)
    return img


def _icon_heart():
    img, d = _new()
    red = (235, 60, 70, 255)
    d.ellipse([1, 2, 8, 9], fill=red)
    d.ellipse([6, 2, 13, 9], fill=red)
    d.polygon([(1, 6), (13, 6), (7, 13)], fill=red)
    return img


def _icon_gift():
    img, d = _new()
    red = (200, 30, 60, 255)
    gold = (240, 200, 60, 255)
    d.rectangle([1, 3, 12, 13], fill=red)
    d.rectangle([1, 3, 12, 5], fill=(120, 20, 40, 255))
    d.rectangle([6, 3, 7, 13], fill=gold)
    d.rectangle([1, 5, 12, 6], fill=gold)
    d.polygon([(5, 1), (7, 3), (9, 1), (8, 3)], fill=gold)
    return img


def _icon_trophy():
    img, d = _new()
    gold = (240, 200, 60, 255)
    dark = (140, 90, 20, 255)
    d.rectangle([3, 1, 10, 6], fill=gold)   # cup body
    d.arc([0, 0, 5, 5], 90, 270, fill=gold) # left handle
    d.arc([8, 0, 13, 5], 270, 90, fill=gold)# right handle
    d.rectangle([5, 6, 8, 10], fill=gold)   # stem
    d.rectangle([2, 10, 11, 12], fill=dark) # base
    d.rectangle([1, 12, 12, 13], fill=dark)
    return img


def _icon_calendar():
    img, d = _new()
    grey = (220, 220, 220, 255)
    red = (210, 50, 60, 255)
    dark = (40, 40, 40, 255)
    d.rectangle([1, 2, 12, 12], fill=grey, outline=dark)
    d.rectangle([1, 2, 12, 5], fill=red)
    d.rectangle([3, 0, 4, 3], fill=dark)
    d.rectangle([9, 0, 10, 3], fill=dark)
    # day grid
    for r in range(7, 12, 2):
        for c in range(3, 11, 2):
            d.point([(c, r)], fill=dark)
    return img


def _icon_clock():
    img, d = _new()
    yellow = (245, 210, 80, 255)
    black = (20, 20, 20, 255)
    d.ellipse([0, 0, 13, 13], fill=yellow, outline=black)
    d.line([(7, 7), (7, 2)], fill=black, width=1)
    d.line([(7, 7), (11, 9)], fill=black, width=1)
    d.point([(7, 7)], fill=black)
    return img


def _icon_fire():
    img, d = _new()
    orange = (240, 130, 30, 255)
    yellow = (250, 220, 70, 255)
    red = (220, 40, 30, 255)
    d.polygon([(7, 0), (3, 6), (4, 9), (7, 4), (10, 9), (11, 6)], fill=red)
    d.polygon([(7, 3), (4, 8), (5, 12), (7, 7), (9, 12), (10, 8)], fill=orange)
    d.polygon([(7, 8), (6, 11), (7, 13), (8, 11)], fill=yellow)
    return img


def _icon_rocket():
    img, d = _new()
    silver = (220, 220, 220, 255)
    red = (220, 50, 50, 255)
    blue = (80, 140, 220, 255)
    # body
    d.polygon([(7, 0), (4, 5), (4, 11), (10, 11), (10, 5)], fill=silver)
    # window
    d.ellipse([5, 5, 9, 8], fill=blue)
    # fins
    d.polygon([(4, 9), (1, 13), (4, 13)], fill=red)
    d.polygon([(10, 9), (13, 13), (10, 13)], fill=red)
    # flame
    d.polygon([(6, 13), (7, 11), (8, 13)], fill=(250, 180, 30, 255))
    return img


def _icon_puck():
    img, d = _new()
    black = (10, 10, 10, 255)
    grey = (90, 90, 90, 255)
    d.ellipse([0, 2, 13, 11], fill=black, outline=grey)
    d.arc([0, 2, 13, 5], 180, 360, fill=grey)
    return img


def _icon_balloon():
    img, d = _new()
    pink = (240, 80, 130, 255)
    string = (180, 180, 180, 255)
    d.ellipse([2, 0, 11, 9], fill=pink)
    d.polygon([(6, 9), (7, 9), (7, 10), (6, 10)], fill=pink)
    d.line([(7, 10), (5, 13)], fill=string)
    return img


def _icon_flag():
    img, d = _new()
    pole = (140, 90, 30, 255)
    red = (220, 30, 40, 255)
    d.rectangle([2, 0, 3, 13], fill=pole)
    d.polygon([(3, 1), (12, 1), (11, 4), (12, 7), (3, 7)], fill=red)
    return img


_ICON_FUNCS = {
    "star":     _icon_star,
    "heart":    _icon_heart,
    "gift":     _icon_gift,
    "trophy":   _icon_trophy,
    "calendar": _icon_calendar,
    "clock":    _icon_clock,
    "fire":     _icon_fire,
    "rocket":   _icon_rocket,
    "puck":     _icon_puck,
    "balloon":  _icon_balloon,
    "flag":     _icon_flag,
    "none":     None,
}


# ── Board ───────────────────────────────────────────────────────────────────


class EventCountdownBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        # Sanitize: users may paste curly quotes / accented characters from
        # other apps into the title field and the pixel font can't render
        # them — they'd show as glyph boxes on the matrix.
        self.title = sanitize(self.get_config_value("title", "MY EVENT") or "").upper().strip()
        self.target_date_str = (self.get_config_value("target_date", "") or "").strip()
        self.target_time_str = (self.get_config_value("target_time", "") or "").strip()
        icon_name = (self.get_config_value("icon", "star") or "star").lower()
        self.icon_name = icon_name if icon_name in _ICON_FUNCS else "star"
        self.skip_if_more_than_days = int(self.get_config_value("skip_if_more_than_days", 0) or 0)
        self.skip_after_event = bool(self.get_config_value("skip_after_event", True))
        self.duration = max(2, int(self.get_config_value("duration", 8)))

        self.font = data.config.layout.font

    def _parse_target(self):
        """Return a datetime (or None) for the configured target."""
        if not self.target_date_str:
            return None
        try:
            d = datetime.strptime(self.target_date_str, "%Y-%m-%d")
        except ValueError:
            debug.warning(f"EventCountdownBoard: bad target_date '{self.target_date_str}'")
            return None
        if self.target_time_str:
            try:
                t = datetime.strptime(self.target_time_str, "%H:%M").time()
                d = datetime.combine(d.date(), t)
            except ValueError:
                debug.warning(f"EventCountdownBoard: bad target_time '{self.target_time_str}', using midnight")
        return d

    def render(self):
        try:
            self._render_inner()
        except Exception as e:
            debug.error(f"EventCountdownBoard: render failed: {e}", exc_info=True)

    def _render_inner(self):
        target = self._parse_target()
        if target is None:
            # No date configured — render a "please configure" prompt rather
            # than nothing, so the user knows the board is alive.
            self._render_text("SET DATE IN DASHBOARD", color=(150, 150, 150))
            return

        now = datetime.now()
        days = (target.date() - now.date()).days

        # "Past event" handling.
        if target < now:
            if self.skip_after_event:
                self.sleepEvent.wait(0.1)
                return
            self._render_two_lines("EVENT PAST", self.title, color=(160, 160, 160))
            return

        # "Far future" skip — keeps the board quiet most of the year for
        # annual events. 0 disables.
        if self.skip_if_more_than_days and days > self.skip_if_more_than_days:
            self.sleepEvent.wait(0.1)
            return

        # Today's-the-day rendering: if a time is set and we're <24h out,
        # show hours/minutes; otherwise just "TODAY".
        if days == 0:
            if self.target_time_str:
                delta = target - now
                total_seconds = int(delta.total_seconds())
                if total_seconds <= 0:
                    line_top = "NOW"
                else:
                    h = total_seconds // 3600
                    m = (total_seconds % 3600) // 60
                    line_top = f"T-{h}H{m:02d}M" if h else f"T-{m}M"
            else:
                line_top = "TODAY"
        elif days == 1:
            line_top = "1 DAY"
        else:
            line_top = f"{days} DAYS"

        self._render_two_lines(line_top, self.title, color=(255, 230, 120))

    def _render_two_lines(self, line_top: str, line_bottom: str, color):
        self.matrix.clear()
        icon_func = _ICON_FUNCS.get(self.icon_name)
        text_x = 1
        if icon_func is not None:
            try:
                self.matrix.draw_image((1, 1), icon_func())
                text_x = ICON_SIZE + 3
            except Exception as e:
                debug.warning(f"EventCountdownBoard: draw_image failed: {e}")

        # Two-line layout: top line is the countdown number, bottom is the title.
        # Wrap title across up to two lines if it's long.
        line_budget = max(8, (self.matrix.width - text_x - 1) // 4)
        self.matrix.draw_text((text_x, 1), line_top[:line_budget], font=self.font, fill=color)

        title_lines = self._wrap(line_bottom, line_budget)
        y = 10
        for ln in title_lines[:2]:
            self.matrix.draw_text((text_x, y), ln, font=self.font, fill=(255, 255, 255))
            y += 7

        self.matrix.render()
        self.sleepEvent.wait(self.duration)

    def _render_text(self, msg: str, color):
        self.matrix.clear()
        icon_func = _ICON_FUNCS.get(self.icon_name)
        if icon_func is not None:
            try:
                self.matrix.draw_image((1, 1), icon_func())
            except Exception:
                pass
        self.matrix.draw_text((1, max(0, (self.matrix.height - 7) // 2)), msg[:24], font=self.font, fill=color)
        self.matrix.render()
        self.sleepEvent.wait(self.duration)

    @staticmethod
    def _wrap(text: str, line_budget: int):
        words = text.split()
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
        return lines
