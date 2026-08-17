"""Draft Class board.

Your team's haul from the most recent NHL Entry Draft — every pick across all
seven rounds, paged a few at a time.

Where draft_tracker answers "what's happening at the draft" (and stops a week
after it ends), this answers "who did we actually get", which stays interesting
all summer as those names show up in prospect camp and preseason.

Source: /v1/draft/picks/{year}/all — 224 picks with round, overall number,
position and junior/college club. Filtered to the preferred team.

Failure policy: never raises into the render loop; renders an empty state.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import preferred_team_abbrev
from boards.builtins._text import sanitize, text_width

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

DRAFT_ALL_URL = "https://api-web.nhle.com/v1/draft/picks/{year}/all"
DRAFT_NOW_URL = "https://api-web.nhle.com/v1/draft/picks/now"


class DraftClassBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 6)))
        self.picks_per_page = max(1, int(self.get_config_value("picks_per_page", 3)))
        self.max_pages = max(1, int(self.get_config_value("max_pages_per_visit", 2)))
        self.show_club = bool(self.get_config_value("show_club", True))
        # Draft classes don't change; a long TTL is plenty.
        self.update_freq_seconds = max(3600, int(self.get_config_value("update_freq_hours", 24)) * 3600)

        self.font = data.config.layout.font

        self._picks = []
        self._year = None
        self._cache_ts = 0.0
        self._cache_abbrev = None
        self._page = 0

    def _draft_year(self):
        """Most recent draft year, taken from the API rather than the clock.

        Deriving it from today's date would be wrong for half the year — before
        late June the "most recent" draft is the previous calendar year's.
        """
        payload = fetch_json(DRAFT_NOW_URL)
        if isinstance(payload, dict) and payload.get("draftYear"):
            try:
                return int(payload["draftYear"])
            except Exception:
                return None
        return None

    def _maybe_refresh(self):
        abbrev = preferred_team_abbrev(self.data)
        if not abbrev:
            self._picks = []
            return
        fresh = (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_abbrev == abbrev
        if fresh and self._picks:
            return

        year = self._year or self._draft_year()
        if not year:
            return
        payload = fetch_json(DRAFT_ALL_URL.format(year=year))
        if not isinstance(payload, dict):
            return
        picks = payload.get("picks")
        if not isinstance(picks, list):
            return

        mine = []
        for p in picks:
            if (p.get("teamAbbrev") or "").upper() != abbrev:
                continue
            last = (p.get("lastName") or {}).get("default") or ""
            first = (p.get("firstName") or {}).get("default") or ""
            if not (last or first):
                continue
            mine.append({
                "round": p.get("round"),
                "overall": p.get("overallPick"),
                "name": sanitize(last or first).upper(),
                "pos": (p.get("positionCode") or "").upper(),
                "club": sanitize(p.get("amateurClubName") or "").upper(),
            })
        mine.sort(key=lambda x: (x["overall"] or 999))
        self._picks = mine
        self._year = year
        self._cache_abbrev = abbrev
        self._cache_ts = time.time()
        debug.info(f"DraftClassBoard: {len(mine)} {year} picks for {abbrev}")

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"DraftClassBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._picks:
                self._render_empty()
                return
            pages = max(1, (len(self._picks) + self.picks_per_page - 1) // self.picks_per_page)
            for _ in range(min(self.max_pages, pages)):
                if self.sleepEvent.is_set():
                    return
                self._render_page(self._page % pages, pages)
                self._page = (self._page + 1) % pages
        except Exception as e:
            debug.error(f"DraftClassBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_page(self, page: int, pages: int):
        start = page * self.picks_per_page
        chunk = self._picks[start:start + self.picks_per_page]

        self.matrix.clear()
        abbrev = self._cache_abbrev or ""
        yy = str(self._year)[-2:] if self._year else ""
        self._draw_header(f"{abbrev} DRAFT {yy}".strip(), page, pages)

        y = 10
        for p in chunk:
            rnd = p["round"] or "?"
            head = f"R{rnd} #{p['overall']}"
            self.matrix.draw_text((1, y), head, font=self.font, fill=(255, 200, 0))
            name = f"{p['name']} {p['pos']}".strip()
            x = text_width(self.font, head) + 3
            name = self._fit(name, self.matrix.width - x - 1)
            self.matrix.draw_text((x, y), name, font=self.font, fill=(255, 255, 255))
            y += 7
            if self.show_club and p["club"] and y <= self.matrix.height - 6:
                club = self._fit(p["club"], self.matrix.width - 3)
                self.matrix.draw_text((3, y), club, font=self.font, fill=(130, 130, 130))
                y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _fit(self, text: str, max_px: int) -> str:
        while text and text_width(self.font, text) > max_px:
            text = text[:-1]
        return text

    def _draw_header(self, text: str, page=None, pages=None):
        self.matrix.draw_text((1, 0), self._fit(text, self.matrix.width - 14),
                              font=self.font, fill=(80, 180, 255))
        if pages and pages > 1:
            label = f"{(page or 0) + 1}/{pages}"
            x = max(0, self.matrix.width - text_width(self.font, label) - 1)
            self.matrix.draw_text((x, 0), label, font=self.font, fill=(60, 130, 190))

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header("DRAFT CLASS")
        msg = "no team" if not preferred_team_abbrev(self.data) else "no picks"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(min(self.rotation_rate, 3))
