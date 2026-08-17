"""Team News board.

Shows recent NHL.com headlines for the user's first preferred team via the
official Forge content API (forge-dapi.d3.nhle.com). RSS feeds were retired
by NHL, so this is the canonical replacement.

Presentation notes (rewritten 2026-08-14 after the first version proved
unreadable on hardware):

* **Headlines only.** The API's ``summary`` field is a ~300-character wire
  paragraph — about 1100px in the 8px pixel font, which took 40-47 SECONDS to
  crawl past at the bottom of an otherwise-blank screen. It read as "a weird
  line at the bottom" and as the board hanging. Headlines carry the
  information; the summaries did not earn their screen time.
* **Static where it fits.** Most headlines wrap into 2-3 lines and can just be
  read. Only genuinely long ones fall back to a marquee.
* **No fixed per-item duration.** Each headline takes exactly as long as it
  needs — its scroll time, or a read time proportional to its length. Short
  headlines are never padded out to fill a slot.

Failure policy: defensive at every step. Missing team id, network error,
empty result — all render an empty state.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import preferred_team_id
from boards.builtins._text import (
    line_overflows,
    paginate,
    read_seconds,
    sanitize,
    scroll_line,
    text_width,
    wrap_lines,
)

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

FORGE_STORIES_URL = (
    "https://forge-dapi.d3.nhle.com/v2/content/en-us/stories"
    "?tags.slug=teamid-{team_id}&%24limit={limit}"
)

# Body area below the header banner.
BODY_TOP = 11


class TeamNewsBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.max_items = max(1, min(20, int(self.get_config_value("max_items", 8))))
        # How many headlines to show per visit to this board. Each takes its
        # own natural time, so this bounds the visit by content, not a timer.
        self.headlines_per_visit = max(1, int(self.get_config_value("headlines_per_visit", 3)))
        # Safety net only: stop STARTING new headlines past this. Never pads.
        self.max_seconds = max(10, int(self.get_config_value("max_seconds", 60)))
        self.update_freq_seconds = max(300, int(self.get_config_value("update_freq", 30)) * 60)
        self.scroll_speed = float(self.get_config_value("scroll_speed", 0.03))
        self.show_counter = bool(self.get_config_value("show_counter", True))

        self.font = data.config.layout.font

        self._cache_items = []
        self._cache_team_id = None
        self._cache_ts = 0.0
        self._cursor = 0

    def _maybe_refresh(self):
        team_id = preferred_team_id(self.data)
        if team_id is None:
            self._cache_items = []
            return
        fresh = (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_team_id == team_id
        if fresh and self._cache_items:
            return
        url = FORGE_STORIES_URL.format(team_id=team_id, limit=self.max_items)
        payload = fetch_json(url, timeout=10.0)
        if not payload:
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return
        parsed = []
        for it in items[: self.max_items]:
            if not isinstance(it, dict):
                continue
            headline = it.get("headline") or it.get("title") or ""
            if not headline:
                continue
            # Sanitize at ingest so curly quotes, em-dashes, accented names,
            # and ellipsis (all common in NHL editorial copy) don't render
            # as glyph boxes in the pixel font.
            parsed.append(sanitize(headline).strip())
        if not parsed:
            return
        self._cache_items = parsed
        self._cache_team_id = team_id
        self._cache_ts = time.time()
        debug.info(f"TeamNewsBoard: loaded {len(parsed)} headlines for team {team_id}")

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"TeamNewsBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._cache_items:
                self._render_empty()
                return
            total = len(self._cache_items)
            shown = 0
            started = time.time()
            while shown < min(self.headlines_per_visit, total):
                # Budget check happens BEFORE starting an item, never after —
                # so we don't cut a headline off mid-read, and never idle
                # waiting for a budget to expire.
                if shown and (time.time() - started) >= self.max_seconds:
                    debug.debug("TeamNewsBoard: visit budget reached, advancing rotation")
                    break
                if self.sleepEvent.is_set():
                    return
                index = self._cursor % total
                self._render_headline(self._cache_items[index], index + 1, total)
                self._cursor = (self._cursor + 1) % total
                shown += 1
        except Exception as e:
            debug.error(f"TeamNewsBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_headline(self, headline: str, position: int, total: int):
        """One headline, always word-wrapped and always static.

        Short headlines fill one screen. Long ones are paged — two or three
        readable lines at a time — rather than crawling past on a single line.
        A marquee is the last resort, used only when a single unbreakable word
        is wider than the panel.

        Every page starts from a full clear. The previous version cleared only
        the scrolling region, which left the old item's text frozen on rows it
        didn't own — the stray "lines" at the bottom of the screen.
        """
        usable_h = self.matrix.height - BODY_TOP
        # 7px rows, not 8: the 04B_24 glyphs are ~6px tall, so 7 still leaves a
        # clean gap and fits a third line in the 21 rows below the header.
        line_h = 7
        per_page = max(1, usable_h // line_h)

        lines, _ = wrap_lines(self.font, headline, self.matrix.width - 2)
        if not lines:
            return

        if line_overflows(self.font, lines, self.matrix.width - 2):
            self._marquee(headline, position, total, line_h, usable_h)
            return

        pages = paginate(lines, per_page)
        for page_no, page in enumerate(pages):
            if self.sleepEvent.is_set():
                return
            self.matrix.clear()
            self._draw_header(position, total)
            for i, line in enumerate(page):
                self.matrix.draw_text((1, BODY_TOP + i * line_h), line,
                                      font=self.font, fill=(255, 255, 255))
            if len(pages) > 1:
                self._draw_page_dots(page_no, len(pages))
            self.matrix.render()
            # Time each page on its own text, so a half-full final page isn't
            # held as long as a full one.
            self.sleepEvent.wait(read_seconds(" ".join(page)))

    def _marquee(self, headline: str, position: int, total: int, line_h: int, usable_h: int):
        """Fallback for text that cannot be wrapped to fit the panel width."""
        y = BODY_TOP + max(0, (usable_h - line_h) // 2)
        self.matrix.clear()
        self._draw_header(position, total)
        self.matrix.render()
        scroll_line(
            self.matrix, self.sleepEvent, self.font,
            headline, y=y, color=(255, 255, 255),
            region=(0, y - 1, self.matrix.width, y + line_h + 1),
            frame_delay=self.scroll_speed,
            redraw_static=lambda: self._draw_header(position, total),
        )

    def _draw_page_dots(self, page_no: int, pages: int):
        """Tiny progress dots on the bottom row so a paged headline reads as
        'there's more coming' rather than as the board having stalled."""
        gap = 3
        width = pages * gap
        x0 = max(0, (self.matrix.width - width) // 2)
        y = self.matrix.height - 1
        for i in range(pages):
            color = (255, 255, 255) if i == page_no else (60, 60, 60)
            self.matrix.draw_pixel((x0 + i * gap, y), color)

    def _draw_header(self, position=None, total=None):
        self.matrix.draw_rectangle((0, 0), (self.matrix.width, 9), fill=(0, 0, 0))
        self.matrix.draw_text((1, 0), "TEAM NEWS", font=self.font, fill=(120, 180, 255))
        if self.show_counter and position and total and total > 1:
            label = f"{position}/{total}"
            x = max(0, self.matrix.width - text_width(self.font, label) - 1)
            self.matrix.draw_text((x, 0), label, font=self.font, fill=(90, 130, 190))

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header()
        msg = "no team" if preferred_team_id(self.data) is None else "no news"
        self.matrix.draw_text((1, BODY_TOP + 2), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        # Short — an empty board should hand the rotation on quickly, not sit
        # on a dead screen for the old 12-second rotation_rate.
        self.sleepEvent.wait(3)
