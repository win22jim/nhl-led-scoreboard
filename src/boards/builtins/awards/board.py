"""Awards board.

Cycles through NHL trophies. For each trophy, shows trophy name and the most
recent winner (parsed from the trophy description HTML). Awards date is
roughly late June, so a 24-hour refresh is plenty.

Source: records.nhl.com/site/api/trophy. The current-winner field is HTML
prose with no structured "currentWinner" key, so we extract it best-effort
via regex from descriptions like "2024-25 Winner: The Florida Panthers...".
If the regex fails to match, we show only the trophy name and brief
description.

Failure policy: defensive try/except around every fetch and parse path.
Renders an empty state rather than raising.
"""

import logging
import re
import time

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

TROPHY_URL = "https://records.nhl.com/site/api/trophy"

# Curated "major" trophy ID list from the Records API. IDs are stable across
# years. If NHL renumbers something this list goes stale silently; the board
# falls back to showing the trophy name without a curated subset.
MAJOR_TROPHY_SHORT_NAMES = {
    "Stanley Cup", "Hart", "Norris", "Vezina", "Selke", "Calder",
    "Conn Smythe", "Art Ross", "Rocket Richard", "Lady Byng",
    "Ted Lindsay", "Jack Adams", "Presidents' Trophy", "Bill Masterton",
    "King Clancy", "Mark Messier",
}

# Regex for "2024-25 Winner: ..." prose embedded in the description field.
_WINNER_RE = re.compile(
    r"(?:(\d{4}-\d{2,4})\s*[Ww]inner[s]?\s*:\s*)([^\.<]+)",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    """Cheap HTML strip — no BeautifulSoup dep for this one field."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_winner(description_html: str):
    """Return (season, winner) from a trophy description, or (None, None)."""
    text = _strip_html(description_html)
    m = _WINNER_RE.search(text)
    if not m:
        return None, None
    season = m.group(1).strip()
    winner = m.group(2).strip().rstrip(",;")
    return season, winner


class AwardsBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 6)))
        self.trophy_set = str(self.get_config_value("trophy_set", "major")).lower()
        self.update_freq_seconds = max(3600, int(self.get_config_value("update_freq_hours", 24)) * 3600)

        self.font = data.config.layout.font

        self._cache_trophies = None
        self._cache_ts = 0.0
        self._cursor = 0  # current trophy index for paging across render() calls

    def _maybe_refresh(self):
        if self._cache_trophies and (time.time() - self._cache_ts) < self.update_freq_seconds:
            return
        payload = fetch_json(TROPHY_URL, timeout=10.0)
        if not payload:
            return
        trophies = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(trophies, list):
            return
        filtered = []
        for t in trophies:
            short = (t.get("shortName") or "").strip()
            if not short:
                continue
            if self.trophy_set == "major":
                # Match either an exact "shortName" or a containing relationship,
                # since NHL is inconsistent about "Stanley Cup" vs "Stanley Cup Trophy".
                if not any(m in short for m in MAJOR_TROPHY_SHORT_NAMES):
                    continue
            filtered.append(t)
        if filtered:
            self._cache_trophies = filtered
            self._cache_ts = time.time()
            debug.info(f"AwardsBoard: loaded {len(filtered)} trophies ({self.trophy_set})")

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"AwardsBoard: refresh failed: {e}", exc_info=True)

        try:
            if not self._cache_trophies:
                self._render_empty()
                return
            trophy = self._cache_trophies[self._cursor % len(self._cache_trophies)]
            self._cursor = (self._cursor + 1) % max(1, len(self._cache_trophies))
            self._render_trophy(trophy)
        except Exception as e:
            debug.error(f"AwardsBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_trophy(self, trophy: dict):
        self.matrix.clear()
        short = (trophy.get("shortName") or "TROPHY").upper()
        self.matrix.draw_text((1, 0), short[:18], font=self.font, fill=(255, 200, 0))
        season, winner = _parse_winner(trophy.get("description", ""))
        if winner:
            self.matrix.draw_text((1, 9), (season or "")[:12], font=self.font, fill=(150, 150, 150))
            # Winner can be long ("The Florida Panthers"). Wrap roughly.
            self._draw_wrapped(winner, x=1, y=17, max_lines=3, color=(255, 255, 255))
        else:
            # Fall back to the briefDescription if no winner parses.
            brief = _strip_html(trophy.get("briefDescription", "") or "")
            self._draw_wrapped(brief[:60], x=1, y=9, max_lines=4, color=(180, 180, 180))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_empty(self):
        self.matrix.clear()
        self.matrix.draw_text((1, 0), "NHL AWARDS", font=self.font, fill=(255, 200, 0))
        self.matrix.draw_text((1, 12), "no data", font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _draw_wrapped(self, text: str, x: int, y: int, max_lines: int, color):
        """Naive char-budget wrap. Width tuned to the 64-wide layout (~18 chars
        per line at the 04B_24 font); 128-wide just looks half-empty."""
        line_budget = 18
        words = text.split()
        line = ""
        lines = []
        for w in words:
            candidate = (line + " " + w).strip()
            if len(candidate) > line_budget:
                if line:
                    lines.append(line)
                line = w
            else:
                line = candidate
        if line:
            lines.append(line)
        for i, ln in enumerate(lines[:max_lines]):
            self.matrix.draw_text((x, y + i * 7), ln, font=self.font, fill=color)
