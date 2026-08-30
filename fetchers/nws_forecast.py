"""3-day forecast from nws (6 periods, day+night pairs)."""

from config import LAT, LON

from fetchers.base import get_json


def fetch() -> list[dict]:
    point = get_json(
        f"https://api.weather.gov/points/{LAT},{LON}",
        headers={"accept": "application/geo+json"},
    )
    forecast_url = point.get("properties", {}).get("forecast")
    if not forecast_url:
        raise RuntimeError("no forecast url in /points response")
    fc = get_json(forecast_url, headers={"accept": "application/geo+json"})
    periods = fc.get("properties", {}).get("periods", []) or []
    out = []
    for p in periods[:6]:
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        out.append(
            {
                "name": p.get("name") or "",
                "temperature": p.get("temperature"),
                "temperatureUnit": p.get("temperatureUnit") or "F",
                "windSpeed": p.get("windSpeed") or "",
                "windDirection": p.get("windDirection") or "",
                "shortForecast": p.get("shortForecast") or "",
                "precipPct": pop,
            }
        )
    return out
