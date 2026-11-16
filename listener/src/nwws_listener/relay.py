"""fetch highland village's rss feeds from the house and pass them to
reveille-full, which serves them to both pages.

the city's website does not answer cloudflare's network (requests from the
worker time out, and from workers builds they fail outright), but it answers
a home connection in half a second. so the nas fetches the feeds every 10
minutes and posts them to the worker's /city endpoint with the same token
the listener's seen reports use. a feed that fails here is sent as an error,
and a relay that stops is noticed by the pages from its timestamp, so either
failure shows in their failed lines.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from .io import _http

log = logging.getLogger("nwws")

FEEDS = {
    "emergency": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=63&CID=Emergency-Alerts-1",
    "police": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Police-Department-6",
    "fire": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=1&CID=Fire-Department-11",
    "all": "https://www.highlandvillage.org/RSSFeed.aspx?ModID=63&CID=All-0",
}
INTERVAL = 600
MAX_FEED_BYTES = 200_000


def collect() -> dict:
    """one pass over the feeds. never raises; a failed feed is an error entry."""
    feeds = {}
    for name, url in FEEDS.items():
        try:
            body = _http(url, timeout=20)[:MAX_FEED_BYTES].decode("utf-8", errors="replace")
            if "<rss" not in body[:500]:
                raise RuntimeError("not an rss feed")
            feeds[name] = {"xml": body}
        except Exception as e:
            feeds[name] = {"error": f"{type(e).__name__}: {e}"[:160]}
    return {"at": datetime.now(UTC).isoformat(timespec="seconds"), "feeds": feeds}


def send(url: str, token: str, doc: dict) -> None:
    _http(url, json.dumps(doc).encode(), {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})


async def run(url: str | None, token: str | None) -> None:
    if not url or not token:
        log.info("city relay off: REVEILLE_CITY_URL or REVEILLE_SEEN_TOKEN not set")
        return
    while True:
        try:
            doc = await asyncio.to_thread(collect)
            await asyncio.to_thread(send, url, token, doc)
            bad = [n for n, f in doc["feeds"].items() if "error" in f]
            if bad:
                log.warning("city relay sent; feeds failed: %s", ", ".join(bad))
        except Exception as e:
            log.warning("city relay failed: %s: %s", type(e).__name__, e)
        await asyncio.sleep(INTERVAL)
