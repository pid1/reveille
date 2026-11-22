"""highland village civicengage rss feeds: emergency, police, fire.

three feeds, same shape. keep entries from the last 14 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import struct_time

import feedparser
from config import TIMEZONE

from fetchers.base import get_json, strip_html, truncate

FEEDS = {
    "emergency": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=63&CID=Emergency-Alerts-1",
    "police": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Police-Department-6",
    "fire": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Fire-Department-11",
}

MAX_AGE_DAYS = 14

# the city's website does not answer cloudflare's network, where this build
# runs, so the nas fetches the feeds from the house and posts them to
# reveille-full every 10 minutes (listener/src/nwws_listener/relay.py). this
# reads them back. a relay older than RELAY_STALE_HOURS fails every feed, and
# a feed the nas could not fetch fails on its own, so both show up in the
# page's failed line.
RELAY_URL = "https://full.reveille.pid1.space/city.json"
RELAY_STALE_HOURS = 3


def _to_central(parsed: struct_time | None) -> datetime | None:
    if not parsed:
        return None
    # feedparser returns parsed times in utc as time.struct_time
    try:
        dt_utc = datetime(*parsed[:6], tzinfo=UTC)
    except TypeError, ValueError:
        return None
    return dt_utc.astimezone(TIMEZONE)


def _relay() -> dict:
    doc = get_json(RELAY_URL, timeout=20.0)
    at = doc.get("at") if isinstance(doc, dict) else None
    if not at:
        raise RuntimeError("nas relay: nothing received")
    try:
        when = datetime.fromisoformat(at)
    except ValueError as e:
        raise RuntimeError("nas relay: bad timestamp") from e
    if datetime.now(UTC) - when > timedelta(hours=RELAY_STALE_HOURS):
        raise RuntimeError(f"nas relay stale since {at}")
    return doc.get("feeds") or {}


def _entries_from(feed: dict | None, name: str) -> list[dict]:
    if not feed:
        raise RuntimeError(f"nas relay: {name} feed missing")
    if feed.get("error"):
        raise RuntimeError(f"nas relay: {feed['error']}")
    url = FEEDS[name]
    fp = feedparser.parse((feed.get("xml") or "").encode("utf-8"))
    # feedparser reads an html error page or a captive portal served as 200
    # as an empty feed, which would hide the section as a quiet day. a real
    # feed, even an empty one, always has a detected version.
    if not fp.version:
        raise RuntimeError(f"not an rss or atom feed: {url}")
    # cutoff at midnight `MAX_AGE_DAYS` ago, not now - 14d (which half-clips
    # by current-time-of-day)
    today_midnight = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cutoff = today_midnight - timedelta(days=MAX_AGE_DAYS)
    out: list[dict] = []
    for e in fp.entries or []:
        published = _to_central(getattr(e, "published_parsed", None)) or _to_central(
            getattr(e, "updated_parsed", None)
        )
        if published and published < cutoff:
            continue
        out.append(
            {
                "title": (getattr(e, "title", "") or "").strip(),
                "link": getattr(e, "link", "") or "",
                "published": published.isoformat(timespec="minutes")
                if published
                else "",
                "summary": truncate(strip_html(getattr(e, "summary", "") or ""), 300),
            }
        )
    # newest first
    out.sort(key=lambda x: x["published"], reverse=True)
    return out


def fetch() -> dict:
    """one failing feed marks only that feed unavailable, under "errors".
    the section as a whole is unavailable only when all three fail.
    """
    out: dict = {}
    errors: dict[str, str] = {}
    feeds = _relay()  # a dead or stale relay fails all three, which raises below
    for name in FEEDS:
        try:
            out[name] = _entries_from(feeds.get(name), name)
        except Exception as e:
            out[name] = []
            errors[name] = f"{type(e).__name__}: {e}"
    if len(errors) == len(FEEDS):
        raise RuntimeError(f"all hv feeds failed; {errors['emergency']}")
    if errors:
        out["errors"] = errors
    return out
