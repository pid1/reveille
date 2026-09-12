"""location config, read from env vars at import time.

source code stays generic. all location-specific values come from env vars,
set on both deploy targets -- cloudflare build variables for the primary
build and github secrets for the fallback. importing this module hard-fails
if any required var is missing.

every value here is set twice, by hand, in two different consoles, so a
required var that nothing reads is a standing sync cost for nothing. only
require what a fetcher or the renderer actually consumes.
"""

import os
from zoneinfo import ZoneInfo


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"required env var {name} is not set")
    return val


LAT = float(_require("HV_LAT"))
LON = float(_require("HV_LON"))
CITY = _require("HV_CITY")
TIMEZONE = ZoneInfo(_require("HV_TIMEZONE"))
GHOSTMAPS_RADIUS_MILES = float(os.environ.get("HV_GHOSTMAPS_RADIUS_MILES", "25"))
