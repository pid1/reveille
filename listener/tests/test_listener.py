"""listener tests against real fort worth products. no network, no xmpp.

home points here are taken from the fixtures' own polygons, never from a
real address.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nwws_listener import geo
from nwws_listener.decide import EMERGENCY, HIGH, NEARBY, NORMAL, QUIET, Home, decide, where
from nwws_listener.io import load_state, save_state
from nwws_listener.product import parse_product, parse_ugc

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 3, 4, 0, tzinfo=UTC)


def prod(name: str):
    return parse_product((FIX / f"{name}.txt").read_text())


def home(lat, lon, ugc=()):
    return Home(lat, lon, set(ugc), geo.maidenhead(lat, lon))


def run(names, h, min_priority=QUIET):
    state, out = {}, []
    for n in names:
        out.append(decide(prod(n), h, state, TZ, min_priority, NOW))
    return out, state


class Parse(unittest.TestCase):
    def test_ugc_ranges_and_prefixes(self):
        self.assertEqual(
            parse_ugc("TXC085-121>123-\nTXZ103-151200-"),
            {"TXC085", "TXC121", "TXC122", "TXC123", "TXZ103"},
        )

    def test_tornado_warning(self):
        p = prod("TORFWD-0")
        self.assertEqual(p.awips_id, "TORFWD")
        seg = p.segments[0]
        self.assertEqual(seg.ugc, {"TXC363", "TXC367"})
        v = seg.vtec[0]
        self.assertEqual((v.action, v.code, v.etn, v.office), ("NEW", "TO.W", 28, "KFWD"))
        self.assertEqual(v.event_key, "2026.KFWD.TO.W.0028")
        self.assertEqual(seg.polygon[0], (32.76, -98.24))
        self.assertEqual(len(seg.polygon), 4)

    def test_every_fixture_parses(self):
        for f in sorted(FIX.glob("*.txt")):
            p = parse_product(f.read_text())
            self.assertTrue(p.awips_id, f.name)
            self.assertTrue(p.segments, f.name)


class Geo(unittest.TestCase):
    def test_maidenhead_known_station(self):
        self.assertEqual(geo.maidenhead(41.714775, -72.727260), "FN31pr")  # W1AW

    def test_grid_box_contains_its_point(self):
        s, w, n, e = geo.grid_box(geo.maidenhead(32.8, -97.98))
        self.assertTrue(s <= 32.8 <= n and w <= -97.98 <= e)

    def test_contains(self):
        sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
        self.assertTrue(geo.contains(sq, (0.5, 0.5)))
        self.assertFalse(geo.contains(sq, (1.5, 0.5)))


class Decide(unittest.TestCase):
    def test_tornado_warning_over_the_house_is_emergency(self):
        (pushes,), _ = run(["TORFWD-0"], home(32.80, -97.983))
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0].priority, EMERGENCY)
        self.assertEqual(pushes[0].title, "TORNADO WARNING")
        self.assertIn("Mineral Wells", pushes[0].message)
        self.assertIn("until 5:15am", pushes[0].message)
        self.assertIn("/vtec/event/2026-O-NEW-KFWD-TO-W-0028", pushes[0].url)

    def test_tornado_warning_nearby_is_high(self):
        # just south of the polygon's southern vertex, but in a grid square it crosses
        poly = prod("TORFWD-0").segments[0].polygon
        h = None
        for dlat in (0.005, 0.01, 0.02, 0.03):
            cand = home(32.63 - dlat, -97.78, ["TXC999"])
            if not geo.contains(poly, (cand.lat, cand.lon)) and geo.overlaps_box(poly, cand.box):
                h = cand
                break
        self.assertIsNotNone(h)
        self.assertEqual(where(prod("TORFWD-0").segments[0], h), NEARBY)
        (pushes,), _ = run(["TORFWD-0"], h)
        self.assertEqual(pushes[0].priority, HIGH)
        self.assertTrue(pushes[0].title.endswith("NEARBY"))

    def test_same_county_far_from_polygon_is_ignored(self):
        # palo pinto county, but nowhere near this warning's polygon
        (pushes,), _ = run(["TORFWD-0"], home(32.55, -98.45, ["TXC363"]))
        self.assertEqual(pushes, [])

    def test_watch_lifecycle(self):
        h = home(32.80, -97.98, ["TXC367"])
        (exp_sv, new, con, exp), state = run(["WCNFWD-3", "WCNFWD-2", "WCNFWD-1", "WCNFWD-0"], h)
        self.assertEqual(exp_sv, [])
        self.assertEqual([(p.priority, p.title) for p in new], [(NORMAL, "tornado watch")])
        self.assertEqual(con, [])  # the CAN segment is for other counties; CON is silent
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0].kind, "clear")
        self.assertEqual(exp[0].priority, QUIET)
        self.assertIn("expired at 11:00pm", exp[0].message)

    def test_warning_update_is_silent_then_clears(self):
        seg = prod("SVRFWD-0").segments[0]
        h = home(32.135, -98.722, seg.ugc)
        out, state = run(["SVRFWD-0", "SVSFWD-2", "SVSFWD-1", "SVSFWD-0"], h)
        self.assertEqual(out[0][0].priority, HIGH)
        self.assertEqual(out[0][0].title, "SEVERE THUNDERSTORM WARNING")
        # later statements: silent while it continues, one quiet all-clear at the end
        later = [p for ps in out[1:] for p in ps]
        self.assertEqual(len(later), 1)
        self.assertEqual(later[0].kind, "clear")

    def test_missed_new_is_pushed_once_as_in_effect(self):
        seg = prod("SVSFWD-2").segments[0]
        h = home(32.174, -98.548, seg.ugc)
        out, _ = run(["SVSFWD-2", "SVSFWD-2"], h)
        self.assertEqual(len(out[0]), 1)
        self.assertIn("(in effect)", out[0][0].message)
        self.assertEqual(out[1], [])

    def test_emergency_wording_upgrade_pushes_again(self):
        h = home(32.80, -97.983)
        tor = (FIX / "TORFWD-0.txt").read_text()
        upd = tor.replace("/O.NEW.", "/O.CON.").replace(
            "TORNADO...RADAR INDICATED", "TORNADO...OBSERVED\nTORNADO DAMAGE THREAT...CATASTROPHIC\nThis is a TORNADO EMERGENCY for Weatherford."
        )
        state = {}
        first = decide(parse_product(tor), h, state, TZ, QUIET, NOW)
        again = decide(parse_product(tor.replace("/O.NEW.", "/O.CON.")), h, state, TZ, QUIET, NOW)
        worse = decide(parse_product(upd), h, state, TZ, QUIET, NOW)
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        self.assertEqual(len(worse), 1)
        self.assertEqual(worse[0].priority, EMERGENCY)
        self.assertIn("tornado emergency", worse[0].message)

    def test_test_products_never_push(self):
        h = home(32.80, -97.983)
        tor = (FIX / "TORFWD-0.txt").read_text().replace("/O.NEW.", "/T.NEW.")
        self.assertEqual(decide(parse_product(tor), h, {}, TZ, QUIET, NOW), [])

    def test_advisories_are_below_the_default_threshold(self):
        seg = prod("NPWFWD-1").segments[0]
        h = home(32.8, -97.0, seg.ugc)
        self.assertEqual(decide(prod("NPWFWD-1"), h, {}, TZ, NORMAL, NOW), [])
        self.assertEqual(decide(prod("NPWFWD-1"), h, {}, TZ, QUIET, NOW)[0].priority, QUIET)


class State(unittest.TestCase):
    def test_state_survives_a_restart(self):
        h = home(32.80, -97.983)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            state = {}
            self.assertEqual(len(decide(prod("TORFWD-0"), h, state, TZ, QUIET, NOW)), 1)
            save_state(path, state)
            reloaded = load_state(path)
            self.assertEqual(decide(prod("TORFWD-0"), h, reloaded, TZ, QUIET, NOW), [])
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
