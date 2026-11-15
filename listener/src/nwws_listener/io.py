"""side effects: state file, pushover, healthchecks ping, the worker report,
and resolving the home county and zone from api.weather.gov.

every function here logs and returns on failure. a dead pushover or a
missing network must never take the listener down.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path

from .decide import EMERGENCY, EventState, Push

log = logging.getLogger("nwws")
UA = "reveille-listener (github.com/pid1/reveille)"


def _http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 15) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("only https urls are fetched")
    h = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h)
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=timeout) as r:  # https-only, checked above
        return r.read()


# -- state ------------------------------------------------------------------


def load_state(path: Path) -> dict[str, EventState]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("state unreadable, starting empty: %s", e)
        return {}
    out = {}
    for k, v in raw.get("events", {}).items():
        try:
            v["tags"] = set(v.get("tags", []))
            out[k] = EventState(**v)
        except TypeError:
            continue
    return out


def save_state(path: Path, state: dict[str, EventState], extra: dict | None = None) -> None:
    data = {"events": {k: {**asdict(v), "tags": sorted(v.tags)} for k, v in state.items()}}
    if extra:
        data.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# -- outputs ----------------------------------------------------------------


def send_push(p: Push, token: str, user: str, dry_run: bool) -> bool:
    form = {
        "token": token,
        "user": user,
        "title": p.title[:250],
        "message": p.message[:1024],
        "priority": str(p.priority),
    }
    if p.url:
        form["url"] = p.url
        form["url_title"] = "full text"
    if p.priority == EMERGENCY:
        form["retry"] = "60"
        form["expire"] = "1800"
    if dry_run or not token or not user:
        log.info("push (dry run) p=%s %s | %s", p.priority, p.title, p.message)
        return True
    try:
        body = _http("https://api.pushover.net/1/messages.json", urllib.parse.urlencode(form).encode())
        ok = json.loads(body).get("status") == 1
        log.info("push %s p=%s %s", "sent" if ok else "rejected", p.priority, p.title)
        return ok
    except Exception as e:
        log.error("push failed: %s: %s", type(e).__name__, e)
        return False


def ping(url: str | None) -> None:
    if not url:
        return
    try:
        _http(url, timeout=10)
    except Exception as e:
        log.warning("healthcheck ping failed: %s", e)


def report_seen(url: str | None, token: str | None, keys: list[str]) -> None:
    """tell reveille-full which events the nas already pushed, so its
    10-minute poll does not push them again."""
    if not url or not token or not keys:
        return
    try:
        _http(
            url,
            json.dumps({"keys": keys}).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
    except Exception as e:
        log.warning("seen report failed: %s", e)


def resolve_ugc(lat: float, lon: float) -> set[str]:
    """county and forecast zone codes for the point, e.g. {TXC121, TXZ103}."""
    body = json.loads(_http(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"))
    props = body["properties"]
    codes = set()
    for k in ("county", "forecastZone", "fireWeatherZone"):
        url = props.get(k) or ""
        if url:
            codes.add(url.rstrip("/").rsplit("/", 1)[-1])
    return codes
