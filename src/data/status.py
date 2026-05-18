from datetime import datetime, date
import logging

import requests

from nhl_api import current_season_info, next_season_info

debug = logging.getLogger("scoreboard")


# api-web.nhle.com/v1/season returns just a flat list of season IDs (ints),
# not dicts. The actual season-window dates live on the stats endpoint below,
# which we hit directly so is_offseason / is_playoff can work.
_STATS_SEASON_URL = "https://api.nhle.com/stats/rest/en/season"


def _parse_api_date(value: str):
    """The NHL stats season endpoint returns dates as ISO datetimes with a
    time suffix like '2026-04-17T00:00:00'. Older code assumed bare 'YYYY-MM-DD',
    so strptime("%Y-%m-%d") fails on whatever the API actually returns now.
    Strip the time portion safely and return a date or None."""
    if not isinstance(value, str) or not value:
        return None
    head = value.split("T", 1)[0]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_season_window(season_id):
    """Return a dict of {regularSeasonStartDate, regularSeasonEndDate,
    seasonEndDate} as date-only strings for the given season id. Returns
    an empty dict on any failure so callers can guard on emptiness."""
    try:
        r = requests.get(
            _STATS_SEASON_URL,
            params={"cayenneExp": f"id={season_id}"},
            headers={"User-Agent": "Mozilla/5.0 nhl-led-scoreboard"},
            timeout=8,
        )
        if r.status_code != 200:
            debug.warning(f"_fetch_season_window: HTTP {r.status_code}")
            return {}
        payload = r.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            return {}
        row = rows[0]
        out = {}
        rs_start = row.get("startDate") or row.get("regularSeasonStartDate")
        rs_end = row.get("regularSeasonEndDate")
        s_end = row.get("endDate") or row.get("seasonEndDate")
        if rs_start:
            out["regularSeasonStartDate"] = rs_start.split("T", 1)[0]
        if rs_end:
            out["regularSeasonEndDate"] = rs_end.split("T", 1)[0]
        if s_end:
            out["seasonEndDate"] = s_end.split("T", 1)[0]
        return out
    except Exception as e:
        debug.warning(f"_fetch_season_window failed: {e}")
        return {}


class Status:
    """
    Season information manager for NHL seasons.

    Manages season metadata including start/end dates, season IDs,
    and provides season-related utility methods.

    Note: Game state checking methods have been migrated to the Game model.
    See nhl_api.models.Game for is_live, is_final, is_scheduled, is_irregular properties.
    """

    def __init__(self):
        self.season_id = 20252026
        self.season_info = {}
        self.next_season_info = {}
        self.refresh_next_season()

    def is_offseason(self, date):
        """Return True only if today is entirely outside the season window
        (before regular-season start or after the season's final game).
        Playoffs are still 'in season' by this definition — use is_playoff
        to distinguish playoff-window days."""
        try:
            regular_season_startdate = _parse_api_date(self.season_info.get("regularSeasonStartDate"))
            end_of_season = _parse_api_date(self.season_info.get("seasonEndDate"))
            if not regular_season_startdate or not end_of_season:
                return False
            return date < regular_season_startdate or date > end_of_season
        except Exception:
            debug.error("status.is_offseason: bad season_info, returning False")
            return False

    def is_playoff(self, date, playoff_obj):
        """Return True if today is in the playoff window AND playoffs exist."""
        try:
            regular_season_enddate = _parse_api_date(self.season_info.get("regularSeasonEndDate"))
            end_of_season = _parse_api_date(self.season_info.get("seasonEndDate"))
            if not regular_season_enddate or not end_of_season:
                return False
            in_window = regular_season_enddate < date <= end_of_season
            has_rounds = bool(getattr(playoff_obj, "rounds", None))
            return bool(in_window and has_rounds)
        except Exception:
            debug.error("status.is_playoff: bad season_info, returning False")
            return False

    def refresh_next_season(self):
        """Fetch and update season information from NHL API.

        Historically this stored ``current_season_info()[-1]`` directly into
        ``season_info``, but that endpoint returns a list of season IDs
        (ints), not dicts. We now keep the id in ``season_id`` and hit the
        stats season endpoint to populate ``season_info`` with the dates
        the rest of the app expects.
        """
        debug.info("Updating next season info")
        try:
            seasons = current_season_info() or []
            if seasons:
                self.season_id = int(seasons[-1])
        except Exception as e:
            debug.warning(f"refresh_next_season: failed to read season id: {e}")

        self.season_info = _fetch_season_window(self.season_id) or {}

        try:
            self.next_season_info = next_season_info() or {}
        except Exception:
            self.next_season_info = {}

        # If the next-season info dict is missing or empty, fall back to a
        # synthesized start date so season_countdown still has something to
        # display.
        if not isinstance(self.next_season_info, dict) or not self.next_season_info.get("regularSeasonStartDate"):
            if not isinstance(self.next_season_info, dict):
                self.next_season_info = {}
            self.next_season_info["regularSeasonStartDate"] = f"{date.today().year}-10-01"
            debug.info("Next season info unavailable, defaulting to Oct 1 of current year as start of new season")

    def next_season_start(self):
        """Get the start date of the next season"""
        return self.next_season_info.get("regularSeasonStartDate", f"{date.today().year}-10-01")
