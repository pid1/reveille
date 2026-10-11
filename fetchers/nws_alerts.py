"""nws active alerts for the configured point."""

import re

from config import LAT, LON

from fetchers.base import get_json, truncate


# ozone action days (tceq air quality alerts) are routine summer noise
# jonathan does not act on, so they are dropped before render and summary.
# an air quality alert that also names particulates is kept: smoke and dust
# are a different, rarer problem.
_OZONE = re.compile(r"\bozone\b", re.I)
_PARTICULATE = re.compile(r"particle|particulate|pm ?2\.5|pm ?10|smoke|dust", re.I)


def _is_ozone_only(p: dict) -> bool:
    text = " ".join(p.get(k) or "" for k in ("event", "headline", "description"))
    return bool(_OZONE.search(text)) and not _PARTICULATE.search(text)


def fetch() -> list[dict]:
    url = f"https://api.weather.gov/alerts/active?point={LAT},{LON}"
    data = get_json(url, headers={"accept": "application/geo+json"})
    alerts = []
    for feat in data.get("features", []) or []:
        p = feat.get("properties", {}) or {}
        if _is_ozone_only(p):
            continue
        alerts.append(
            {
                "event": p.get("event") or "Unknown",
                "severity": p.get("severity") or "Unknown",
                "urgency": p.get("urgency") or "Unknown",
                "headline": p.get("headline") or "",
                "description": truncate(p.get("description") or "", 400),
                "expires": p.get("expires") or p.get("ends") or "",
            }
        )
    return alerts
