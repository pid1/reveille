"""parse nws text products: segments, ugc codes, vtec lines, polygons, tags.

pure functions over the product text. nothing here touches the network, so
the whole parser is tested against real products in tests/fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

# /O.NEW.KFWD.TO.W.0028.260619T0923Z-260619T1015Z/
_VTEC = re.compile(
    r"/([OTEX])\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\."
    r"(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/"
)
# a ugc block starts a line with SSF (state, C or Z) and ends with the
# DDHHMM- purge time. it may wrap across several lines.
_UGC_START = re.compile(r"^[A-Z]{2}[CZ]\d{3}[->]", re.M)
_UGC_BLOCK = re.compile(r"^([A-Z]{2}[CZ][\d>\-\s]*?\d{6}-)\s*$", re.M | re.S)
_POLY = re.compile(r"LAT\.\.\.LON((?:\s+\d{4,5}\s+\d{4,5})+)")
_AWIPS = re.compile(r"^[A-Z]{4}\d{2} [A-Z]{4} \d{6}.*\n([A-Z0-9]{4,6})\s*$", re.M)

# wording that turns a routine update into one worth a push.
_EMERGENCY_TAGS = {
    "tornado emergency": re.compile(r"tornado emergency", re.I),
    "flash flood emergency": re.compile(r"flash flood emergency", re.I),
    "particularly dangerous situation": re.compile(
        r"particularly dangerous situation", re.I
    ),
    "catastrophic damage threat": re.compile(
        r"damage threat\.\.\.catastrophic", re.I
    ),
    "destructive storm": re.compile(r"damage threat\.\.\.destructive", re.I),
    "tornado observed": re.compile(r"tornado\.\.\.observed", re.I),
}


@dataclass(frozen=True)
class Vtec:
    product_class: str  # O operational, T test, E experimental, X exp. vtec
    action: str  # NEW CON EXT EXA EXB UPG CAN EXP COR ROU
    office: str  # KFWD
    phenomena: str  # TO SV FF ...
    significance: str  # W warning, A watch, Y advisory, S statement
    etn: int
    begin: datetime | None
    end: datetime | None

    @property
    def event_key(self) -> str:
        """stable id for one event across all its updates."""
        year = (self.begin or self.end or datetime.now(UTC)).year
        return (
            f"{year}.{self.office}.{self.phenomena}.{self.significance}.{self.etn:04d}"
        )

    @property
    def code(self) -> str:
        return f"{self.phenomena}.{self.significance}"


@dataclass
class Segment:
    ugc: set[str]
    vtec: list[Vtec]
    polygon: list[tuple[float, float]]  # (lat, lon), lon negative west
    text: str
    tags: set[str] = field(default_factory=set)


@dataclass
class Product:
    awips_id: str  # TORFWD
    text: str
    segments: list[Segment]

    @property
    def headline(self) -> str:
        """the product's own name line, e.g. 'Tornado Warning'."""
        for line in self.text.splitlines()[2:15]:
            s = line.strip()
            if s and not s.startswith(("/", "BULLETIN", "URGENT")) and not _UGC_START.match(s):
                if not re.match(r"^[A-Z]{4}\d{2} ", s) and s != self.awips_id:
                    return s
        return self.awips_id


def _vtec_time(s: str) -> datetime | None:
    if s.startswith("000000"):
        return None
    return datetime.strptime(s, "%y%m%dT%H%MZ").replace(tzinfo=UTC)


def parse_vtec(text: str) -> list[Vtec]:
    out = []
    for m in _VTEC.finditer(text):
        cls, act, office, ph, sig, etn, b, e = m.groups()
        out.append(Vtec(cls, act, office, ph, sig, int(etn), _vtec_time(b), _vtec_time(e)))
    return out


def parse_ugc(block: str) -> set[str]:
    """expand 'TXC085-121>123-TXZ103-151200-' into individual codes."""
    codes: set[str] = set()
    prefix = ""
    for tok in re.sub(r"\s+", "", block).split("-"):
        if not tok or re.fullmatch(r"\d{6}", tok):
            continue
        m = re.fullmatch(r"([A-Z]{2}[CZ])?(\d{3})(?:>(\d{3}))?", tok)
        if not m:
            continue
        if m.group(1):
            prefix = m.group(1)
        if not prefix:
            continue
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        for n in range(lo, hi + 1):
            codes.add(f"{prefix}{n:03d}")
    return codes


def parse_polygon(text: str) -> list[tuple[float, float]]:
    m = _POLY.search(text)
    if not m:
        return []
    nums = m.group(1).split()
    pts = []
    for lat, lon in zip(nums[0::2], nums[1::2], strict=False):
        lon_v = int(lon) / 100.0
        # nws drops the leading 1 for 100-180 west only in some old
        # products; in conus anything under 60 is really 1xx.
        if lon_v < 60:
            lon_v += 100
        pts.append((int(lat) / 100.0, -lon_v))
    return pts


def _tags(text: str) -> set[str]:
    return {name for name, rx in _EMERGENCY_TAGS.items() if rx.search(text)}


def parse_product(text: str) -> Product:
    text = text.replace("\r", "")
    m = _AWIPS.search(text)
    awips = m.group(1) if m else ""
    segments = []
    for chunk in text.split("$$"):
        um = _UGC_BLOCK.search(chunk)
        if not um:
            continue
        segments.append(
            Segment(
                ugc=parse_ugc(um.group(1)),
                vtec=parse_vtec(chunk),
                polygon=parse_polygon(chunk),
                text=chunk.strip(),
                tags=_tags(chunk),
            )
        )
    return Product(awips_id=awips, text=text, segments=segments)
