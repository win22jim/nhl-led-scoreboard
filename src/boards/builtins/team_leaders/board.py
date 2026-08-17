"""Team Leaders board.

Your team's statistical leaders from the most recently completed season —
points, goals, assists, and the goalie's record — cycled one category per
screen.

Source: /v1/club-stats/{abbrev}/{season}/2 (gameType 2 = regular season).
Uses the PREVIOUS season id: data.status.season_id rolls over to the upcoming
season long before it starts (in August 2026 it already reads 20262027), so
asking for the "current" season off-season returns nothing.

Failure policy: never raises into the render loop; renders an empty state.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import preferred_team_abbrev, previous_season_id
from boards.builtins._text import sanitize, text_width

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

CLUB_STATS_URL = "https://api-web.nhle.com/v1/club-stats/{abbrev}/{season}/2"

# (config key, screen title, stat field, suffix)
SKATER_CATEGORIES = [
    ("points", "POINTS", "points", "P"),
    ("goals", "GOALS", "goals", "G"),
    ("assists", "ASSISTS", "assists", "A"),
]


class TeamLeadersBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 7)))
        self.leaders_to_show = max(1, int(self.get_config_value("leaders_to_show", 3)))
        self.categories = self.get_config_value("categories", ["points", "goals", "assists"])
        self.show_goalie = bool(self.get_config_value("show_goalie", True))
        self.categories_per_visit = max(1, int(self.get_config_value("categories_per_visit", 2)))
        self.update_freq_seconds = max(3600, int(self.get_config_value("update_freq_hours", 24)) * 3600)

        self.font = data.config.layout.font

        self._skaters = []
        self._goalies = []
        self._season = None
        self._cache_ts = 0.0
        self._cache_abbrev = None
        self._page = 0

    def _maybe_refresh(self):
        abbrev = preferred_team_abbrev(self.data)
        season = previous_season_id(self.data)
        if not abbrev or not season:
            self._skaters = []
            return
        fresh = (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_abbrev == abbrev
        if fresh and self._skaters:
            return
        payload = fetch_json(CLUB_STATS_URL.format(abbrev=abbrev, season=season))
        if not isinstance(payload, dict):
            return
        self._skaters = [s for s in (payload.get("skaters") or []) if isinstance(s, dict)]
        self._goalies = [g for g in (payload.get("goalies") or []) if isinstance(g, dict)]
        self._season = season
        self._cache_abbrev = abbrev
        self._cache_ts = time.time()
        debug.info(f"TeamLeadersBoard: {abbrev} {season} — "
                   f"{len(self._skaters)} skaters, {len(self._goalies)} goalies")

    def _season_label(self):
        s = str(self._season or "")
        return f"{s[2:4]}-{s[6:8]}" if len(s) == 8 else ""

    @staticmethod
    def _name(entry):
        last = (entry.get("lastName") or {})
        first = (entry.get("firstName") or {})
        last = last.get("default") if isinstance(last, dict) else last
        first = first.get("default") if isinstance(first, dict) else first
        return sanitize(last or first or "").upper()

    def _pages(self):
        """Ordered list of render callables for the categories in use."""
        pages = []
        wanted = [c for c in (self.categories or []) if isinstance(c, str)]
        for key, title, field, suffix in SKATER_CATEGORIES:
            if key in wanted:
                pages.append(lambda t=title, f=field, s=suffix: self._render_skaters(t, f, s))
        if self.show_goalie and self._goalies:
            pages.append(self._render_goalie)
        return pages

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"TeamLeadersBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._skaters:
                self._render_empty()
                return
            pages = self._pages()
            if not pages:
                self._render_empty()
                return
            for _ in range(min(self.categories_per_visit, len(pages))):
                if self.sleepEvent.is_set():
                    return
                pages[self._page % len(pages)]()
                self._page = (self._page + 1) % len(pages)
        except Exception as e:
            debug.error(f"TeamLeadersBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_skaters(self, title: str, field: str, suffix: str):
        ranked = sorted(
            (s for s in self._skaters if isinstance(s.get(field), (int, float))),
            key=lambda s: s.get(field, 0), reverse=True,
        )[: self.leaders_to_show]
        if not ranked:
            self._render_empty()
            return

        self.matrix.clear()
        self._draw_header(f"{title} {self._season_label()}".strip())

        y = 10
        for i, s in enumerate(ranked):
            value = f"{int(s.get(field, 0))}{suffix}"
            vw = text_width(self.font, value)
            name = self._fit(self._name(s), self.matrix.width - vw - 4)
            # Leader in the team's accent colour so the top line reads first.
            color = (255, 200, 0) if i == 0 else (255, 255, 255)
            self.matrix.draw_text((1, y), name, font=self.font, fill=color)
            self.matrix.draw_text((self.matrix.width - vw - 1, y), value, font=self.font, fill=color)
            y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_goalie(self):
        ranked = sorted(self._goalies, key=lambda g: g.get("gamesStarted") or g.get("gamesPlayed") or 0,
                        reverse=True)
        if not ranked:
            self._render_empty()
            return
        g = ranked[0]
        self.matrix.clear()
        self._draw_header(f"GOALIE {self._season_label()}".strip())

        self.matrix.draw_text((1, 10), self._fit(self._name(g), self.matrix.width - 2),
                              font=self.font, fill=(255, 200, 0))
        rec = f"{int(g.get('wins') or 0)}-{int(g.get('losses') or 0)}-{int(g.get('overtimeLosses') or 0)}"
        self.matrix.draw_text((1, 18), rec, font=self.font, fill=(255, 255, 255))

        gaa = g.get("goalsAgainstAverage")
        svp = g.get("savePercentage")
        bits = []
        if isinstance(gaa, (int, float)):
            bits.append(f"{gaa:.2f} GAA")
        if isinstance(svp, (int, float)):
            bits.append(f"{svp:.3f}".lstrip("0") + " SV")
        if bits:
            self.matrix.draw_text((1, self.matrix.height - 7),
                                  self._fit(" ".join(bits), self.matrix.width - 2),
                                  font=self.font, fill=(140, 140, 140))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _fit(self, text: str, max_px: int) -> str:
        while text and text_width(self.font, text) > max_px:
            text = text[:-1]
        return text

    def _draw_header(self, text: str):
        self.matrix.draw_text((1, 0), self._fit(text, self.matrix.width - 2),
                              font=self.font, fill=(80, 180, 255))

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header("TEAM LEADERS")
        msg = "no team" if not preferred_team_abbrev(self.data) else "no stats"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(min(self.rotation_rate, 3))
