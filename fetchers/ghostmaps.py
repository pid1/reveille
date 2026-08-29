"""ghostmaps common intelligence picture: kmz -> kml -> filtered placemarks.

confirmed file layout in master database directory (verified at impl time):
filenames look like
  S2Underground_Common_Intelligence_Picture_<Month>_<Day>_<Year>_Export_<N>.kmz
e.g. S2Underground_Common_Intelligence_Picture_May_18_2026_Export_2.kmz
files are ~3.3MB each, ~20 entries on a rolling basis. one canonical "latest"
is the one with the most recent date in the filename, breaking ties by export
number. this is the primary selection strategy; commit-history and alphabetic
fallbacks are kept for defensive purposes.
"""

from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from math import asin, atan2, cos, degrees, radians, sin, sqrt

from config import GHOSTMAPS_RADIUS_MILES, LAT, LON, TIMEZONE
from fetchers.base import extract_hrefs, get_bytes, get_json, strip_html, truncate

# show only incidents dated today or yesterday. ghostmaps Date fields are
# day-granular (m/d/yyyy, no time-of-day component), so the cutoff sits at
# 'midnight today minus N days'. setting N=1 yields a window that covers
# today + yesterday's incidents, which is the closest the data granularity
# allows to 'past 24 hours'. anything older is excluded -- the user has
# already seen it on previous mornings' briefings and is unlikely to act
# on it today.
MAX_AGE_DAYS = 1

REPO = "s2underground/GhostMaps"
DIR_PATH = "ArcGIS Data for ATAK (KMZs)/Common Intelligence Picture/Master Database"
MAX_KMZ_BYTES = 25 * 1024 * 1024  # 25mb

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

_FILENAME_RE = re.compile(
    r"S2Underground_Common_Intelligence_Picture_"
    r"(?P<month>[A-Za-z]+)_(?P<day>\d{1,2})_(?P<year>\d{4})"
    r"(?:_Export_(?P<n>\d+))?",
    re.IGNORECASE,
)


