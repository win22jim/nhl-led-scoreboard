"""Season Opener board.

Days until YOUR team's first game, with the opponent and whether it's home or
away — rather than the generic league-wide countdown that season_countdown
already provides.

Also covers the bit of the calendar people actually care about in September:
if training-camp/preseason games come first, it counts down to those and says
so, then switches to the regular-season opener once preseason is under way.

Source: /v1/club-schedule-season/{abbrev}/{season}. gameType 1 = preseason,
2 = regular season.

Skips itself once the opener has passed (the regular season is running and
this board has nothing to say). Configurable via ``skip_after_opener``.

Failure policy: never raises into the render loop; renders an empty state.
"""

import logging
import time
from datetime import date, datetime

from PIL import Image

from boards.base_board import BoardBase
from boards.builtins._external_fetch import fetch_json
from boards.builtins._team import current_season_id, preferred_team_abbrev
from boards.builtins._text import text_width
from renderer.logos import LogoRenderer

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")

SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{abbrev}/{season}"

GAMETYPE_PRESEASON = 1
GAMETYPE_REGULAR = 2


class SeasonOpenerBoard(BoardBase):
    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.rotation_rate = max(2, int(self.get_config_value("rotation_rate", 8)))
        self.show_preseason = bool(self.get_config_value("show_preseason", True))
        self.skip_after_opener = bool(self.get_config_value("skip_after_opener", True))
        self.update_freq_seconds = max(3600, int(self.get_config_value("update_freq_hours", 12)) * 3600)

        self.font = data.config.layout.font
        # Matches season_countdown's big-number treatment (04B_24 @24).
        self.font_count = data.config.layout.font_large_2
        # Global layout entry supplies the logo's position; see
        # config/layout/layout.json -> season_opener.logo
        self.layout = data.config.config.layout.get_board_layout("season_opener")

        self._games = []
        self._cache_ts = 0.0
        self._cache_key = None

    def _maybe_refresh(self):
        abbrev = preferred_team_abbrev(self.data)
        season = current_season_id(self.data)
        if not abbrev or not season:
            self._games = []
            return
        key = (abbrev, season)
        fresh = (time.time() - self._cache_ts) < self.update_freq_seconds and self._cache_key == key
        if fresh and self._games:
            return
        payload = fetch_json(SCHEDULE_URL.format(abbrev=abbrev, season=season))
        if not isinstance(payload, dict):
            return
        games = payload.get("games")
        if not isinstance(games, list):
            return
        parsed = []
        for g in games:
            gd = g.get("gameDate")
            if not gd:
                continue
            try:
                game_date = datetime.strptime(gd, "%Y-%m-%d").date()
            except Exception:
                continue
            home = (g.get("homeTeam") or {}).get("abbrev") or ""
            away = (g.get("awayTeam") or {}).get("abbrev") or ""
            parsed.append({
                "date": game_date,
                "type": g.get("gameType"),
                "home": home.upper(),
                "away": away.upper(),
                "is_home": home.upper() == abbrev,
                "opponent": (away if home.upper() == abbrev else home).upper(),
            })
        parsed.sort(key=lambda x: x["date"])
        self._games = parsed
        self._cache_key = key
        self._cache_ts = time.time()
        debug.info(f"SeasonOpenerBoard: {len(parsed)} games for {abbrev} {season}")

    def _next_opener(self):
        """(game, label) for the next milestone game, or (None, None).

        Preseason first while it's still ahead, then the regular-season opener.
        """
        today = date.today()
        regular = [g for g in self._games if g["type"] == GAMETYPE_REGULAR and g["date"] >= today]
        if self.show_preseason:
            pre = [g for g in self._games if g["type"] == GAMETYPE_PRESEASON and g["date"] >= today]
            # Only treat preseason as the milestone if it comes first — once
            # camp games are under way the regular-season opener is the story.
            if pre and (not regular or pre[0]["date"] < regular[0]["date"]):
                return pre[0], "PRESEASON"
        if regular:
            return regular[0], "OPENER"
        return None, None

    def render(self):
        try:
            self._maybe_refresh()
        except Exception as e:
            debug.error(f"SeasonOpenerBoard: refresh failed: {e}", exc_info=True)

        try:
            game, label = self._next_opener()
            if game is None:
                if self.skip_after_opener and self._games:
                    # Season is under way (or over) — nothing to count down to.
                    debug.debug("SeasonOpenerBoard: no upcoming opener, skipping")
                    self.sleepEvent.wait(0.1)
                    return
                self._render_empty()
                return
            self._render_countdown(game, label)
        except Exception as e:
            debug.error(f"SeasonOpenerBoard: render failed: {e}", exc_info=True)
            self._render_empty()

    def _render_countdown(self, game, label):
        """Laid out like the NHL season_countdown board — team logo on the
        right behind a gradient, big day count on the left — but counting down
        to this team's own first game."""
        days = (game["date"] - date.today()).days
        abbrev = preferred_team_abbrev(self.data) or ""

        self.matrix.clear()
        self._draw_logo(abbrev)

        if days <= 0:
            self.matrix.draw_text((1, 1), "GAME", font=self.font, fill=(255, 200, 0))
            self.matrix.draw_text((1, 8), "DAY!", font=self.font_count, fill=(255, 165, 0))
        else:
            # 24px digits occupy rows 4-18 when drawn at y=1; the label sits at
            # 18 so it clears them by a row.
            self.matrix.draw_text((1, 1), str(days), font=self.font_count, fill=(255, 165, 0))
            self.matrix.draw_text((1, 18), "DAYS TIL" if days != 1 else "DAY TIL",
                                  font=self.font, fill=(255, 165, 0))

        # Bottom line: PRESEASON / OPENER plus the matchup.
        vs = f"{'VS' if game['is_home'] else '@'} {game['opponent']}"
        bottom = f"{label[:3]} {vs}" if days > 0 else vs
        self.matrix.draw_text((1, self.matrix.height - 7),
                              self._fit(bottom, self.matrix.width - 2),
                              font=self.font, fill=(255, 255, 255))
        self.matrix.render()
        self.sleepEvent.wait(self.rotation_rate)

    def _draw_logo(self, abbrev: str):
        """Team logo on the right, dimmed by the same center gradient the
        season_countdown board uses so the text stays readable over it."""
        if not abbrev:
            return
        try:
            logo_renderer = LogoRenderer(
                self.matrix,
                self.data.config,
                self.layout.logo,
                abbrev,
                "season_opener",
            )
            logo_renderer.render()
        except Exception as e:
            # A missing/undownloadable logo must not cost us the countdown.
            debug.warning(f"SeasonOpenerBoard: could not draw {abbrev} logo: {e}")
        try:
            gradient = Image.open(
                f"assets/images/{self.matrix.width}x{self.matrix.height}_scoreboard_center_gradient.png"
            )
            self.matrix.draw_image((-4, 0), gradient)
        except Exception as e:
            debug.debug(f"SeasonOpenerBoard: no gradient asset: {e}")

    def _fit(self, text: str, max_px: int) -> str:
        while text and text_width(self.font, text) > max_px:
            text = text[:-1]
        return text

    def _draw_header(self, text: str):
        self.matrix.draw_text((1, 0), self._fit(text, self.matrix.width - 2),
                              font=self.font, fill=(80, 180, 255))

    def _render_empty(self):
        self.matrix.clear()
        self._draw_header("SEASON OPENER")
        msg = "no team" if not preferred_team_abbrev(self.data) else "no schedule"
        self.matrix.draw_text((1, 12), msg, font=self.font, fill=(150, 150, 150))
        self.matrix.render()
        self.sleepEvent.wait(min(self.rotation_rate, 3))
