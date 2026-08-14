"""
API Status board module.

Displayed in place of boards that need live NHL data whenever the NHL API is
unreachable, so an outage shows an explanatory screen instead of a blank
matrix or a restart loop.
"""

# Board metadata using standard Python package conventions
__version__ = "1.0.0"
__description__ = "Shows an NHL-API-unavailable notice while the NHL API is down"
__board_name__ = "API Status Board"
__author__ = "NHL LED Scoreboard"

# Board requirements (optional)
__requirements__ = []

# Minimum application version required (optional)
__min_app_version__ = "1.0.0"
