"""highland village civicengage rss feeds: emergency, police, fire.

three feeds, same shape. keep entries from the last 14 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import struct_time

import feedparser
from config import TIMEZONE

from fetchers.base import strip_html, truncate

FEEDS = {
    "emergency": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=63&CID=Emergency-Alerts-1",
    "police": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Police-Department-6",
    "fire": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Fire-Department-11",
}

MAX_AGE_DAYS = 14


def _to_central(parsed: struct_time | None) -> datetime | None:
    if not parsed:
        return None
    # feedparser returns parsed times in utc as time.struct_time
    try:
        dt_utc = datetime(*parsed[:6], tzinfo=UTC)
    except TypeError, ValueError:
        return None
    return dt_utc.astimezone(TIMEZONE)


def _entries_from(url: str) -> list[dict]:
    fp = feedparser.parse(url)
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
    return {name: _entries_from(url) for name, url in FEEDS.items()}
