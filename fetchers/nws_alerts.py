"""nws active alerts for the configured point."""

from config import LAT, LON

from fetchers.base import get_json, truncate


def fetch() -> list[dict]:
    url = f"https://api.weather.gov/alerts/active?point={LAT},{LON}"
    data = get_json(url, headers={"accept": "application/geo+json"})
    alerts = []
    for feat in data.get("features", []) or []:
        p = feat.get("properties", {}) or {}
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
