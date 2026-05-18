"""Draft Tracker board.

Live-updating NHL Entry Draft picks during the draft itself, and most-recent
completed-round picks otherwise. Falls back to rankings if no picks are
available yet.

Source: NHL public API (api-web.nhle.com), no auth required.

Failure policy: any fetch/parse error logs and renders an empty state. Never
raises into the render loop. The board can crash-free even if the entire
draft endpoint disappears.
"""

import logging
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._text import sanitize

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

DRAFT_PICKS_NOW_URL = "https://api-web.nhle.com/v1/draft/picks/now"
DRAFT_RANKINGS_NOW_URL = "https://api-web.nhle.com/v1/draft/rankings/now"


class DraftTrackerBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 6)))
        self.picks_to_show = max(1, int(self.get_config_value("picks_to_show", 5)))
        self.highlight_preferred = bool(self.get_config_value("highlight_preferred", True))
        self.update_freq_seconds = max(60, int(self.get_config_value("update_freq_minutes", 5)) * 60)

        self.font = data.config.layout.font
        self.font_large = data.config.layout.font_large

        # Cache: refreshed on a TTL, served stale on failure.
        self._cache_payload = None
        self._cache_ts = 0.0
        self._cache_kind = None  # "picks" or "rankings" or "empty"

    def _maybe_refresh(self):
        if (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_payload is not None:
            return
        # Try live/current picks first.
        picks = fetch_json(DRAFT_PICKS_NOW_URL)
        if picks and isinstance(picks.get("picks"), list) and picks["picks"]:
            self._cache_payload = picks
            self._cache_kind = "picks"
            self._cache_ts = time.time()
            return
        # No picks — try rankings (pre-draft prospect list).
        rankings = fetch_json(DRAFT_RANKINGS_NOW_URL)
        if rankings and isinstance(rankings.get("rankings"), list) and rankings["rankings"]:
            self._cache_payload = rankings
            self._cache_kind = "rankings"
            self._cache_ts = time.time()
            return
        # Both empty. Keep last successful cache if any; otherwise mark empty.
        if self._cache_payload is None:
            self._cache_kind = "empty"
            self._cache_ts = time.time()

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

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"DraftTrackerBoard: refresh failed: {e}", exc_info=True)

        try:
            if self._cache_kind == "picks":
                self._render_picks()
            elif self._cache_kind == "rankings":
                self._render_rankings()
            else:
                self._render_empty()
        except Exception as e:
            debug.error(f"DraftTrackerBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_picks(self):
        payload = self._cache_payload or {}
        picks = payload.get("picks") or []
        if not picks:
            self._render_empty()
            return
        # Show the most recent N picks. NHL returns in pick order; take tail.
        recent = picks[-self.picks_to_show:]
        pref_abbrevs = self._preferred_team_abbrevs() if self.highlight_preferred else set()
        year = payload.get("draftYear", "")
        round_num = payload.get("selectableRounds", [None])[-1] if payload.get("selectableRounds") else None

        self.matrix.clear()
        header = f"DRAFT {year}" if year else "NHL DRAFT"
        if round_num:
            header = f"{header}  R{round_num}"
        self._draw_header(header)

        y = 9
        for p in recent:
            team_ab = (p.get("teamAbbrev") or "").upper()
            pick_num = p.get("overallPick", "?")
            first = (p.get("firstName") or {}).get("default", "") or ""
            last = (p.get("lastName") or {}).get("default", "") or ""
            # Sanitize: NHL draft prospects often have accented names (Couture,
            # Vejmelka, Lehkonen, etc.) that don't render in the pixel font.
            name = sanitize((last or first or "TBD")).upper()
            line = f"#{pick_num} {team_ab} {name}"[:24]
            color = (255, 200, 0) if team_ab in pref_abbrevs else (255, 255, 255)
            self.matrix.draw_text((1, y), line, font=self.font, fill=color)
            y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_rankings(self):
        payload = self._cache_payload or {}
        rankings = (payload.get("rankings") or [])[: self.picks_to_show]
        year = payload.get("draftYear", "")
        self.matrix.clear()
        header = f"DRAFT {year} RANK" if year else "DRAFT RANK"
        self._draw_header(header)
        y = 9
        for r in rankings:
            rank = r.get("finalRank") or r.get("midtermRank") or "?"
            last = sanitize(r.get("lastName") or "").upper()
            pos = r.get("positionCode") or ""
            line = f"#{rank} {last} {pos}"[:24]
            self.matrix.draw_text((1, y), line, font=self.font, fill=(255, 255, 255))
            y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header("NHL DRAFT")
        self.matrix.draw_text((1, 12), "no data", font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _draw_header(self, text: str):
        # 64-wide and 128-wide displays both get the same simple top banner.
        self.matrix.draw_text((1, 0), text[:18], font=self.font, fill=(80, 180, 255))
