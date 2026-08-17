"""Draft Tracker board.

Shows the right thing for where we are in the draft cycle:

* **Before the draft** — the prospect rankings (who's projected to go).
* **During the draft** — the most recent picks as they happen.
* **Just after the draft** — the top of round 1, the picks people talk about.
* **A week after the draft** — nothing; the board skips itself so it isn't
  still showing June's news in September. Configurable via
  ``skip_days_after_draft`` (0 disables the skip).

Source: NHL public API (api-web.nhle.com), no auth required.

Accuracy notes (rewritten 2026-08-14 — the first version got these wrong):

* ``/v1/draft/picks/now`` 307-redirects to ``/v1/draft/picks/{year}/{round}``
  and carries a ``state`` field ("over" once the draft is complete) plus
  ``broadcastStartTimeUTC``. The old code ignored both, so it could not tell
  pre-draft from post-draft at all.
* The round label came from ``selectableRounds[-1]``, which is just the highest
  *selectable* round — always 7. It therefore labelled round 1 picks as "R7".
  The round is per-pick (``pick["round"]``); that's what we use now.
* It showed ``picks[-5:]`` — the tail. For a completed round that's picks
  #28-32, the least interesting ones. A finished draft now shows the top of
  round 1; only a live draft shows the most recent picks.
* It fell back to rankings whenever picks were empty, which post-draft meant
  showing the already-drafted class as though they were still prospects.
  Rankings are now used only before the draft.

Failure policy: any fetch/parse error logs and renders an empty state. Never
raises into the render loop.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import preferred_team_abbrevs
from boards.builtins._text import sanitize, text_width

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

DRAFT_PICKS_NOW_URL = "https://api-web.nhle.com/v1/draft/picks/now"
DRAFT_RANKINGS_NOW_URL = "https://api-web.nhle.com/v1/draft/rankings/now"

# The NHL Entry Draft runs over two days; broadcastStartTimeUTC is round 1's
# start, so the event wraps up roughly a day later.
DRAFT_DURATION_DAYS = 1


class DraftTrackerBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 8)))
        self.picks_to_show = max(1, int(self.get_config_value("picks_to_show", 4)))
        self.highlight_preferred = bool(self.get_config_value("highlight_preferred", True))
        self.update_freq_seconds = max(60, int(self.get_config_value("update_freq_minutes", 5)) * 60)
        # Days after the draft wraps before this board stops showing.
        # Default 7. Set to 0 to show year-round.
        self.skip_days_after_draft = int(self.get_config_value("skip_days_after_draft", 7) or 0)

        self.font = data.config.layout.font

        self._picks_payload = None
        self._rankings_payload = None
        self._cache_ts = 0.0

    # ---------------------------------------------------------------- fetching

    def _maybe_refresh(self):
        if (time.time() - self._cache_ts) < self.update_freq_seconds and self._picks_payload is not None:
            return
        picks = fetch_json(DRAFT_PICKS_NOW_URL)
        if isinstance(picks, dict):
            self._picks_payload = picks
            self._cache_ts = time.time()
        # Rankings are only needed before the draft — don't spend a request on
        # them otherwise (post-draft they're stale by definition).
        if not self._draft_is_over():
            rankings = fetch_json(DRAFT_RANKINGS_NOW_URL)
            if isinstance(rankings, dict):
                self._rankings_payload = rankings
                self._cache_ts = time.time()

    # ------------------------------------------------------------- draft state

    def _draft_is_over(self):
        payload = self._picks_payload or {}
        return str(payload.get("state", "")).lower() == "over"

    def _draft_start(self):
        """UTC datetime the draft began, or None if unavailable."""
        raw = (self._picks_payload or {}).get("broadcastStartTimeUTC")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception as e:
            debug.debug(f"DraftTrackerBoard: unparseable broadcastStartTimeUTC {raw!r}: {e}")
            return None

    def _days_since_draft_end(self):
        """Whole days since the draft wrapped up. None if we can't tell."""
        start = self._draft_start()
        if start is None:
            return None
        end = start + timedelta(days=DRAFT_DURATION_DAYS)
        return (datetime.now(timezone.utc) - end).days

    def _should_skip(self):
        if not self.skip_days_after_draft:
            return False
        if not self._draft_is_over():
            return False
        days = self._days_since_draft_end()
        if days is None:
            return False
        return days >= self.skip_days_after_draft

    # ---------------------------------------------------------------- rendering

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"DraftTrackerBoard: refresh failed: {e}", exc_info=True)

        try:
            if self._should_skip():
                debug.debug(
                    "Draft board skipped: {}d since draft >= {}d threshold".format(
                        self._days_since_draft_end(), self.skip_days_after_draft
                    )
                )
                # Brief pause so the rotation advances normally rather than
                # spinning on a no-op render.
                self.sleepEvent.wait(0.1)
                return

            picks = (self._picks_payload or {}).get("picks") or []
            drafted = [p for p in picks if (p.get("lastName") or {}).get("default")]

            if drafted:
                self._render_picks(drafted, finished=self._draft_is_over())
            elif self._rankings_payload:
                self._render_rankings()
            else:
                self._render_empty()
        except Exception as e:
            debug.error(f"DraftTrackerBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_picks(self, picks, finished: bool):
        """A finished draft shows the TOP of the round; a live one shows the
        most recent picks, which is what you actually want while it's running."""
        selection = picks[: self.picks_to_show] if finished else picks[-self.picks_to_show:]

        # Round comes from the picks themselves — selectableRounds is just
        # [1..7] and told us nothing about what's on screen.
        rounds = {p.get("round") for p in selection if p.get("round")}
        round_num = rounds.pop() if len(rounds) == 1 else None

        year = (self._picks_payload or {}).get("draftYear", "")
        pref = preferred_team_abbrevs(self.data) if self.highlight_preferred else set()

        self.matrix.clear()
        header = f"DRAFT {year}" if year else "NHL DRAFT"
        if round_num:
            header = f"{header} R{round_num}"
        self._draw_header(header, live=not finished)

        y = 10
        for p in selection:
            team_ab = (p.get("teamAbbrev") or "").upper()
            pick_num = p.get("overallPick", "?")
            last = (p.get("lastName") or {}).get("default") or ""
            first = (p.get("firstName") or {}).get("default") or ""
            name = sanitize(last or first or "TBD").upper()
            line = f"{pick_num}.{team_ab} {name}"
            # Trim to fit the panel rather than a hardcoded character count —
            # the old [:24] overflowed on a 64px display.
            while line and text_width(self.font, line) > self.matrix.width - 2:
                line = line[:-1]
            color = (255, 200, 0) if team_ab in pref else (255, 255, 255)
            self.matrix.draw_text((1, y), line, font=self.font, fill=color)
            y += 7
            if y > self.matrix.height - 6:
                break
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _render_rankings(self):
        payload = self._rankings_payload or {}
        rankings = (payload.get("rankings") or [])[: self.picks_to_show]
        year = payload.get("draftYear", "")
        self.matrix.clear()
        self._draw_header(f"{year} PROSPECTS" if year else "PROSPECTS")
        y = 10
        for r in rankings:
            rank = r.get("finalRank") or r.get("midtermRank") or "?"
            last = sanitize(r.get("lastName") or "").upper()
            pos = r.get("positionCode") or ""
            line = f"{rank}.{last} {pos}".strip()
            while line and text_width(self.font, line) > self.matrix.width - 2:
                line = line[:-1]
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
        self.sleepEvent.wait(min(self.rotation_rate, 3))

    def _draw_header(self, text: str, live: bool = False):
        while text and text_width(self.font, text) > self.matrix.width - 2:
            text = text[:-1]
        self.matrix.draw_text((1, 0), text, font=self.font, fill=(80, 180, 255))
        if live:
            # Small red dot, top-right, while the draft is actually running.
            self.matrix.draw_rectangle((self.matrix.width - 3, 1), (2, 2), fill=(255, 40, 40))
