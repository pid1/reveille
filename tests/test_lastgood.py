"""last good copies: a failed section shows the previous build's data,
labelled, trimmed to what is still valid, and only while it is recent."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
from datetime import datetime, timedelta

import lastgood
from config import TIMEZONE
from tests.test_degradation import (
    ALERTS,
    BLUF,
    POINTS,
    SECTIONS,
    Build,
    PageAsserts,
    FakeNet,
)

DEAD = urllib.error.URLError(socket.gaierror(8, "no dns"))
LABEL = "[fetch failed; showing copy from "


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _ago(hours: float) -> str:
    return _iso(datetime.now(TIMEZONE) - timedelta(hours=hours))


def _ahead(hours: float) -> str:
    return _iso(datetime.now(TIMEZONE) + timedelta(hours=hours))


def _period(name: str, end: str) -> dict:
    return {
        "name": name,
        "temperature": 60,
        "temperatureUnit": "F",
        "windSpeed": "5 mph",
        "windDirection": "S",
        "shortForecast": f"{name} forecast",
        "precipPct": 10,
        "endTime": end,
    }


def _forecast_entry(age_h: float) -> dict:
    return {
        "fetched_at": _ago(age_h),
        "data": [
            _period("Past Period", _ago(2)),
            _period("Next Period", _ahead(10)),
            _period("Later Period", _ahead(22)),
        ],
    }


def _net(previous: dict, broken: dict) -> FakeNet:
    return FakeNet(broken, overrides={lastgood.URL: lastgood.dumps(previous).encode()})


class LastGoodTest(PageAsserts, unittest.TestCase):
    def section(self, html: str, title: str) -> str:
        """the text of one section, header to the next header."""
        start = html.index(f"<b>{title}</b>")
        end = html.find("<b>", start + 3)
        return html[start : end if end != -1 else None]

    def test_good_build_publishes_its_data(self):
        b = Build(FakeNet())
        self.assertBuilt(b)
        doc = json.loads(b.store_text)
        self.assertEqual(doc["v"], 1)
        self.assertEqual(
            set(doc["sections"]),
            {
                "nws_alerts",
                "nws_forecast",
                "ercot",
                "ghostmaps",
                "hf_bands",
                "hv_rss.emergency",
                "hv_rss.police",
                "hv_rss.fire",
            },
        )
        self.assertEqual(
            doc["sections"]["nws_forecast"]["data"][0]["shortForecast"],
            "Test Forecast Text",
        )
        # the location is private; the copy must not carry it
        self.assertNotIn("33.09", b.store_text)
        self.assertNotIn("97.05", b.store_text)
        self.assertNotIn("gridpoints", b.store_text)

    def test_forecast_failed_with_yesterdays_copy(self):
        entry = _forecast_entry(24)
        b = Build(_net({"nws_forecast": entry}, {POINTS: DEAD}))
        self.assertBuilt(b)
        fc = self.section(b.html, "NWS FORECAST")
        self.assertIn(LABEL, fc)
        self.assertNotIn("Past Period", fc)
        self.assertIn("Next Period", fc)
        self.assertIn("Later Period", fc)
        self.assertNotIn("[unavailable", fc)
        # the rest of the page is this morning's
        for key in SECTIONS:
            if key != "nws_forecast":
                self.assertSectionOk(b, key)
        self.assertNotIn("every source failed", b.html)
        # the model is told it's a copy, and the past period isn't in it
        self.assertIn("NWS_FORECAST: (today's fetch failed; copy from ", b.prompt)
        self.assertNotIn("Past Period", b.prompt)
        # the copy is passed on with its original time, so it ages out
        carried = json.loads(b.store_text)["sections"]["nws_forecast"]
        self.assertEqual(carried["fetched_at"], entry["fetched_at"])

    def test_forecast_failed_without_a_copy(self):
        b = Build(_net({}, {POINTS: DEAD}))
        self.assertBuilt(b)
        self.assertSectionUnavailable(b, "nws_forecast")
        self.assertNotIn(LABEL, b.html)
        self.assertNotIn("nws_forecast", json.loads(b.store_text)["sections"])

    def test_forecast_copy_past_the_cutoff_is_not_shown(self):
        b = Build(_net({"nws_forecast": _forecast_entry(49)}, {POINTS: DEAD}))
        self.assertBuilt(b)
        self.assertSectionUnavailable(b, "nws_forecast")
        self.assertNotIn("Next Period", b.html)
        self.assertNotIn("nws_forecast", json.loads(b.store_text)["sections"])

    def test_forecast_copy_entirely_in_the_past_is_not_shown(self):
        entry = {"fetched_at": _ago(30), "data": [_period("Gone", _ago(1))]}
        b = Build(_net({"nws_forecast": entry}, {POINTS: DEAD}))
        self.assertSectionUnavailable(b, "nws_forecast")

    def test_alerts_copy_same_day_keeps_unexpired_only(self):
        def alert(event, expires):
            return {
                "event": event,
                "severity": "Severe",
                "urgency": "Expected",
                "headline": "h",
                "description": "d",
                "expires": expires,
            }

        entry = {
            "fetched_at": _ago(2),
            "data": [alert("Winter Storm Warning", _ahead(8)), alert("Old Advisory", _ago(1))],
        }
        b = Build(_net({"nws_alerts": entry}, {ALERTS: DEAD}))
        alerts = self.section(b.html, "NWS ALERTS")
        self.assertIn(LABEL, alerts)
        self.assertIn("Winter Storm Warning", alerts)
        self.assertNotIn("Old Advisory", alerts)

    def test_alerts_go_stale_fast(self):
        entry = {
            "fetched_at": _ago(13),
            "data": [
                {"event": "Winter Storm Warning", "severity": "s", "urgency": "u",
                 "headline": "", "description": "", "expires": _ahead(8)}
            ],
        }
        b = Build(_net({"nws_alerts": entry}, {ALERTS: DEAD}))
        self.assertIn("<b>NWS ALERTS</b>\n\n[unavailable: ", b.html)
        self.assertNotIn("Winter Storm Warning", b.html)

    def test_copy_with_nothing_to_show_is_not_hidden(self):
        # a normal-conditions ercot copy would hide as a calm day; a copy
        # must still say today's fetch failed
        entry = {
            "fetched_at": _ago(1),
            "data": {"source": "gridstatus.io", "alert_level": "Normal Conditions"},
        }
        from fetchers import ercot

        b = Build(_net({"ercot": entry}, {ercot.ERCOT_RT_URL: DEAD}))
        sec = self.section(b.html, "ERCOT")
        self.assertIn(LABEL, sec)
        self.assertIn("nothing to report in that copy", sec)

    def test_one_feed_failed_shows_its_copy(self):
        from fetchers import hv_rss

        entry = {
            "fetched_at": _ago(24),
            "data": [{"title": "Yesterday police item", "link": "", "published": "", "summary": ""}],
        }
        b = Build(_net({"hv_rss.police": entry}, {hv_rss.FEEDS["police"]: DEAD}))
        police = self.section(b.html, "HV POLICE")
        self.assertIn(LABEL, police)
        self.assertIn("Yesterday police item", police)
        self.assertSectionOk(b, "rss_emergency")
        self.assertSectionOk(b, "rss_fire")
        self.assertNotIn(LABEL, self.section(b.html, "HV FIRE DEPT"))
        self.assertIn("HV_POLICE: (today's fetch failed; copy from ", b.prompt)

    def test_everything_failed_with_yesterdays_copy(self):
        # yesterday's good build, then every source down this morning
        yesterday = json.loads(Build(FakeNet()).store_text)["sections"]
        for entry in yesterday.values():
            entry["fetched_at"] = _ago(24)
        yesterday["nws_forecast"] = _forecast_entry(24)
        net = FakeNet(
            {"https://api.": DEAD, "https://www.": DEAD, "https://raw.": DEAD},
            overrides={lastgood.URL: lastgood.dumps(yesterday).encode()},
        )
        b = Build(net)
        self.assertBuilt(b)
        self.assertIn("every source failed this run. sections below are", b.html)
        # look-ahead and slow-moving sections carry over, labelled
        for title, marker in (
            ("NWS FORECAST", "Next Period"),
            ("HF BANDS", "sfi 124"),
            ("HV EMERGENCY ALERTS", "Test emergency item"),
        ):
            sec = self.section(b.html, title)
            self.assertIn(LABEL, sec, title)
            self.assertIn(marker, sec, title)
        # fast-moving ones don't
        for title in ("NWS ALERTS", "ERCOT", "GHOSTMAPS"):
            self.assertIn(f"<b>{title}</b>\n\n[unavailable: ", b.html, title)
        self.assertIn("[ai summary unavailable", b.html)

    def test_previous_copy_unreachable_or_garbage(self):
        for name, result in (
            ("dns", DEAD),
            ("html", b"<html>not json</html>"),
            ("wrong version", json.dumps({"v": 99, "sections": {}}).encode()),
            ("bad entries", json.dumps({"v": 1, "sections": {"nws_forecast": 5}}).encode()),
            ("bad data", json.dumps(
                {"v": 1, "sections": {"nws_forecast": {"fetched_at": _ago(1), "data": 5}}}
            ).encode()),
            ("bad time", json.dumps(
                {"v": 1, "sections": {"nws_forecast": {"fetched_at": "x", "data": []}}}
            ).encode()),
        ):
            with self.subTest(previous=name):
                b = Build(FakeNet({lastgood.URL: result, POINTS: DEAD}))
                self.assertBuilt(b)
                self.assertSectionUnavailable(b, "nws_forecast")
                for key in SECTIONS:
                    if key != "nws_forecast":
                        self.assertSectionOk(b, key)
                self.assertIn(BLUF, b.html)


if __name__ == "__main__":
    unittest.main()
