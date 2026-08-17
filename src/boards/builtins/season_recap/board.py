"""Season Recap board.

How your team finished the most recently completed season: record, points,
where they landed in the division and conference, goal differential, and
whether they made the playoffs.

Source: /v1/standings/now. Through the off-season that endpoint still serves
the completed season's final table (verified 2026-08-14: it returned the full
82-game 2025-26 standings), so no special end-of-season snapshot is needed.

Failure policy: never raises into the render loop; renders an empty state.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import preferred_team_abbrev
from boards.builtins._text import text_width

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"


def _ordinal(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    if 10 <= n % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")
    return f"{n}{suffix}"


class SeasonRecapBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 8)))
        self.pages_per_visit = max(1, int(self.get_config_value("pages_per_visit", 2)))
        self.update_freq_seconds = max(3600, int(self.get_config_value("update_freq_hours", 12)) * 3600)

        self.font = data.config.layout.font

        self._record = None
        self._cache_ts = 0.0
        self._cache_abbrev = None
        self._page = 0

    def _maybe_refresh(self):
        abbrev = preferred_team_abbrev(self.data)
        if not abbrev:
            self._record = None
            return
        fresh = (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_abbrev == abbrev
        if fresh and self._record:
            return
        payload = fetch_json(STANDINGS_URL)
        if not isinstance(payload, dict):
            return
        rows = payload.get("standings")
        if not isinstance(rows, list):
            return
        for row in rows:
            if (row.get("teamAbbrev") or {}).get("default", "").upper() != abbrev:
                continue
            self._record = {
                "season": row.get("seasonId"),
                "gp": row.get("gamesPlayed"),
                "w": row.get("wins"), "l": row.get("losses"), "otl": row.get("otLosses"),
                "pts": row.get("points"),
                "gf": row.get("goalFor"), "ga": row.get("goalAgainst"),
                "div": row.get("divisionName"), "div_seq": row.get("divisionSequence"),
                "conf": row.get("conferenceName"), "conf_seq": row.get("conferenceSequence"),
                "league_seq": row.get("leagueSequence"),
                "clinched": (row.get("clinchIndicator") or "").strip(),
            }
            self._cache_abbrev = abbrev
            self._cache_ts = time.time()
            debug.info(f"SeasonRecapBoard: {abbrev} {self._record['season']} "
                       f"{self._record['w']}-{self._record['l']}-{self._record['otl']}")
            return

    def _season_label(self):
        s = str((self._record or {}).get("season") or "")
        # 20252026 -> 25-26
        return f"{s[2:4]}-{s[6:8]}" if len(s) == 8 else ""

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"SeasonRecapBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._record:
                self._render_empty()
                return
            pages = (self._render_record, self._render_finish)
            for _ in range(min(self.pages_per_visit, len(pages))):
                if self.sleepEvent.is_set():
                    return
                pages[self._page % len(pages)]()
                self._page = (self._page + 1) % len(pages)
        except Exception as e:
            debug.error(f"SeasonRecapBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_record(self):
        r = self._record
        abbrev = self._cache_abbrev or ""
        self.matrix.clear()
        self._draw_header(f"{abbrev} {self._season_label()}".strip())

        rec = f"{r['w']}-{r['l']}-{r['otl']}"
        self.matrix.draw_text((1, 10), rec, font=self.font, fill=(255, 255, 255))

        pts = f"{r['pts']} PTS"
        self.matrix.draw_text((1, 18), pts, font=self.font, fill=(255, 200, 0))

        gf, ga = r.get("gf"), r.get("ga")
        if gf is not None and ga is not None:
            diff = gf - ga
            line = f"{gf}GF {ga}GA {'+' if diff >= 0 else ''}{diff}"
            self.matrix.draw_text((1, self.matrix.height - 7),
                                  self._fit(line, self.matrix.width - 2),
                                  font=self.font, fill=(140, 140, 140))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_finish(self):
        r = self._record
        abbrev = self._cache_abbrev or ""
        self.matrix.clear()
        self._draw_header(f"{abbrev} FINISH")

        div = (r.get("div") or "").upper()
        if r.get("div_seq"):
            self.matrix.draw_text((1, 10), self._fit(f"{_ordinal(r['div_seq'])} {div}", self.matrix.width - 2),
                                  font=self.font, fill=(255, 255, 255))
        if r.get("conf_seq"):
            conf = (r.get("conf") or "").upper()
            self.matrix.draw_text((1, 18), self._fit(f"{_ordinal(r['conf_seq'])} {conf}", self.matrix.width - 2),
                                  font=self.font, fill=(200, 200, 200))

        # clinchIndicator is 'x'/'y'/'z'/'p' when a playoff spot was clinched;
        # empty means they missed.
        made = bool(r.get("clinched"))
        msg = "MADE PLAYOFFS" if made else "MISSED PLAYOFFS"
        color = (80, 220, 120) if made else (200, 90, 90)
        self.matrix.draw_text((1, self.matrix.height - 7), self._fit(msg, self.matrix.width - 2),
                              font=self.font, fill=color)
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
        self._draw_header("SEASON RECAP")
        msg = "no team" if not preferred_team_abbrev(self.data) else "no data"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(min(self.rotation_rate, 3))
