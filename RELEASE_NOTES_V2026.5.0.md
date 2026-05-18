# V2026.5.0 — Phase-aware rotation, six new boards, and the dashboard rewrite

First fork release after upstream's terminal V2026.3.0. This is a substantial step up — the scoreboard now adapts its rotation to where you are in the NHL calendar, ships six new boards, and the web dashboard has been overhauled around a dynamic registry instead of hardcoded lists.

## ✨ New features

### Season-phase rotation states

The scoreboard now auto-detects four season phases from the NHL schedule and playoff data:

| Phase | When it activates |
|---|---|
| 🏒 Regular Season | Existing `off_day` continues to apply |
| 🏆 Playoffs — Team In (`post_season_active`) | Your preferred team is alive in the bracket |
| 🥲 Playoffs — Team Out (`post_season_eliminated`) | Playoffs running but your team is out |
| ☀️ Off Season (`off_season`) | Between the Cup and the next regular-season opener |

Detection is fully automatic — nothing to configure. New state columns appear in the Board Rotation tab; drag any boards into them. Empty phase lists fall back to `off_day` so existing configs aren't disrupted.

### Six new builtin boards

- **Draft Tracker (`draft_tracker`)** — Live NHL Entry Draft picks via the official `api-web.nhle.com` API, with optional highlighting for your preferred teams.
- **Awards (`awards`)** — Stanley Cup / Hart / Norris / Vezina / etc. trophies via `records.nhl.com`, with most-recent winner parsed from the description.
- **Free Agency (`free_agency`)** — Recent signings + top remaining unsigned players via Spotrac. Auto-falls-back to the available list when no signings exist yet.
- **Team News (`team_news`)** — Recent NHL.com headlines for the first team in your Preferences. Uses NHL's Forge content API — the official replacement for the retired RSS feeds.
- **Holiday (`holiday`)** — Themed icons + greetings on 24 US/CA holidays (US/Canada selectable). Skips by default on non-holiday days. Icons drawn programmatically — no asset downloads.
- **Event Countdown (`event_countdown`)** — Count down to any user-configured event (birthday, vacation, etc.) with a title, date, optional time, and one of 12 curated icons. Auto-skips after the event passes.

### Open-Meteo weather provider

OpenWeatherMap moved their One Call endpoint to a paid v3 subscription tier, breaking free OWM keys. Added `openmeteo` as a third weather data source option (alongside EC and OWM) — free, no API key, no signup, global coverage. Existing OWM and EC options stay so nothing breaks.

### Web dashboard rewrite

- **Dynamic registry** — Available-boards picker now fetches the live Python registry via new `/api/scoreboard/available-boards` and `/api/scoreboard/states` endpoints. Adding a new plugin or builtin no longer requires editing the dashboard's JavaScript.
- **Hover popovers** — Rich popovers on each available-board chip showing display name, board id, and a description sourced from the board's `plugin.json`. Replaces the old browser-native tooltip.
- **Collapsible state columns** — Click any state header to fold it. Collapsed set persists in `localStorage`.
- **Drag-to-reorder** — Reorder boards within a state column (the previous version was append-only). Insertion indicator shows where the drop lands.
- **Season Phase stat box** — New live indicator on the Status tab showing the currently-detected phase.
- **Interrupt boards hidden from chip pool** — `screensaver`, `wxalert`, `pbdisplay` are triggered automatically by interrupts. Dragging them into the rotation would lock the matrix; they're now hidden from the draggable pool but still configurable in their accordion settings.
- **Christmas + Event Countdown** boards have new settings panels.

## 🐛 Bug fixes

- **Seriesticker tz crash on TBD playoff games** — `Game.from_api` now normalises `game_date` to tz-aware UTC at the parsing boundary so comparisons against `datetime.now(timezone.utc)` no longer raise `TypeError`. Was crashing the renderer whenever the seriesticker reached an Eastern/Western Conference Final with a TBD home team.
- **Seriesticker mislabels Eastern Conference Finals as WEST** — When one side is TBD, `topSeedTeam.conference` is null. The Series constructor only checked the top seed and fell through to a bare `except` returning `""`, then the seriesticker defaulted to `"Western"`. Now checks both top and bottom seeds.
- **Status.season_info was an int, not a dict** — `current_season_info()` returns a list of season-id integers, but `Status.refresh_next_season()` stored `[-1]` (an int) into `season_info`. Every dict-access in `is_offseason` / `is_playoff` raised `TypeError` and the bare except returned `False`, breaking phase detection (it always returned `REGULAR_SEASON`). Now hits `api.nhle.com/stats/rest/en/season` directly.
- **Weather forecast init crashed the whole service** — `wxForecast.__init__` ran `getForecast()` synchronously and an unhandled `TypeError` (from OWM's 401 response shape) brought down the scheduler. Now defensively parses, logs an actionable message on 401, and every weather worker init in `scheduler.py` is individually try/except-guarded.
- **Free agency board showed "no data" outside July** — Defaults to `mode: signings` but Spotrac only renders the signed table during the active signing window. Auto-falls-back to the available list now.
- **Glyph boxes on team_news / awards / draft / free_agency** — Pixel font lacks curly quotes, em-dashes, ellipsis, accented characters (Éric, Jørgen, Łukáš, etc.). New `sanitize()` helper maps to ASCII equivalents before drawing.
- **Long news headlines didn't scroll** — Added a horizontal-marquee helper. Team news and trophy winner text now scroll when wider than the matrix.
- **Collapsed state card left tall empty grid cell** — Fixed with `align-items: start` on the CSS grid.
- **Chip × button broke after reorder** — Was using array indices baked into inline `onclick`; now uses `addEventListener` with closure over the chip ref.

## 📋 Upgrading

Existing installs:

```bash
ssh -i ~/.ssh/pi_key pi@<pi-ip> "cd /home/pi/nhl-led-scoreboard && git pull origin main && sudo find /home/pi/nhl-led-scoreboard/src -name '*.pyc' -delete && sudo supervisorctl restart scoreboard logo-editor"
```

If you were previously on OpenWeatherMap and want weather data back, open the dashboard → Boards → Weather → Data Source → **Open-Meteo (free, global, no key)**.

No config changes are required for the new phase states — they default to empty and the renderer falls back to your existing `off_day` rotation. Populate them in the dashboard's Board Rotation tab when you're ready.

Fresh installs: `sb-init` and `sb-upgrade` pick up the new `requests` and `beautifulsoup4` Python dependencies automatically (added to `requirements.txt`, `requirements-pi5.txt`, and `requirements-docker.txt`).
