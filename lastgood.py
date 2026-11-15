"""the last good copy of each section, so a failed fetch can show it.

builds run in throwaway containers on two platforms (cloudflare workers
builds and the github actions fallback), so nothing on local disk survives
to the next build. what both of them do share is the deployed site. so each
build writes the data it got into dist/last-good.json next to index.html,
it is deployed with the page, and the next build reads it back from
reveille.pid1.space. no extra service, no credentials, and index.html
doesn't grow: readers never download this file.

when a source fails, the section shows its last good copy, labelled with
when it is from, if that copy is younger than the section's cutoff:

  nws_forecast  48h  a 3-day forecast, with the periods already past
                     dropped, is still a valid look-ahead a day later.
  hf_bands      48h  conditions move over hours, but the day/night calls
                     are coarse guidance; a labelled day-old copy beats none.
  hv_rss        72h  feeds cover 14 days and every entry carries its date.
  nws_alerts    12h  warnings get cancelled and new ones issued; a copy is
                     only shown from a same-day rebuild, expired ones dropped.
  ghostmaps     12h  the section is "today and yesterday"; a day-old copy
                     would show incidents outside that window.
  ercot          3h  grid numbers are minute-to-minute; yesterday's load
                     says nothing about this morning.

on the normal once-a-day schedule that means the forecast, band conditions
and city feeds carry over from yesterday, and alerts, ghostmaps and ercot
only carry over within the same day (the github fallback or a rebuild).
older copies are dropped, not shown.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from fetchers.base import get_json, ok

URL = "https://reveille.pid1.space/last-good.json"
FILENAME = "last-good.json"
VERSION = 1

CUTOFF_HOURS = {
    "nws_alerts": 12,
    "nws_forecast": 48,
    "ercot": 3,
    "ghostmaps": 12,
    "hv_rss": 72,
    "hf_bands": 48,
}
_SIMPLE = ("nws_alerts", "nws_forecast", "ercot", "ghostmaps", "hf_bands")
_FEEDS = ("emergency", "police", "fire")


def _url() -> str:
    return os.environ.get("REVEILLE_LAST_GOOD_URL", "").strip() or URL


def load() -> dict:
    """read the previous build's last-good.json. {} on any failure."""
    try:
        doc = get_json(_url(), timeout=15.0)
        if not isinstance(doc, dict) or doc.get("v") != VERSION:
            raise RuntimeError("unrecognised last-good document")
        entries = doc.get("sections")
        if not isinstance(entries, dict):
            raise RuntimeError("last-good document has no sections")
        return {
            k: e
            for k, e in entries.items()
            if isinstance(e, dict) and isinstance(e.get("fetched_at"), str) and "data" in e
        }
    except Exception as e:
        print(f"[lastgood] no previous copy: {type(e).__name__}: {e}", flush=True)
        return {}


def dumps(store: dict) -> str:
    return json.dumps({"v": VERSION, "sections": store}, separators=(",", ":"))


def _age_ok(entry: dict | None, section: str, now: datetime) -> bool:
    if not entry:
        return False
    try:
        t = datetime.fromisoformat(entry["fetched_at"])
    except TypeError, ValueError:
        return False
    return timedelta(0) <= now - t <= timedelta(hours=CUTOFF_HOURS[section])


def _future(iso: str, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(iso) > now
    except TypeError, ValueError:
        return False  # no usable end time: can't show it as still ahead


def _usable(section: str, entry: dict | None, now: datetime):
    """the entry's data, trimmed to what is still valid now, or None."""
    if not _age_ok(entry, section, now):
        return None
    data = entry["data"]
    if section == "nws_forecast":
        data = [p for p in data if _future(p.get("endTime"), now)]
        return data or None
    if section == "nws_alerts":
        # an empty list would render as "no alerts", which a copy can't claim
        data = [a for a in data if _future(a.get("expires"), now)]
        return data or None
    return data


def _stale(data, entry: dict, error: str | None) -> dict:
    env = ok(data)
    env["stale_from"] = entry["fetched_at"]
    env["today_error"] = error
    return env


def apply(sections: dict, previous: dict, now: datetime) -> dict:
    """swap failed sections for usable last-good copies, in place.

    returns the store to publish for the next build: this build's good data,
    plus the carried-over copies it used (keeping their original time, so a
    copy ages out instead of being refreshed by each failing build).
    """
    store: dict = {}
    for key in _SIMPLE:
        env = sections[key]
        if env["status"] == "ok":
            store[key] = {"data": env["data"], "fetched_at": env["fetched_at"]}
            continue
        entry = previous.get(key)
        try:
            data = _usable(key, entry, now)
        except Exception:
            data = None  # a malformed copy is no copy
        if data is not None:
            sections[key] = _stale(data, entry, env["error"])
            store[key] = entry
    _apply_rss(sections, previous, now, store)
    return store


def _apply_rss(sections: dict, previous: dict, now: datetime, store: dict) -> None:
    # hv_rss is three feeds that fail separately, so they are stored separately
    env = sections["hv_rss"]
    if env["status"] == "ok":
        data = dict(env["data"])
        errors = dict(data.get("errors") or {})
    else:
        data = {name: [] for name in _FEEDS}
        errors = {name: env["error"] for name in _FEEDS}
    stale: dict[str, str] = {}
    for name in _FEEDS:
        key = f"hv_rss.{name}"
        if name not in errors:
            store[key] = {"data": data.get(name) or [], "fetched_at": env["fetched_at"]}
            continue
        entry = previous.get(key)
        if _age_ok(entry, "hv_rss", now) and isinstance(entry["data"], list):
            data[name] = entry["data"]
            stale[name] = entry["fetched_at"]
            del errors[name]
            store[key] = entry
    if not stale:
        return
    data["errors"] = errors
    data["stale"] = stale
    new = ok(data)
    if env["status"] != "ok":
        new["today_error"] = env["error"]
    sections["hv_rss"] = new
