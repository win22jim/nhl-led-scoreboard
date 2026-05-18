"""Free Agency board.

Cycles through recently-signed free agents and/or top remaining unsigned
free agents. Source: spotrac.com (HTML scrape — there is no official NHL
free agency feed, and PuckPedia / CapFriendly are unavailable as
no-auth options).

Failure policy: any HTTP/parse failure renders an empty state. BeautifulSoup
is the only added Python dep — the plugin loader will skip this board if
beautifulsoup4 is missing rather than crashing on import.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_text

from . import __board_name__, __description__, __version__

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None  # board will render empty state

debug = logging.getLogger("scoreboard")

FREE_AGENTS_URL = "https://www.spotrac.com/nhl/free-agents/"


class FreeAgencyBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 6)))
        self.mode = str(self.get_config_value("mode", "signings")).lower()
        if self.mode not in ("signings", "available", "both"):
            self.mode = "signings"
        self.max_entries = max(1, int(self.get_config_value("max_entries", 10)))
        self.preferred_teams_only = bool(self.get_config_value("preferred_teams_only", False))
        self.update_freq_seconds = max(600, int(self.get_config_value("update_freq_minutes", 60)) * 60)

        self.font = data.config.layout.font

        # Parsed buckets: list of dicts with {player, team, type/contract}
        self._cache_signed = []
        self._cache_available = []
        self._cache_ts = 0.0
        self._cursor = 0

    def _preferred_team_abbrevs(self):
        try:
            ids = set(self.data.pref_teams or [])
        except Exception:
            return set()
        if not ids:
            return set()
        abbrevs = set()
        teams_info = getattr(self.data, "teams_info", {}) or {}
        for tid in ids:
            t = teams_info.get(tid) or teams_info.get(str(tid))
            if not t:
                continue
            ab = getattr(getattr(t, "details", None), "abbrev", None)
            if ab:
                abbrevs.add(ab)
        return abbrevs

    def _maybe_refresh(self):
        if (time.time() - self._cache_ts) < self.update_freq_seconds and (self._cache_signed or self._cache_available):
            return
        if BeautifulSoup is None:
            debug.warning("FreeAgencyBoard: beautifulsoup4 not installed, skipping fetch")
            return
        html = fetch_text(FREE_AGENTS_URL, timeout=15.0)
        if not html:
            return
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            debug.error(f"FreeAgencyBoard: BS4 parse failed: {e}")
            return
        signed, available = [], []
        # Spotrac renders two tables; the signed one carries "signed" in its
        # class string and the available one carries "available". We bin rows
        # by which table they belong to, then extract Player + Prev Team
        # columns. Column order is stable enough to use positions.
        for table in soup.find_all("table"):
            cls = " ".join(table.get("class") or []).lower()
            tbody = table.find("tbody")
            if not tbody:
                continue
            target = signed if "signed" in cls else (available if "available" in cls else None)
            if target is None:
                continue
            for tr in tbody.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 4:
                    continue
                player = cells[0]
                # Heuristic: previous team column varies in position depending on
                # whether contract details are visible. Search for a 2-4 char
                # uppercase token among cells — that's the team abbrev.
                team = ""
                for c in cells[1:]:
                    tok = c.strip()
                    if 2 <= len(tok) <= 4 and tok.isupper() and tok.isalpha():
                        team = tok
                        break
                if not player:
                    continue
                target.append({"player": player[:18], "team": team})
                if len(target) >= self.max_entries * 4:  # over-fetch then filter
                    break
        self._cache_signed = signed
        self._cache_available = available
        self._cache_ts = time.time()
        debug.info(f"FreeAgencyBoard: parsed {len(signed)} signed, {len(available)} available")

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"FreeAgencyBoard: refresh failed: {e}", exc_info=True)

        try:
            entries = self._select_entries()
            if not entries:
                self._render_empty()
                return
            self._render_entries(entries)
        except Exception as e:
            debug.error(f"FreeAgencyBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _select_entries(self):
        if self.mode == "signings":
            base = list(self._cache_signed)
        elif self.mode == "available":
            base = list(self._cache_available)
        else:
            base = list(self._cache_signed) + list(self._cache_available)
        if self.preferred_teams_only:
            abbrevs = self._preferred_team_abbrevs()
            if abbrevs:
                base = [e for e in base if e.get("team") in abbrevs]
        return base[: self.max_entries]

    def _render_entries(self, entries):
        self.matrix.clear()
        header = {
            "signings": "FA SIGNINGS",
            "available": "TOP FAs",
            "both": "FREE AGENCY",
        }.get(self.mode, "FREE AGENCY")
        self.matrix.draw_text((1, 0), header[:18], font=self.font, fill=(120, 220, 120))
        y = 9
        for e in entries:
            team = e.get("team") or "FA"
            player = e.get("player") or ""
            line = f"{team} {player}"[:24]
            self.matrix.draw_text((1, y), line, font=self.font, fill=(255, 255, 255))
            y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_empty(self):
        self.matrix.clear()
        self.matrix.draw_text((1, 0), "FREE AGENCY", font=self.font, fill=(120, 220, 120))
        msg = "no data"
        if BeautifulSoup is None:
            msg = "needs bs4"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)
