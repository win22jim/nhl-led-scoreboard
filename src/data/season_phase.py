"""Season-phase detection.

Adds a coarse `SeasonPhase` classification on top of the existing per-game
states (off_day / scheduled / intermission / post_game). The renderer uses
this to pick which board list to display when there's no live game for the
user's preferred team:

    REGULAR_SEASON          -> existing `boards_off_day`
    POST_SEASON_ACTIVE      -> `boards_post_season_active`
    POST_SEASON_ELIMINATED  -> `boards_post_season_eliminated`
    OFF_SEASON              -> `boards_off_season`

Detection is fully automatic from NHL API data already loaded into the
Data object (status.season_info + playoff carousel). All logic is wrapped
in broad try/except — phase detection failures fall back to REGULAR_SEASON
so the scoreboard never crashes because of a phase computation.
"""

import logging
from enum import Enum

debug = logging.getLogger("scoreboard")


class SeasonPhase(str, Enum):
    REGULAR_SEASON = "regular_season"
    POST_SEASON_ACTIVE = "post_season_active"
    POST_SEASON_ELIMINATED = "post_season_eliminated"
    OFF_SEASON = "off_season"


def _team_alive_in_playoffs(data) -> bool:
    """Return True if any of data.pref_teams is still alive in the bracket.

    A team is "alive" if they:
      (a) appear in any series that is not yet final, OR
      (b) appear in a final series that they won (advanced — next round may not
          have started yet, but they're still in the tournament).

    Returns False if no preferred team is set, if no preferred team appears in
    any series, or if every appearance is as the loser of a final series.
    """
    pref_ids = set(data.pref_teams) if getattr(data, "pref_teams", None) else set()
    if not pref_ids:
        return False

    # Combine series_list (current round or all rounds depending on config) and
    # pref_series (filtered to preferred teams) to be safe — duplicates harmless.
    series_objs = []
    for source in (getattr(data, "series_list", None), getattr(data, "pref_series", None)):
        if not source:
            continue
        for s in source:
            if s not in series_objs:
                series_objs.append(s)

    if not series_objs:
        return False

    for s in series_objs:
        try:
            top = s.top_team
            bottom = s.bottom_team
        except AttributeError:
            continue
        team_in_series = (top.id in pref_ids) or (bottom.id in pref_ids)
        if not team_in_series:
            continue
        is_final = getattr(s, "final", False)
        if not is_final:
            return True
        try:
            top_wins = int(top.series_wins)
            bottom_wins = int(bottom.series_wins)
        except (TypeError, ValueError, AttributeError):
            continue
        if top.id in pref_ids and top_wins > bottom_wins:
            return True
        if bottom.id in pref_ids and bottom_wins > top_wins:
            return True
    return False


def detect_phase(data) -> SeasonPhase:
    """Classify the current moment relative to the NHL season.

    All branches are defensive — if NHL API state isn't loaded yet, or any
    accessor throws, we return REGULAR_SEASON as the safe default. The
    renderer treats REGULAR_SEASON as "use boards_off_day", which is what
    the scoreboard did before phases existed.
    """
    try:
        today = data.date()
    except Exception:
        debug.warning("detect_phase: could not read data.date(), defaulting to REGULAR_SEASON")
        return SeasonPhase.REGULAR_SEASON

    status = getattr(data, "status", None)
    playoffs = getattr(data, "playoffs", None)
    if status is None:
        return SeasonPhase.REGULAR_SEASON

    try:
        in_offseason = status.is_offseason(today)
    except Exception as e:
        debug.warning(f"detect_phase: is_offseason raised, defaulting to REGULAR_SEASON: {e}")
        return SeasonPhase.REGULAR_SEASON

    if not in_offseason:
        return SeasonPhase.REGULAR_SEASON

    # We're outside the regular season window. Distinguish post-season vs off-season.
    try:
        in_playoffs = bool(playoffs) and status.is_playoff(today, playoffs)
    except Exception as e:
        debug.warning(f"detect_phase: is_playoff raised, defaulting to OFF_SEASON: {e}")
        return SeasonPhase.OFF_SEASON

    if not in_playoffs:
        return SeasonPhase.OFF_SEASON

    try:
        alive = _team_alive_in_playoffs(data)
    except Exception as e:
        debug.warning(f"detect_phase: alive check raised, defaulting to POST_SEASON_ELIMINATED: {e}")
        return SeasonPhase.POST_SEASON_ELIMINATED

    return SeasonPhase.POST_SEASON_ACTIVE if alive else SeasonPhase.POST_SEASON_ELIMINATED
