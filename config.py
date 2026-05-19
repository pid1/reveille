"""location config, read from env vars at import time.

source code stays generic. all location-specific values come from env vars
backed by github secrets. importing this module hard-fails if any required
var is missing.
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
ZIP_CODE = _require("HV_ZIP")
CITY = _require("HV_CITY")
STATE = _require("HV_STATE")
COUNTY = _require("HV_COUNTY")
TIMEZONE = ZoneInfo(_require("HV_TIMEZONE"))
GHOSTMAPS_RADIUS_MILES = float(os.environ.get("HV_GHOSTMAPS_RADIUS_MILES", "25"))
