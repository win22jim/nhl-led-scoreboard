"""Team News board.

Shows recent NHL.com headlines for the user's first preferred team via the
official Forge content API (forge-dapi.d3.nhle.com). RSS feeds were retired
by NHL, so this is the canonical replacement.

Failure policy: defensive at every step. Missing team id, network error,
empty result — all render an empty state.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._text import sanitize, scroll_line, text_width

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

FORGE_STORIES_URL = (
    "https://forge-dapi.d3.nhle.com/v2/content/en-us/stories"
    "?tags.slug=teamid-{team_id}&%24limit={limit}"
)


class TeamNewsBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(3, int(self.get_config_value("rotation_rate", 12)))
        self.max_items = max(1, min(20, int(self.get_config_value("max_items", 5))))
        self.update_freq_seconds = max(300, int(self.get_config_value("update_freq", 30)) * 60)

        self.font = data.config.layout.font

        self._cache_items = []
        self._cache_team_id = None
        self._cache_ts = 0.0
        self._cursor = 0

    def _resolve_team_id(self):
        """Get the NHL team id of the first preferred team. None if unknown.

        Uses data.pref_teams (list of ids) directly when populated. We trust
        the existing pref_teams resolution rather than re-implementing
        name->id lookups here.
        """
        try:
            pref = self.data.pref_teams or []
        except Exception:
            return None
        if not pref:
            return None
        return pref[0]

    def _maybe_refresh(self):
        team_id = self._resolve_team_id()
        if team_id is None:
            self._cache_items = []
            return
        # Refresh on a TTL, OR if the preferred team changed.
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
            summary = it.get("summary") or ""
            if not headline:
                continue
            # Sanitize at ingest so curly quotes, em-dashes, accented names,
            # and ellipsis (all common in NHL editorial copy) don't render
            # as glyph boxes in the pixel font.
            parsed.append({
                "headline": sanitize(headline).strip(),
                "summary": sanitize(summary).strip(),
            })
        self._cache_items = parsed
        self._cache_team_id = team_id
        self._cache_ts = time.time()
        debug.info(f"TeamNewsBoard: loaded {len(parsed)} stories for team {team_id}")

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"TeamNewsBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._cache_items:
                self._render_empty()
                return
            item = self._cache_items[self._cursor % len(self._cache_items)]
            self._cursor = (self._cursor + 1) % max(1, len(self._cache_items))
            self._render_item(item)
        except Exception as e:
            debug.error(f"TeamNewsBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_item(self, item: dict):
        headline = item.get("headline", "") or ""
        summary = item.get("summary", "") or ""

        # Initial frame: header + first slice of the headline so the matrix
        # isn't blank for the half-second before the scroll starts.
        self.matrix.clear()
        self._draw_header()
        if headline:
            self.matrix.draw_text((1, 12), headline[:32], font=self.font, fill=(255, 255, 255))
        if summary:
            self.matrix.draw_text((1, 22), summary[:32], font=self.font, fill=(170, 170, 170))
        self.matrix.render()
        self.sleepEvent.wait(0.6)

        # Scroll the headline horizontally across the middle region, then the
        # summary below it. Static header is repainted each frame via the
        # redraw callback so the scroll-rect clear doesn't wipe it.
        def _repaint_header():
            self._draw_header()

        if headline and text_width(self.font, headline) > self.matrix.width - 2:
            scroll_line(
                self.matrix, self.sleepEvent, self.font,
                headline, y=12, color=(255, 255, 255),
                region=(0, 11, self.matrix.width, 19),
                redraw_static=_repaint_header,
            )
        if summary and text_width(self.font, summary) > self.matrix.width - 2:
            scroll_line(
                self.matrix, self.sleepEvent, self.font,
                summary, y=22, color=(170, 170, 170),
                region=(0, 21, self.matrix.width, 29),
                redraw_static=_repaint_header,
            )

        # Hold the final frame briefly so the rotation feels paced. Without
        # this the next item starts the moment the scroll exits.
        self.sleepEvent.wait(min(self.rotation_rate, 2.0))

    def _draw_header(self):
        self.matrix.draw_rectangle((0, 0), (self.matrix.width, 9), fill=(0, 0, 0))
        self.matrix.draw_text((1, 0), "TEAM NEWS", font=self.font, fill=(120, 180, 255))

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header()
        msg = "no team" if self._resolve_team_id() is None else "no data"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)
