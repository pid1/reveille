"""graceful degradation: any one source failing must not cost the page.

every test runs the real build.main() against a fake urllib opener that
serves a canned good response for every source, then breaks one of them
in one way (timeout, dns failure, http 5xx, malformed body, missing key).
it then checks that the page was written, that the broken section shows an
[unavailable: ...] notice instead of vanishing, that every other section is
still there, and that the summary prompt and the push still went out.

no real network: an unknown url fails the test.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.parse
import zipfile
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

import build
import lastgood
import notifier
import summarize
from config import TIMEZONE
from fetchers import base, ercot, hf_bands, hv_rss

FIXTURES = Path(__file__).parent / "fixtures"

ALERTS = "https://api.weather.gov/alerts/active"
POINTS = "https://api.weather.gov/points/"
FORECAST = "https://api.weather.gov/gridpoints/FWD/1,1/forecast"
GH_LIST = "https://api.github.com/repos/s2underground/GhostMaps/contents/"
KMZ = "https://raw.githubusercontent.com/test/cip.kmz"
GRIDSTATUS = "https://api.gridstatus.io/"


def _good_bodies() -> dict[str, bytes]:
    now = datetime.now(TIMEZONE)

    def rss(title: str) -> bytes:
        return (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            f"<item><title>{title}</title><link>https://www.highlandvillage.org/x</link>"
            f"<pubDate>{format_datetime(now)}</pubDate>"
            "<description>body</description></item></channel></rss>"
        ).encode()

    kml = (
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Folder><name>Crime</name><Placemark><name>Test Incident</name>"
        "<description><![CDATA[<table>"
        "<tr><td>CrimeType</td><td>Test crime type</td></tr>"
        f"<tr><td>Date</td><td>{now.month}/{now.day}/{now.year}</td></tr>"
        "</table>]]></description>"
        "<Point><coordinates>-97.04,33.10,0</coordinates></Point>"
        "</Placemark></Folder></Document></kml>"
    )
    kmz = io.BytesIO()
    with zipfile.ZipFile(kmz, "w") as z:
        z.writestr("doc.kml", kml)

    return {
        ALERTS: json.dumps(
            {
                "features": [
                    {
                        "properties": {
                            "event": "Severe Thunderstorm Watch",
                            "severity": "Severe",
                            "urgency": "Expected",
                            "headline": "watch until 9pm",
                            "description": "large hail possible",
                            "expires": "2026-11-06T21:00:00-06:00",
                        }
                    }
                ]
            }
        ).encode(),
        POINTS: json.dumps({"properties": {"forecast": FORECAST}}).encode(),
        FORECAST: json.dumps(
            {
                "properties": {
                    "periods": [
                        {
                            "name": "Tonight",
                            "temperature": 55,
                            "temperatureUnit": "F",
                            "windSpeed": "10 mph",
                            "windDirection": "N",
                            "shortForecast": "Test Forecast Text",
                            "probabilityOfPrecipitation": {"value": 20},
                        }
                    ]
                }
            }
        ).encode(),
        ercot.ERCOT_RT_URL: (
            b'<table><tr><td class="tdLeft">Actual System Demand</td>'
            b'<td class="labelClassCenter">45,000</td></tr>'
            b'<tr><td class="tdLeft">Total System Capacity (not including '
            b'Ancillary Services)</td><td class="labelClassCenter">67,000</td></tr>'
            b"</table>"
        ),
        GRIDSTATUS: json.dumps(
            {"data": [{"title": "Watch Test", "eea_level": 0, "load": 44000}]}
        ).encode(),
        hv_rss.FEEDS["emergency"]: rss("Test emergency item"),
        hv_rss.FEEDS["police"]: rss("Test police item"),
        hv_rss.FEEDS["fire"]: rss("Test fire item"),
        GH_LIST: json.dumps(
            [
                {
                    "type": "file",
                    "name": "S2Underground_Common_Intelligence_Picture_November_6_2026.kmz",
                    "path": "x/cip.kmz",
                    "size": len(kmz.getvalue()),
                    "download_url": KMZ,
                }
            ]
        ).encode(),
        KMZ: kmz.getvalue(),
        hf_bands.URL: (FIXTURES / "hamqsl_solarxml.xml").read_bytes(),
        summarize.ANTHROPIC_API: json.dumps(
            {"content": [{"type": "text", "text": "storms tonight, hail possible."}]}
        ).encode(),
        notifier.PUSHOVER_URL: json.dumps({"status": 1}).encode(),
        # a first deploy: no previous copy of anything
        lastgood.URL: lastgood.dumps({}).encode(),
    }


# what each section looks like on a good build: (section title, text in it)
SECTIONS = {
    "nws_alerts": ("NWS ALERTS", "Severe Thunderstorm Watch"),
    "nws_forecast": ("NWS FORECAST", "Test Forecast Text"),
    "ercot": ("ERCOT", "45,000 MW"),
    "ghostmaps": ("GHOSTMAPS", "Test Incident"),
    "rss_emergency": ("HV EMERGENCY ALERTS", "Test emergency item"),
    "rss_police": ("HV POLICE", "Test police item"),
    "rss_fire": ("HV FIRE DEPT", "Test fire item"),
    "hf_bands": ("HF BANDS", "sfi 124"),
}
BLUF = "storms tonight, hail possible."

# which urls make up each source, and which page sections it feeds
SOURCES = {
    "nws_alerts": ([ALERTS], ["nws_alerts"]),
    "nws_forecast": ([POINTS], ["nws_forecast"]),
    "ercot": ([ercot.ERCOT_RT_URL], ["ercot"]),
    "rss_emergency": ([hv_rss.FEEDS["emergency"]], ["rss_emergency"]),
    "rss_police": ([hv_rss.FEEDS["police"]], ["rss_police"]),
    "rss_fire": ([hv_rss.FEEDS["fire"]], ["rss_fire"]),
    "rss_all": (list(hv_rss.FEEDS.values()), ["rss_emergency", "rss_police", "rss_fire"]),
    "ghostmaps_listing": ([GH_LIST], ["ghostmaps"]),
    "ghostmaps_kmz": ([KMZ], ["ghostmaps"]),
    "hf_bands": ([hf_bands.URL], ["hf_bands"]),
}

# summary-blob label for each section, to check the prompt still goes out
BLOB = {
    "nws_alerts": "NWS_ALERTS",
    "nws_forecast": "NWS_FORECAST",
    "ercot": "ERCOT",
    "ghostmaps": "GHOSTMAPS_NEARBY",
    "rss_emergency": "HV_EMERGENCY",
    "rss_police": "HV_POLICE",
    "rss_fire": "HV_FIRE",
    "hf_bands": "HF_PROPAGATION",
}


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, code, "err", {}, io.BytesIO(b"<html>upstream error</html>")
    )


# each failure: a function of the url that raises, or returns a bad body
FAILURES = {
    "connect_timeout": lambda url: urllib.error.URLError(TimeoutError("timed out")),
    "read_timeout": lambda url: TimeoutError("The read operation timed out"),
    "dns": lambda url: urllib.error.URLError(
        socket.gaierror(8, "nodename nor servname provided, or not known")
    ),
    "http_503": lambda url: _http_error(url, 503),
    "http_500": lambda url: _http_error(url, 500),
    # a captive portal, a cdn error page served as 200, a renamed endpoint
    "malformed": lambda url: b"<html><body>captive portal</body></html>",
}


class FakeNet:
    """stands in for urllib.request.urlopen. routes by url prefix."""

    def __init__(self, broken: dict[str, object] | None = None, overrides=None):
        self.bodies = _good_bodies()
        self.bodies.update(overrides or {})
        self.broken = broken or {}  # prefix -> exception or bytes
        self.calls: list[tuple[str, bytes | None]] = []
        self.unknown: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, req.data))
        assert timeout, f"no timeout on {url}"
        for prefix, result in self.broken.items():
            if url.startswith(prefix):
                if isinstance(result, BaseException):
                    raise result
                return io.BytesIO(result)
        for prefix, body in self.bodies.items():
            if url.startswith(prefix):
                return io.BytesIO(body)
        self.unknown.append(url)
        raise urllib.error.URLError("unknown url in test")

    def body_sent_to(self, prefix: str) -> str | None:
        for url, data in self.calls:
            if url.startswith(prefix):
                return (data or b"").decode()
        return None


KEYS = {
    "ANTHROPIC_API_KEY": "test-anthropic",
    "PUSHOVER_API_KEY": "test-token",
    "PUSHOVER_USER_KEY": "test-user",
}


class Build:
    """one build.main() run against a FakeNet; holds the results."""

    def __init__(self, net: FakeNet, env: dict | None = None):
        self.net = net
        env = KEYS if env is None else env
        log = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(build, "DIST", Path(tmp)),
            mock.patch("urllib.request.urlopen", net),
            mock.patch.object(ercot.time, "sleep", lambda s: None),
            mock.patch.dict(os.environ, env),
            contextlib.redirect_stdout(log),
        ):
            self.rc = build.main()
            self.html = (Path(tmp) / "index.html").read_text()
            store = Path(tmp) / lastgood.FILENAME
            self.store_text = store.read_text() if store.exists() else None
        self.log = log.getvalue()
        self.prompt = None
        sent = net.body_sent_to(summarize.ANTHROPIC_API)
        if sent:
            self.prompt = json.loads(sent)["messages"][0]["content"]
        self.pushed = net.body_sent_to(notifier.PUSHOVER_URL)
        self.push_message = None
        if self.pushed:
            self.push_message = urllib.parse.parse_qs(self.pushed)["message"][0]


class PageAsserts:
    def assertSectionOk(self, b: Build, key: str):
        title, marker = SECTIONS[key]
        self.assertIn(f"<b>{title}</b>", b.html, key)
        self.assertIn(marker, b.html, key)

    def assertSectionUnavailable(self, b: Build, key: str):
        title, marker = SECTIONS[key]
        self.assertIn(f"<b>{title}</b>\n\n[unavailable: ", b.html, key)
        self.assertNotIn(marker, b.html, key)

    def assertBuilt(self, b: Build):
        self.assertEqual(b.rc, 0)
        self.assertEqual(b.net.unknown, [])
        self.assertTrue(b.html.startswith("<!doctype html>"))
        self.assertTrue(b.html.rstrip().endswith("</html>"))


class DegradationTest(PageAsserts, unittest.TestCase):
    def test_all_good(self):
        b = Build(FakeNet())
        self.assertBuilt(b)
        self.assertIn(BLUF, b.html)
        for key in SECTIONS:
            self.assertSectionOk(b, key)
        self.assertEqual(b.push_message, BLUF)

    def test_each_source_each_failure(self):
        for source, (prefixes, failed) in SOURCES.items():
            for mode, make in FAILURES.items():
                with self.subTest(source=source, failure=mode):
                    net = FakeNet({p: make(p) for p in prefixes})
                    b = Build(net)
                    self.assertBuilt(b)
                    for key in SECTIONS:
                        if key in failed:
                            self.assertSectionUnavailable(b, key)
                        else:
                            self.assertSectionOk(b, key)
                    # the summary still went out, saying which part is missing
                    self.assertIsNotNone(b.prompt)
                    for key in failed:
                        self.assertIn(f"{BLOB[key]}: unavailable (", b.prompt)
                    self.assertIn(BLUF, b.html)
                    self.assertIsNotNone(b.pushed)

    def test_anthropic_failures(self):
        for mode, make in FAILURES.items():
            with self.subTest(failure=mode):
                api = summarize.ANTHROPIC_API
                b = Build(FakeNet({api: make(api)}))
                self.assertBuilt(b)
                self.assertIn("[ai summary unavailable -- see raw data below]", b.html)
                for key in SECTIONS:
                    self.assertSectionOk(b, key)
                # no BLUF, so the short fallback push instead
                self.assertEqual(b.push_message, notifier.FALLBACK_MESSAGE)

    def test_anthropic_reply_without_text(self):
        api = summarize.ANTHROPIC_API
        reply = json.dumps({"content": [{"type": "thinking"}], "stop_reason": "max_tokens"})
        b = Build(FakeNet({api: reply.encode()}))
        self.assertBuilt(b)
        self.assertIn("[ai summary unavailable", b.html)
        self.assertIn("no text block", b.html)

    def test_no_anthropic_key_still_pushes_fallback(self):
        env = {k: v for k, v in KEYS.items() if k != "ANTHROPIC_API_KEY"}
        b = Build(FakeNet(), env=env)
        self.assertBuilt(b)
        self.assertEqual(b.push_message, "briefing up, summary unavailable.")

    def test_nstr_day_sends_nothing(self):
        api = summarize.ANTHROPIC_API
        reply = json.dumps({"content": [{"type": "text", "text": "NSTR."}]})
        b = Build(FakeNet(overrides={api: reply.encode()}))
        self.assertBuilt(b)
        self.assertIsNone(b.pushed)

    def test_pushover_failures(self):
        for mode, make in FAILURES.items():
            with self.subTest(failure=mode):
                url = notifier.PUSHOVER_URL
                b = Build(FakeNet({url: make(url)}))
                self.assertBuilt(b)
                self.assertIn(BLUF, b.html)
                for key in SECTIONS:
                    self.assertSectionOk(b, key)
                self.assertIn("[pushover] send failed", b.log)

    def test_pushover_rejects(self):
        url = notifier.PUSHOVER_URL
        b = Build(FakeNet({url: b'{"status":0,"errors":["user key is invalid"]}'}))
        self.assertBuilt(b)
        self.assertIn("[pushover] api rejected", b.log)

    def test_no_keys(self):
        b = Build(FakeNet(), env={})
        self.assertBuilt(b)
        self.assertIn("reason: ANTHROPIC_API_KEY not set", b.html)
        for key in SECTIONS:
            self.assertSectionOk(b, key)
        self.assertIsNone(b.prompt)
        self.assertIsNone(b.pushed)
        self.assertIsNone(b.net.body_sent_to(GRIDSTATUS))

    def test_gridstatus_failure_falls_back_to_ercot_html(self):
        env = {**KEYS, "GRIDSTATUS_API_KEY": "test-grid"}
        good = Build(FakeNet(), env=env)
        self.assertIn("alert level     Watch Test", good.html)
        for mode, make in FAILURES.items():
            with self.subTest(failure=mode):
                b = Build(FakeNet({GRIDSTATUS: make(GRIDSTATUS)}), env=env)
                self.assertBuilt(b)
                self.assertSectionOk(b, "ercot")
                self.assertIn("note: gridstatus.io failed", b.html)

    def test_gridstatus_and_ercot_html_both_fail(self):
        env = {**KEYS, "GRIDSTATUS_API_KEY": "test-grid"}
        dead = urllib.error.URLError("down")
        b = Build(FakeNet({GRIDSTATUS: dead, ercot.ERCOT_RT_URL: dead}), env=env)
        self.assertBuilt(b)
        self.assertSectionUnavailable(b, "ercot")
        self.assertSectionOk(b, "nws_forecast")

    def test_valid_json_wrong_shape(self):
        # 200 with json that isn't an alerts response must not read as
        # "no alerts", which hides the section
        b = Build(FakeNet({ALERTS: b"{}"}))
        self.assertBuilt(b)
        self.assertSectionUnavailable(b, "nws_alerts")
        self.assertIn("NWS_ALERTS: unavailable (", b.prompt)

    def test_garbage_field_types_cost_one_section(self):
        # well-formed json whose fields are the wrong type gets past the
        # fetcher and has to be stopped at render, not crash the build
        period = {"name": "Tonight", "temperature": 55, "shortForecast": ["Rain"]}
        fc = {"properties": {"periods": [period]}}
        b = Build(FakeNet({FORECAST: json.dumps(fc).encode()}))
        self.assertBuilt(b)
        self.assertIn("<b>NWS FORECAST</b>\n\n[unavailable: ", b.html)
        for key in SECTIONS:
            if key != "nws_forecast":
                self.assertSectionOk(b, key)
        self.assertIn(BLUF, b.html)

    def test_long_error_body_is_cut_on_the_page(self):
        big = urllib.error.HTTPError(ALERTS, 502, "bad", {}, io.BytesIO(b"x" * 5000))
        b = Build(FakeNet({ALERTS: big}))
        line = next(l for l in b.html.splitlines() if l.startswith("[unavailable: "))
        self.assertLessEqual(len(line), 200)

    def test_everything_failed(self):
        dead = urllib.error.URLError(socket.gaierror(8, "no dns"))
        net = FakeNet({"https://": dead})
        b = Build(net)
        self.assertBuilt(b)
        self.assertIn("[ai summary unavailable", b.html)
        for key in SECTIONS:
            title, _ = SECTIONS[key]
            self.assertIn(f"<b>{title}</b>\n\n[unavailable: ", b.html, key)
        self.assertIn("every source failed", b.html)
        # the url in "fetching <url>: <reason>" links without the colon
        self.assertIn('<a href="https://api.weather.gov/alerts/active?', b.html)
        self.assertNotIn(':">', b.html)


class DeadlineTest(unittest.TestCase):
    """the socket timeout bounds each read, not the whole response. a server
    that trickles bytes can hold a fetch open far longer than `timeout`."""

    def test_trickling_response_hits_the_total_deadline(self):
        clock = [0.0]

        class Trickle(io.RawIOBase):
            def read(self, n=-1):
                clock[0] += 10.0  # each read takes 10s of "wall time"
                return b"x"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with (
            mock.patch("urllib.request.urlopen", lambda req, timeout: Trickle()),
            mock.patch.object(base.time, "monotonic", lambda: clock[0]),
        ):
            with self.assertRaises(RuntimeError) as cm:
                base.get_bytes("https://example.test/slow", timeout=5.0)
        self.assertIn("deadline", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
