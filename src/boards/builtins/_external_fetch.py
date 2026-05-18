"""Small HTTP helper shared by the off-season / phase boards.

These boards (draft_tracker, awards, free_agency, team_news) hit external
services that NHL doesn't proxy on api-web.nhle.com:
  - records.nhl.com (older trophies API, browser UA enforced)
  - spotrac.com (HTML scrape, browser UA enforced)
  - forge-dapi.d3.nhle.com (NHL Forge content API, browser UA enforced)
  - api-web.nhle.com (draft endpoints, browser UA enforced)

All callers must tolerate failure — return None and let the board render an
empty state. Crashing here would kill the off-day rotation. Kept dependency-
free (requests is already in requirements; no external pip adds).
"""

import json
import logging
import time
from typing import Optional

import requests

debug = logging.getLogger("scoreboard")

# api-web.nhle.com and spotrac return 403 for default Python UA. Use a
# stable browser string. Note: NHL endpoints have been observed to be
# loose about UA but other CDNs are not — keep this baseline.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}


def fetch_json(url: str, timeout: float = 8.0, attempts: int = 2) -> Optional[dict]:
    """Fetch a JSON URL with retries. Returns None on any failure.

    Follows redirects (NHL `/now` endpoints return 307). Logs but never
    raises. Use this for the structured-API endpoints.
    """
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                try:
                    return r.json()
                except (ValueError, json.JSONDecodeError) as e:
                    last_err = f"invalid json: {e}"
                    debug.warning(f"fetch_json: {url}: {last_err}")
                    return None  # No point retrying a malformed body
            else:
                last_err = f"HTTP {r.status_code}"
                debug.debug(f"fetch_json attempt {attempt}/{attempts}: {url}: {last_err}")
        except (requests.RequestException, OSError) as e:
            last_err = str(e)
            debug.debug(f"fetch_json attempt {attempt}/{attempts}: {url}: {last_err}")
        if attempt < attempts:
            time.sleep(0.5)
    debug.warning(f"fetch_json gave up on {url}: {last_err}")
    return None


def fetch_text(url: str, timeout: float = 12.0, attempts: int = 2) -> Optional[str]:
    """Fetch a URL as text (HTML). Returns None on failure. Same shape as fetch_json."""
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            last_err = f"HTTP {r.status_code}"
            debug.debug(f"fetch_text attempt {attempt}/{attempts}: {url}: {last_err}")
        except (requests.RequestException, OSError) as e:
            last_err = str(e)
            debug.debug(f"fetch_text attempt {attempt}/{attempts}: {url}: {last_err}")
        if attempt < attempts:
            time.sleep(0.5)
    debug.warning(f"fetch_text gave up on {url}: {last_err}")
    return None
