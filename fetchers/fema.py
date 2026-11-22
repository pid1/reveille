"""fema: open disaster recovery centers nearby, and recent disaster
declarations for the county. both are empty on a normal day, and the page
shows nothing then.

recovery centers come from fema's gis layer (updated hourly), filtered to
the home county and its neighbours. declarations come from openfema,
limited to the last 60 days. neither needs a key.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta

from fetchers.base import get_json

DRC_URL = "https://gis.fema.gov/arcgis/rest/services/FEMA/DRC/MapServer/0/query"
DECL_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

COUNTIES = ("DENTON", "TARRANT", "DALLAS", "COLLIN", "WISE", "COOKE", "GRAYSON")
HOME_COUNTY = "Denton (County)"
STATE = "TX"
DECLARATION_DAYS = 60


def _drcs() -> list[dict]:
    # a plain state query; fema's server hangs on functions like UPPER(),
    # so the county filter happens here
    where = f"state='{STATE}'"
    q = urllib.parse.urlencode(
        {
            "where": where,
            "outFields": "drc_name,street_1,city,county_parish,status,hours",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    # gis.fema.gov often holds a request open (two of three hung for 25 s
    # on 2026-11-15) and answers the next one in half a second, so it gets
    # three short tries rather than one long one
    for attempt in range(3):
        try:
            doc = get_json(f"{DRC_URL}?{q}", timeout=8.0)
            break
        except Exception:
            if attempt == 2:
                raise
    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        raise RuntimeError("unexpected fema drc response")
    out = []
    for f in doc["features"]:
        a = f.get("attributes") or {}
        if str(a.get("county_parish") or "").strip().upper() not in COUNTIES:
            continue
        if str(a.get("status") or "open").lower().startswith("clos"):
            continue
        out.append(
            {
                "name": (a.get("drc_name") or "").strip(),
                "address": (a.get("street_1") or "").strip(),
                "city": (a.get("city") or "").strip(),
                "hours": (a.get("hours") or "").strip(),
            }
        )
    return out


def _declarations() -> list[dict]:
    since = (datetime.now(UTC) - timedelta(days=DECLARATION_DAYS)).strftime("%Y-%m-%dT00:00:00.000Z")
    q = urllib.parse.urlencode(
        {
            "$filter": f"state eq '{STATE}' and designatedArea eq '{HOME_COUNTY}' and declarationDate ge '{since}'",
            "$orderby": "declarationDate desc",
            "$top": "5",
            "$select": "femaDeclarationString,declarationType,declarationTitle,declarationDate,incidentType",
        },
        quote_via=urllib.parse.quote,
    )
    doc = get_json(f"{DECL_URL}?{q}", timeout=30.0)
    rows = doc.get("DisasterDeclarationsSummaries") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("unexpected openfema response")
    seen, out = set(), []
    for r in rows:
        key = r.get("femaDeclarationString")
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": key or "",
                "title": (r.get("declarationTitle") or r.get("incidentType") or "").strip(),
                "date": (r.get("declarationDate") or "")[:10],
            }
        )
    return out


# two sources, so the flaky gis server can't hide a declaration
def fetch_drcs() -> list[dict]:
    return _drcs()


def fetch_declarations() -> list[dict]:
    return _declarations()
