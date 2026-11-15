"""hf band conditions: parser against a captured hamqsl response, and render."""

import unittest
from datetime import datetime
from pathlib import Path

from fetchers import hf_bands
from fetchers.base import ok, unavailable

SAMPLE = (Path(__file__).parent / "fixtures" / "hamqsl_solarxml.xml").read_bytes()


class ParseTest(unittest.TestCase):
    def test_captured_sample(self):
        d = hf_bands.parse(SAMPLE)
        self.assertEqual(d["sfi"], "124")
        self.assertEqual(d["ssn"], "109")
        self.assertEqual(d["a"], "6")
        self.assertEqual(d["k"], "2")
        self.assertEqual(d["xray"], "B8.7")
        self.assertEqual(d["geomag"], "quiet")
        self.assertEqual(d["noise"], "s1-s2")
        self.assertEqual(
            d["conditions"]["day"],
            {"80m-40m": "fair", "30m-20m": "good", "17m-15m": "fair", "12m-10m": "fair"},
        )
        self.assertEqual(d["conditions"]["night"]["12m-10m"], "poor")

    def test_not_xml_raises(self):
        with self.assertRaises(Exception):
            hf_bands.parse(b"<html><body>502 bad gateway")

    def test_xml_without_conditions_raises(self):
        with self.assertRaises(RuntimeError):
            hf_bands.parse(b"<solar><solardata><solarflux>1</solarflux></solardata></solar>")

    def test_overlong_values_are_cut(self):
        body = SAMPLE.replace(b"<solarflux>124", b"<solarflux>" + b"9" * 500)
        self.assertEqual(len(hf_bands.parse(body)["sfi"]), 16)


class RenderTest(unittest.TestCase):
    def test_render(self):
        from render import _linkify, _render_hf_bands

        text = _render_hf_bands(ok(hf_bands.parse(SAMPLE)))
        self.assertEqual(
            text,
            "\n[[B]]HF BANDS[[/B]]\n\n"
            "  sfi 124  ssn 109  a 6  k 2  xray B8.7\n"
            "  geomag quiet  noise s1-s2\n"
            "        80-40 30-20 17-15 12-10\n"
            "  day   fair  good  fair  fair\n"
            "  night good  good  fair  poor\n"
            "  N0NBH https://www.hamqsl.com/",
        )
        # every line fits the page's mobile width
        self.assertTrue(all(len(line) <= 48 for line in text.splitlines()))
        self.assertIn('N0NBH <a href="https://www.hamqsl.com/">hamqsl.com</a>', _linkify(text))

    def test_missing_band_shows_dash(self):
        from render import _render_hf_bands

        d = hf_bands.parse(SAMPLE)
        del d["conditions"]["night"]["12m-10m"]
        self.assertIn("  night good  good  fair  -", _render_hf_bands(ok(d)))

    def test_unavailable(self):
        from render import _render_hf_bands

        text = _render_hf_bands(unavailable("RuntimeError: HTTP 503"))
        self.assertIn("HF BANDS", text)
        self.assertIn("[unavailable: RuntimeError: HTTP 503]", text)


class SummaryBlobTest(unittest.TestCase):
    def test_one_line_of_indices(self):
        from summarize import _fmt_hf_bands

        self.assertEqual(
            _fmt_hf_bands(ok(hf_bands.parse(SAMPLE))),
            "sfi 124, ssn 109, a 6, k 2, xray B8.7, geomag quiet",
        )
        self.assertEqual(_fmt_hf_bands(unavailable("x")), "unavailable (x)")


if __name__ == "__main__":
    unittest.main()
