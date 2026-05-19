"""ercot grid status.

option A (preferred): gridstatus.io rest api (requires GRIDSTATUS_API_KEY).
option C (fallback): scrape ercot's real-time system conditions html page.

option A gives a real conservation-alert level (Energy Emergency Alert
stage 0/1/2/3); option C only gives raw load / generation numbers from
which stress must be inferred. the fetcher tries A first iff a key is
configured, else goes straight to C.

NOTE: highest-fragility source. ercot can change either endpoint at any
time. unavailable envelope is the expected behavior when that happens.

gridstatus.io dataset notes:
  - ercot_current_conditions: replaces the old ercot_energy_emergency_alert
    dataset (RTC+B rollout on 2025-12-05). columns: time_utc, eea_level
    (INTEGER 0..3), energy_level_value, state, title, condition_note.
  - ercot_load: columns interval_start_utc, interval_end_utc, load (MW).
  - free-tier rate limit is 1 request per second; back-to-back queries
    must be spaced or one will 429. we sleep between them and do a
    single one-shot retry on 429.
"""

from __future__ import annotations

import os
import time
from html.parser import HTMLParser

from fetchers.base import get_json, get_text

ERCOT_RT_URL = "https://www.ercot.com/content/cdr/html/real_time_system_conditions.html"
GRIDSTATUS_BASE = "https://api.gridstatus.io/v1"

# free-tier limit is 1 req/sec. add headroom.
_GRIDSTATUS_MIN_SPACING_S = 1.15


def _get_json_with_429_retry(url: str, headers: dict) -> dict:
    """get_json wrapper that retries once on HTTP 429 after a short sleep."""
    try:
        return get_json(url, headers=headers)
    except RuntimeError as e:
        # fetchers.base raises RuntimeError("HTTP 429 from ...") for 429s.
        if "HTTP 429" not in str(e):
            raise
        time.sleep(_GRIDSTATUS_MIN_SPACING_S)
        return get_json(url, headers=headers)


def _fetch_gridstatus() -> dict:
    """option A: query gridstatus.io for the current ercot EEA level and load.
    errors here bubble up to the caller; safe() / fallback handles them.
    """
    key = os.environ.get("GRIDSTATUS_API_KEY")
    if not key:
        raise RuntimeError("GRIDSTATUS_API_KEY not set")
    headers = {"x-api-key": key, "accept": "application/json"}

    out: dict = {}

    # current grid condition (includes EEA level when one is active)
    try:
        cc = _get_json_with_429_retry(
            f"{GRIDSTATUS_BASE}/datasets/ercot_current_conditions/query"
            "?limit=1&order=desc",
            headers=headers,
        )
        rows = cc.get("data") if isinstance(cc, dict) else None
        if rows:
            row = rows[0]
            # prefer the human-readable title (e.g. "Normal",
            # "Energy Emergency Alert Level 2") for the existing
            # alert_level string field consumed by render/summarize.
            title = (row.get("title") or "").strip()
            note = (row.get("condition_note") or "").strip()
            eea = row.get("eea_level")
            if title:
                out["alert_level"] = title
            elif eea is not None:
                out["alert_level"] = (
                    f"EEA Level {int(eea)}" if int(eea) > 0 else "Normal"
                )
            if eea is not None:
                try:
                    out["eea_level"] = int(eea)
                except (TypeError, ValueError):
                    pass
            if note and note.lower() != "normal":
                out["condition_note"] = note
    except Exception as e:
        out["alert_level_err"] = f"{type(e).__name__}: {e}"

    # space the next call to stay under the 1 req/sec free-tier cap
    time.sleep(_GRIDSTATUS_MIN_SPACING_S)

    # current actual load
    try:
        load = _get_json_with_429_retry(
            f"{GRIDSTATUS_BASE}/datasets/ercot_load/query?limit=1&order=desc",
            headers=headers,
        )
        rows = load.get("data") if isinstance(load, dict) else None
        if rows:
            mw = rows[0].get("load") or rows[0].get("load_mw") or rows[0].get("value")
            if mw is not None:
                out["load_mw"] = int(float(mw))
    except Exception as e:
        out["load_err"] = f"{type(e).__name__}: {e}"

    if not out or (set(out.keys()) <= {"alert_level_err", "load_err"}):
        raise RuntimeError(
            "gridstatus.io returned no usable fields "
            f"(alert_err={out.get('alert_level_err','-')}, "
            f"load_err={out.get('load_err','-')})"
        )

    out["source"] = "gridstatus.io"
    return out


class _SysCondParser(HTMLParser):
    """parse ercot real-time system conditions html into a {label: value} dict.

    structure is alternating <td class='tdLeft'>label</td><td class='labelClassCenter'>value</td>
    pairs, plus 'headerValueClass' section headers we ignore.
    """

    def __init__(self):
        super().__init__()
        self.pairs: dict[str, str] = {}
        self._mode: str | None = None  # 'label' | 'value' | None
        self._pending_label: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "td":
            return
        cls = dict(attrs).get("class", "")
        if "tdLeft" in cls:
            self._mode = "label"
            self._buf = []
        elif "labelClassCenter" in cls:
            self._mode = "value"
            self._buf = []
        else:
            self._mode = None

    def handle_endtag(self, tag):
        if tag != "td" or self._mode is None:
            self._mode = None
            return
        text = " ".join("".join(self._buf).split())
        if self._mode == "label":
            self._pending_label = text
        elif self._mode == "value" and self._pending_label:
            self.pairs[self._pending_label] = text
            self._pending_label = None
        self._mode = None
        self._buf = []

    def handle_data(self, data):
        if self._mode in {"label", "value"}:
            self._buf.append(data)


def _to_int(s: str) -> int | None:
    if not s:
        return None
    try:
        return int(float(s.replace(",", "").strip()))
    except ValueError:
        return None


def _to_float(s: str) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _fetch_ercot_html() -> dict:
    html = get_text(ERCOT_RT_URL)
    p = _SysCondParser()
    p.feed(html)
    pairs = p.pairs
    if not pairs:
        raise RuntimeError("could not parse ercot real-time system conditions")

    demand = _to_int(pairs.get("Actual System Demand", ""))
    capacity = _to_int(pairs.get("Total System Capacity (not including Ancillary Services)", ""))
    wind = _to_int(pairs.get("Total Wind Output", ""))
    pvgr = _to_int(pairs.get("Total PVGR Output", ""))
    freq = _to_float(pairs.get("Current Frequency", ""))

    margin = None
    if demand is not None and capacity is not None:
        margin = capacity - demand

    renewable_share = None
    if demand and (wind is not None or pvgr is not None):
        renewable_mw = (wind or 0) + (pvgr or 0)
        renewable_share = round(100.0 * renewable_mw / demand, 1)

    return {
        "source": "ercot.com/cdr",
        "frequency_hz": freq,
        "load_mw": demand,
        "capacity_mw": capacity,
        "reserve_margin_mw": margin,
        "wind_mw": wind,
        "pvgr_mw": pvgr,
        "renewable_share_pct": renewable_share,
        "alert_level": None,  # not derivable from this page
    }


def fetch() -> dict:
    """try gridstatus.io if configured, fall back to ercot html."""
    if os.environ.get("GRIDSTATUS_API_KEY"):
        try:
            return _fetch_gridstatus()
        except Exception as e:
            # fall through to ercot html; record the gridstatus failure
            html_data = _fetch_ercot_html()
            html_data["gridstatus_error"] = f"{type(e).__name__}: {e}"
            return html_data
    return _fetch_ercot_html()
