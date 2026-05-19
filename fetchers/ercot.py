"""ercot grid status.

option A (preferred): gridstatus.io rest api (requires GRIDSTATUS_API_KEY).
option C (fallback): scrape ercot's real-time system conditions html page.

option A gives a real conservation-alert level; option C only gives raw
load / generation numbers from which stress must be inferred. the
fetcher tries A first iff a key is configured, else goes straight to C.

NOTE: highest-fragility source. ercot can change either endpoint at any
time. unavailable envelope is the expected behavior when that happens.
"""

from __future__ import annotations

import os
from html.parser import HTMLParser

from fetchers.base import get_json, get_text

ERCOT_RT_URL = "https://www.ercot.com/content/cdr/html/real_time_system_conditions.html"
GRIDSTATUS_BASE = "https://api.gridstatus.io/v1"


def _fetch_gridstatus() -> dict:
    """option A: query gridstatus.io for the current ercot conservation alert level
    and load. errors here bubble up to the caller; safe() / fallback handles them.
    """
    key = os.environ.get("GRIDSTATUS_API_KEY")
    if not key:
        raise RuntimeError("GRIDSTATUS_API_KEY not set")
    headers = {"x-api-key": key, "accept": "application/json"}

    # the conservation/alert dataset name has shifted over time; we make a best
    # effort. failure here surfaces as `unavailable` and we never block on it.
    # ask for the most recent row from ercot_eea (energy emergency alert) status.
    out: dict = {}
    try:
        eea = get_json(
            f"{GRIDSTATUS_BASE}/datasets/ercot_energy_emergency_alert/query"
            "?limit=1&order=desc",
            headers=headers,
        )
        rows = eea.get("data") if isinstance(eea, dict) else None
        if rows:
            out["alert_level"] = str(rows[0].get("status") or rows[0].get("alert_level") or "").strip()
    except Exception as e:
        out["alert_level_err"] = f"{type(e).__name__}: {e}"

    # current actual load
    try:
        load = get_json(
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
