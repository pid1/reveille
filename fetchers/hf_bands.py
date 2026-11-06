"""hf band conditions from n0nbh's solar-terrestrial xml feed at hamqsl.com.

one ~1.6KB request per build, no key. n0nbh asks that the feed be polled no
more than hourly (its flux values update hourly, the rest every 3 hours) and
that hamqsl.com be credited, so the rendered section names and links it.

the day/night conditions are n0nbh's calculated estimates per band group,
derived from the noaa swpc indices in the same feed. they are general
guidance for the whole hf picture, not a prediction for any one path.

noaa swpc's own json was the alternative. it is first-party and keyless,
but it has the indices only, no per-band conditions, and getting sfi, k and
a out of it takes three requests instead of one.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from fetchers.base import get_bytes

URL = "https://www.hamqsl.com/solarxml.php"
CREDIT_URL = "https://www.hamqsl.com/"

# the four band groups the feed calculates, in display order.
BANDS = ("80m-40m", "30m-20m", "17m-15m", "12m-10m")

# every value lands in a fixed-width line; anything longer than this is not
# a reading the feed is known to send, so cut it rather than break the layout.
_MAX_VALUE = 16


def parse(xml_bytes: bytes) -> dict:
    """parse the solarxml.php body. raises on anything that isn't the feed."""
    root = ET.fromstring(xml_bytes)
    sd = root.find("solardata")
    if sd is None:
        raise RuntimeError("no <solardata> in hamqsl response")

    def text(tag: str) -> str:
        el = sd.find(tag)
        if el is None or not el.text:
            return ""
        return " ".join(el.text.split())[:_MAX_VALUE]

    conditions: dict[str, dict[str, str]] = {"day": {}, "night": {}}
    for band in sd.iterfind("calculatedconditions/band"):
        name, time = band.get("name"), band.get("time")
        if name in BANDS and time in conditions and band.text:
            conditions[time][name] = band.text.strip().lower()[:_MAX_VALUE]
    if not conditions["day"] and not conditions["night"]:
        raise RuntimeError("no band conditions in hamqsl response")

    return {
        "sfi": text("solarflux"),
        "ssn": text("sunspots"),
        "a": text("aindex"),
        "k": text("kindex"),
        "xray": text("xray"),
        "geomag": text("geomagfield").lower(),
        "noise": text("signalnoise").lower(),
        "conditions": conditions,
    }


def fetch() -> dict:
    return parse(get_bytes(URL))