def _gh_headers() -> dict:
    h = {"accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return h


def _list_dir() -> list[dict]:
    from urllib.parse import quote

    url = f"https://api.github.com/repos/{REPO}/contents/{quote(DIR_PATH)}"
    items = get_json(url, headers=_gh_headers())
    if not isinstance(items, list):
        raise RuntimeError(
            f"unexpected response listing {DIR_PATH}: {type(items).__name__}"
        )
    return items


def _parse_filename_date(name: str) -> tuple[datetime, int] | None:
    m = _FILENAME_RE.search(name)
    if not m:
        return None
    month_name = m.group("month").capitalize()
    if month_name not in _MONTHS:
        return None
    try:
        d = datetime(int(m.group("year")), _MONTHS[month_name], int(m.group("day")))
    except ValueError:
        return None
    n = int(m.group("n") or 0)
    return d, n


def _select_latest(files: list[dict]) -> tuple[dict, str]:
    """returns (file_entry, strategy_used)."""
    candidates = [
        f
        for f in files
        if f.get("type") == "file" and f.get("name", "").lower().endswith(".kmz")
    ]
    if not candidates:
        raise RuntimeError("no .kmz files found in master database")

    # primary: filename date
    dated = []
    for f in candidates:
        parsed = _parse_filename_date(f["name"])
        if parsed:
            dated.append((parsed, f))
    if dated:
        dated.sort(key=lambda x: (x[0][0], x[0][1]), reverse=True)
        return dated[0][1], "filename_date"

    # fallback: commit history (one extra api call per candidate, scoped)
    from urllib.parse import quote

    best = None
    best_ts = ""
    for f in candidates[:25]:  # cap to be polite
        url = (
            f"https://api.github.com/repos/{REPO}/commits"
            f"?path={quote(f['path'])}&per_page=1"
        )
        try:
            commits = get_json(url, headers=_gh_headers())
            if commits:
                ts = commits[0].get("commit", {}).get("committer", {}).get("date", "")
                if ts > best_ts:
                    best_ts = ts
                    best = f
        except Exception:
            continue
    if best is not None:
        return best, "commit_history"

    # last resort: alphabetic
    candidates.sort(key=lambda f: f["name"])
    return candidates[-1], "alphabetic"


def _iter_placemarks(root: ET.Element):
    """yield every Placemark element regardless of namespace."""
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "Placemark":
            yield el


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el.iter():
        if _local(c.tag) == name:
            return c
    return None


def _direct_child(el: ET.Element, name: str) -> ET.Element | None:
    for c in list(el):
        if _local(c.tag) == name:
            return c
    return None


def _coords_from_text(text: str) -> list[tuple[float, float]]:
    """parse a KML coordinates string -> list of (lat, lon) tuples."""
    out: list[tuple[float, float]] = []
    if not text:
        return out
    for token in text.split():
        parts = token.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        out.append((lat, lon))
    return out


def _centroid(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not pts:
        return None
    lat = sum(p[0] for p in pts) / len(pts)
    lon = sum(p[1] for p in pts) / len(pts)
    return lat, lon


def _placemark_point(pm: ET.Element) -> tuple[float, float] | None:
    """extract a representative (lat, lon) from a Placemark."""
    # Point
    pt = _find_child(pm, "Point")
    if pt is not None:
        c = _find_child(pt, "coordinates")
        if c is not None and c.text:
            pts = _coords_from_text(c.text)
            if pts:
                return pts[0]
    # LineString
    ls = _find_child(pm, "LineString")
    if ls is not None:
        c = _find_child(ls, "coordinates")
        if c is not None and c.text:
            return _centroid(_coords_from_text(c.text))
    # Polygon
    poly = _find_child(pm, "Polygon")
    if poly is not None:
        c = _find_child(poly, "coordinates")
        if c is not None and c.text:
            return _centroid(_coords_from_text(c.text))
    return None


def _enclosing_folder(pm: ET.Element, root: ET.Element) -> str:
    """walk the kml tree to find the nearest enclosing <Folder>'s <name>.

    ET doesn't expose parent pointers; build a parent map once per call.
    cache on the root element via a sentinel attribute.
    """
    pmap = getattr(root, "_pmap", None)
    if pmap is None:
        pmap = {c: p for p in root.iter() for c in p}
        # not strictly thread-safe but we're single-threaded
        try:
            root._pmap = pmap  # type: ignore[attr-defined]
        except Exception:
            pass
    cur = pmap.get(pm)
    while cur is not None:
        if _local(cur.tag) == "Folder":
            n = _direct_child(cur, "name")
            if n is not None and n.text:
                return n.text.strip()
        cur = pmap.get(cur)
    return ""


# GhostMaps placemark <description> bodies are little HTML documents
# containing one or more <table>s of <td>key</td><td>value</td> rows.
# we extract those pairs verbatim, drop null / category-header noise, and
# pull research urls out of the <a href> tags separately so the renderer
# can present them as real links.
_TD_PAIR_RE = re.compile(
    r"<td[^>]*>(?P<k>.*?)</td>\s*<td[^>]*>(?P<v>.*?)</td>",
    re.DOTALL | re.IGNORECASE,
)

_NULL_TOKENS = {"", "<null>", "null", "&lt;null&gt;", "n/a", "na", "-"}

# only surface fields we care about, in this display order. anything not
# in this list is dropped from the rendered output (still available in
# extra dict if we want it later).
_DISPLAY_FIELDS: list[tuple[str, str]] = [
    # (canonical_key, label)
    ("AttackType", "type"),
    ("CrimeType", "type"),
    ("InstallationType", "type"),
    ("Date", "date"),
    ("OperationalStatus", "status"),
    ("Affiliation", "affiliation"),
    ("AttackMotive", "motive"),
    ("Notes", "notes"),
    ("Description", "description"),
    ("FullStreetAddress", "address"),
    ("GeolocationNotes", "geo notes"),
    ("TribeClanGroup", "group"),
    ("Installation Name", "installation"),
    ("ReportCredit", "report credit"),
]

_CASUALTY_FIELDS = [
    "Enemy Killed",
    "Enemy Wounded",
    "Friendly Killed",
    "Friendly Wounded",
    "Civilian Killed",
    "Civilian Wounded",
    "Neutral Killed",
    "Neutral Wounded",
    "Unknown Killed",
    "Unknown Wounded",
]


def _norm_key(raw: str) -> str:
    """strip tags / whitespace / the colspan-merged SHAPE artifact."""
    txt = strip_html(raw or "")
    # the category-header cells look like 'Arson\n\n...SHAPE' due to a
    # colspan layout quirk; throw those away
    if "SHAPE" in txt and len(txt) > 6:
        return ""
    return txt.strip()


def _norm_value(raw: str) -> str:
    txt = strip_html(raw or "").strip()
    if txt.lower() in _NULL_TOKENS:
        return ""
    return txt


def _parse_description(
    desc_html: str,
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """parse a placemark description html.

    returns (fields, research_urls, casualties) where:
      fields:       canonical_key -> cleaned value (only display fields)
      research_urls: deduplicated list of http(s) urls from any "Research*" row
      casualties:   subset of casualty-count fields with nonzero values
    """
    if not desc_html:
        return {}, [], {}

    fields: dict[str, str] = {}
    research_urls: list[str] = []
    casualties: dict[str, str] = {}
    seen_research: set[str] = set()

    for m in _TD_PAIR_RE.finditer(desc_html):
        k = _norm_key(m.group("k"))
        if not k:
            continue
        v_raw = m.group("v") or ""

        # research fields: harvest urls (links), discard the visible text
        if k.lower().replace(" ", "").startswith("research"):
            for u in extract_hrefs(v_raw):
                if u not in seen_research:
                    seen_research.add(u)
                    research_urls.append(u)
            continue

        v = _norm_value(v_raw)
        if not v:
            continue

        if k in _CASUALTY_FIELDS:
            # only surface nonzero casualty rows
            if v not in {"0", "0.0"}:
                casualties[k.lower()] = v
            continue

        # only keep the first hit per canonical key (some files repeat)
        if k not in fields:
            fields[k] = v

    return fields, research_urls, casualties


_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{2,4})\s*$")


def _parse_incident_date(s: str) -> datetime | None:
    """parse a ghostmaps placemark Date field. accepts m/d/yyyy and m-d-yyyy.

    2-digit years are rejected (ambiguous). returns a tz-aware datetime
    at midnight in the configured timezone, or None if unparseable.
    """
    if not s:
        return None
    m = _DATE_RE.match(s)
    if not m:
        return None
    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yr < 100:
        return None
    try:
        return datetime(yr, mo, da, tzinfo=TIMEZONE)
    except ValueError:
        return None


def _display_fields(fields: dict[str, str]) -> list[tuple[str, str]]:
    """return [(label, value)] in the order defined by _DISPLAY_FIELDS,
    only including fields actually present and non-empty.
    """
    out: list[tuple[str, str]] = []
    used_labels: set[str] = set()
    for canonical, label in _DISPLAY_FIELDS:
        v = fields.get(canonical)
        if not v:
            continue
        # avoid two different canonicals both rendering as 'type'
        if label in used_labels:
            continue
        used_labels.add(label)
        out.append((label, v))
    return out


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.7613
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


_COMPASS_16 = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    y = sin(dlon) * cos(rlat2)
    x = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    brng = (degrees(atan2(y, x)) + 360.0) % 360.0
    idx = int((brng + 11.25) // 22.5) % 16
    return _COMPASS_16[idx]


def fetch() -> dict:
    files = _list_dir()
    sel, strategy = _select_latest(files)

    size = sel.get("size") or 0
    if size and size > MAX_KMZ_BYTES:
        raise RuntimeError(f"selected kmz {sel.get('name')} too large: {size} bytes")

    download_url = sel.get("download_url")
    if not download_url:
        raise RuntimeError(f"no download_url for {sel.get('name')}")

    kmz_bytes = get_bytes(download_url)
    if len(kmz_bytes) > MAX_KMZ_BYTES:
        raise RuntimeError(f"downloaded kmz too large: {len(kmz_bytes)} bytes")

    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        kml_names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise RuntimeError("no .kml inside kmz")
        kml_bytes = z.read(kml_names[0])

    try:
        root = ET.fromstring(kml_bytes)
    except ET.ParseError as e:
        raise RuntimeError(f"kml parse error: {e}") from e

    total = 0
    skipped = 0
    nearby: list[dict] = []
    seen: set[tuple[str, float, float]] = set()
    # cutoff at midnight `MAX_AGE_DAYS` ago, so the window includes all of
    # the boundary day rather than half-clipping by current-time-of-day
    today_midnight = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cutoff = today_midnight - timedelta(days=MAX_AGE_DAYS)

    for pm in _iter_placemarks(root):
        total += 1
        try:
            name_el = _direct_child(pm, "name")
            name = (
                (name_el.text or "").strip()
                if name_el is not None and name_el.text
                else ""
            )
            if not name:
                skipped += 1
                continue
            coords = _placemark_point(pm)
            if coords is None:
                skipped += 1
                continue
            lat, lon = coords
            dist = _haversine_miles(LAT, LON, lat, lon)
            if dist > GHOSTMAPS_RADIUS_MILES:
                continue
            key = (name, round(lat, 3), round(lon, 3))
            if key in seen:
                continue
            seen.add(key)
            desc_el = _direct_child(pm, "description")
            desc_text = desc_el.text if desc_el is not None else ""
            folder = _enclosing_folder(pm, root)
            parsed_fields, research_urls, casualties = _parse_description(
                desc_text or ""
            )

            # recency filter: drop anything older than the cutoff, or anything
            # without a parseable Date field. installations (safehouses, etc.)
            # have no Date and are excluded by design -- this section is for
            # recent incidents only.
            incident_dt = _parse_incident_date(parsed_fields.get("Date", ""))
            if incident_dt is None or incident_dt < cutoff:
                continue

            nearby.append(
                {
                    "name": name,
                    "distance_mi": round(dist, 1),
                    "bearing": _bearing(LAT, LON, lat, lon),
                    "folder": folder,
                    "fields": _display_fields(parsed_fields),
                    "casualties": casualties,
                    "research_urls": research_urls,
                    # keep a stripped fallback summary in case nothing parsed;
                    # truncated, used only when fields/research are both empty
                    "fallback_text": truncate(strip_html(desc_text or ""), 200),
                }
            )
        except Exception:
            skipped += 1
            continue

    nearby.sort(key=lambda x: x["distance_mi"])

    return {
        "source_file": sel.get("name", ""),
        "source_strategy": strategy,
        "total_placemarks": total,
        "nearby": nearby,
        "skipped_geometry": skipped,
    }
