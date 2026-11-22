"""spc convective outlook categories for the configured point, days 1-3.

one keyless point lookup per day against iowa state's iem service, which
does the point-in-polygon test against spc's own outlook polygons. an empty
outlook means the point is outside every risk area, i.e. no risk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from config import LAT, LON

from fetchers.base import get_json

URL = "https://mesonet.agron.iastate.edu/json/spcoutlook.py"

# spc's categorical levels, in order. TSTM is general thunder, not a risk.
LEVELS = {"TSTM": 0, "MRGL": 1, "SLGT": 2, "ENH": 3, "MDT": 4, "HIGH": 5}


def fetch() -> dict:
    now = datetime.now(UTC)
    out = {}
    for day in (1, 2, 3):
        t = (now + timedelta(days=day - 1)).strftime("%Y-%m-%dT%H:%MZ")
        doc = get_json(f"{URL}?lat={LAT:.3f}&lon={LON:.3f}&day={day}&cat=categorical&time={t}")
        if not isinstance(doc, dict) or "outlook" not in doc:
            raise RuntimeError("unexpected spc outlook response")
        level = (doc.get("outlook") or {}).get("threshold") or ""
        out[str(day)] = level if level in LEVELS else ""
    return out


def label(level: str) -> str:
    """'SLGT (2/5)' for a risk, '-' for none or general thunder."""
    n = LEVELS.get(level, 0)
    return f"{level} ({n}/5)" if n else "-"


def any_risk(data: dict) -> bool:
    return any(LEVELS.get(v, 0) for v in (data or {}).values())
