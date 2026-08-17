"""Preferred-team resolution shared by the off-season boards.

Several boards (draft_class, season_opener, season_recap, team_leaders,
team_news, draft_tracker) all need the same thing: "which team does this user
care about, and what's its id / abbreviation?" Each had its own slightly
different copy of that logic, which drifted.

Everything here is defensive and returns None rather than raising — these
boards run inside the rotation, and a missing team must render an empty state,
never take the render loop down.
"""

import logging

debug = logging.getLogger("scoreboard")


def preferred_team_id(data):
    """NHL team id of the first preferred team, or None.

    Trusts data.pref_teams (resolved by Data.get_pref_teams_id) rather than
    re-implementing name->id matching. Note this is legitimately empty while
    running on the offline team table during an NHL API outage, because that
    table's names don't match the API's — see Data.retry_nhl_api_if_down.
    """
    try:
        pref = data.pref_teams or []
    except Exception:
        return None
    return pref[0] if pref else None


def preferred_team_abbrev(data):
    """Three-letter code of the first preferred team (e.g. 'UTA'), or None."""
    team_id = preferred_team_id(data)
    if team_id is None:
        return None
    try:
        teams_info = getattr(data, "teams_info", {}) or {}
        team = teams_info.get(team_id) or teams_info.get(str(team_id))
        abbrev = getattr(getattr(team, "details", None), "abbrev", None)
        return abbrev.upper() if abbrev else None
    except Exception as e:
        debug.debug(f"preferred_team_abbrev failed: {e}")
        return None


def preferred_team_abbrevs(data):
    """Set of abbreviations for ALL preferred teams — for highlighting."""
    out = set()
    try:
        ids = data.pref_teams or []
        teams_info = getattr(data, "teams_info", {}) or {}
    except Exception:
        return out
    for tid in ids:
        team = teams_info.get(tid) or teams_info.get(str(tid))
        abbrev = getattr(getattr(team, "details", None), "abbrev", None)
        if abbrev:
            out.add(abbrev.upper())
    return out


def current_season_id(data, default=None):
    """Season id as an int like 20262027, from data.status. None if unknown.

    NOTE: this is the CURRENT-OR-UPCOMING season, not the one that just ended.
    Status.refresh_next_season() sets it from the last entry of /v1/season,
    which rolls over to the new season well before that season starts — in
    August 2026 it already reads 20262027. Boards looking backwards (recap,
    last season's leaders) want previous_season_id() instead.
    """
    try:
        season = getattr(getattr(data, "status", None), "season_id", None)
        return int(season) if season else default
    except Exception:
        return default


def previous_season_id(data):
    """The season *before* data.status.season_id, e.g. 20262027 -> 20252026.

    This is the most recently COMPLETED season for anything that reports on
    results (final standings, scoring leaders).
    """
    season = current_season_id(data)
    if not season:
        return None
    try:
        start = season // 10000
        return int(f"{start - 1}{start}")
    except Exception:
        return None
