"""decide which product segments become a push, and at what priority.

the rules, in order:

- only operational vtec (class O) counts; test and experimental products
  never push.
- a segment is about the house when its polygon contains the home point,
  or, with no polygon, when its ugc codes include the home county or zone.
  it is "nearby" when the polygon only crosses the home grid square.
  anything else is ignored, even inside the home county.
- new, expanded and upgraded events push. continuations and extensions
  stay silent unless they bring emergency wording the event did not have,
  move from nearby to the house, or are the first time we have seen the
  event (the listener was down when it was issued).
- cancellations and expirations of an event we pushed send one quiet
  all-clear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import geo
from .product import Product, Segment, Vtec

HERE, NEARBY = "here", "nearby"
_RANK = {NEARBY: 1, HERE: 2}

# pushover priorities
EMERGENCY, HIGH, NORMAL, QUIET = 2, 1, 0, -1

NAMES = {
    "TO.W": "tornado warning",
    "SV.W": "severe thunderstorm warning",
    "FF.W": "flash flood warning",
    "EW.W": "extreme wind warning",
    "TO.A": "tornado watch",
    "SV.A": "severe thunderstorm watch",
    "FF.A": "flood watch",
    "FA.W": "flood warning",
    "FA.Y": "flood advisory",
    "WS.W": "winter storm warning",
    "IS.W": "ice storm warning",
    "BZ.W": "blizzard warning",
    "WW.Y": "winter weather advisory",
    "HW.W": "high wind warning",
    "WI.Y": "wind advisory",
    "XH.W": "extreme heat warning",
    "XH.A": "extreme heat watch",
    "HT.Y": "heat advisory",
    "EH.W": "excessive heat warning",
    "FW.W": "red flag warning",
    "FW.A": "fire weather watch",
    "FZ.W": "freeze warning",
    "HZ.W": "hard freeze warning",
    "EC.W": "extreme cold warning",
    "CW.Y": "cold weather advisory",
    "DS.W": "dust storm warning",
    "FG.Y": "dense fog advisory",
}

# convective warnings that matter when they only clip the grid square
_NEARBY_PRIORITY = {"TO.W": HIGH, "EW.W": HIGH, "SV.W": NORMAL, "FF.W": NORMAL}
_HERE_PRIORITY = {"TO.W": EMERGENCY, "EW.W": EMERGENCY, "SV.W": HIGH, "FF.W": HIGH}
_EMERGENCY_TAGS = {"tornado emergency", "flash flood emergency"}
_NOTEWORTHY_TAGS = _EMERGENCY_TAGS | {
    "particularly dangerous situation",
    "catastrophic damage threat",
    "destructive storm",
    "tornado observed",
}
_START = {"NEW", "EXA", "EXB"}
_ONGOING = {"CON", "EXT", "COR"}
_END = {"CAN", "EXP"}


@dataclass
class Home:
    lat: float
    lon: float
    ugc: set[str]
    grid: str

    @property
    def box(self) -> tuple[float, float, float, float]:
        return geo.grid_box(self.grid)


@dataclass
class Push:
    key: str
    title: str
    message: str
    priority: int
    url: str | None = None
    kind: str = "alert"  # alert | clear


@dataclass
class EventState:
    where: str
    priority: int
    tags: set[str] = field(default_factory=set)
    pushed: bool = False
    ended: bool = False
    end: str | None = None
    seen: str = ""


def where(seg: Segment, home: Home) -> str | None:
    if seg.polygon:
        if geo.contains(seg.polygon, (home.lat, home.lon)):
            return HERE
        if geo.overlaps_box(seg.polygon, home.box):
            return NEARBY
        return None
    if seg.ugc & home.ugc:
        return HERE
    return None


def priority(code: str, loc: str, tags: set[str]) -> int | None:
    """None means never push this."""
    if loc == HERE and tags & _EMERGENCY_TAGS:
        return EMERGENCY
    if loc == NEARBY:
        return _NEARBY_PRIORITY.get(code)
    if code in _HERE_PRIORITY:
        return _HERE_PRIORITY[code]
    sig = code.split(".")[1]
    if sig == "W":
        return NORMAL
    if code in ("TO.A", "SV.A"):
        return NORMAL
    if sig == "A":
        return QUIET
    if sig == "Y":
        return QUIET
    return None  # statements and anything else


def _local(dt: datetime | None, tz) -> str:
    if not dt:
        return ""
    return dt.astimezone(tz).strftime("%-I:%M%p").lower()


def _lead(seg: Segment) -> str:
    """the 'at 423 am, a severe thunderstorm was located...' sentence."""
    lines = [ln.strip() for ln in seg.text.splitlines()]
    for i, ln in enumerate(lines):
        s = ln.lstrip("* ").strip()
        if s.lower().startswith("at ") and ("located" in s.lower() or "," in s[:20]):
            out = [s]
            for nxt in lines[i + 1 :]:
                if not nxt or nxt.startswith("*"):
                    break
                out.append(nxt)
            return " ".join(out)
    return ""


def _hazard(seg: Segment) -> str:
    for ln in seg.text.splitlines():
        s = ln.strip()
        if s.upper().startswith("HAZARD..."):
            return s[9:].strip()
    return ""


def event_url(v: Vtec) -> str:
    year = (v.begin or v.end or datetime.now(UTC)).year
    return (
        f"https://mesonet.agron.iastate.edu/vtec/event/"
        f"{year}-O-NEW-{v.office}-{v.phenomena}-{v.significance}-{v.etn:04d}"
    )


def decide(
    product: Product,
    home: Home,
    state: dict[str, EventState],
    tz,
    min_priority: int = NORMAL,
    now: datetime | None = None,
) -> list[Push]:
    now = now or datetime.now(UTC)
    # gather everything this product says about each event, best location wins
    seen: dict[str, dict] = {}
    elsewhere: dict[str, tuple[Vtec, set[str]]] = {}
    for seg in product.segments:
        loc = where(seg, home)
        if not loc:
            for v in seg.vtec:
                if v.product_class == "O" and v.action not in ("ROU", "UPG"):
                    elsewhere.setdefault(v.event_key, (v, set()))[1].add(v.action)
            continue
        for v in seg.vtec:
            if v.product_class != "O" or v.action in ("ROU", "UPG"):
                continue
            e = seen.setdefault(
                v.event_key, {"vtec": v, "actions": set(), "where": loc, "seg": seg, "tags": set()}
            )
            e["actions"].add(v.action)
            e["tags"] |= seg.tags & _NOTEWORTHY_TAGS
            if _RANK[loc] > _RANK[e["where"]] or (v.action not in _END and e["vtec"].action in _END):
                e.update(where=loc, seg=seg, vtec=v)

    pushes: list[Push] = []
    for key, e in seen.items():
        v: Vtec = e["vtec"]
        loc, tags, seg = e["where"], e["tags"], e["seg"]
        prev = state.get(key)
        live = e["actions"] - _END
        name = NAMES.get(v.code, f"{v.phenomena}.{v.significance} {product.headline}".lower())

        if not live:  # only cancellations / expirations
            if prev and prev.pushed and not prev.ended:
                if "CAN" in e["actions"]:
                    text = f"{name} cancelled at {_local(now, tz)}."
                else:
                    text = f"{name} expired at {_local(v.end or now, tz)}."
                pushes.append(Push(key, f"ended: {name}", text, QUIET, event_url(v), "clear"))
            if prev:
                prev.ended = True
            continue

        p = priority(v.code, loc, tags)
        reason = None
        if prev is None or prev.ended:
            reason = "new" if live & _START else "in effect"
        elif _RANK[loc] > _RANK[prev.where]:
            reason = "now over the house"
        elif tags - prev.tags:
            reason = "upgraded wording"
        elif live & {"EXA", "EXB"} and not prev.pushed:
            reason = "expanded"

        st = prev or EventState(where=loc, priority=p if p is not None else -2)
        st.where = loc if (prev is None or _RANK[loc] >= _RANK[prev.where]) else prev.where
        st.tags |= tags
        st.ended = False
        st.end = v.end.isoformat() if v.end else st.end
        st.seen = now.isoformat()
        state[key] = st

        if reason is None or p is None or p < min_priority:
            continue
        st.pushed = True
        st.priority = p
        title = name if loc == HERE else f"{name} nearby"
        if p >= HIGH:
            title = title.upper()
        until = f"until {_local(v.end, tz)}" if v.end else ""
        bits = [
            ("over the house" if loc == HERE else "crosses the home grid square") + (f", {until}" if until else "") + ".",
        ]
        if tags:
            bits.append(", ".join(sorted(tags)) + ".")
        if reason not in ("new",):
            bits.append(f"({reason})")
        lead = _lead(seg)
        if lead:
            bits.append(lead)
        hz = _hazard(seg)
        if hz:
            bits.append(f"hazard: {hz}")
        msg = " ".join(bits)
        if len(msg) > 1000:
            msg = msg[:996].rsplit(" ", 1)[0] + " ..."
        pushes.append(Push(key, title, msg, p, event_url(v)))

    # an event we pushed that this product only mentions away from the house:
    # the polygon moved off, or the warning ended somewhere we no longer match.
    for key, (v, actions) in elsewhere.items():
        prev = state.get(key)
        if key in seen or not prev or not prev.pushed or prev.ended:
            continue
        name = NAMES.get(v.code, v.code.lower())
        if actions - _END:
            text = f"{name} no longer covers the house ({_local(now, tz)})."
        elif "CAN" in actions:
            text = f"{name} cancelled at {_local(now, tz)}."
        else:
            text = f"{name} expired at {_local(v.end or now, tz)}."
        prev.ended = True
        pushes.append(Push(key, f"ended: {name}", text, QUIET, event_url(v), "clear"))
    return pushes


def prune(state: dict[str, EventState], now: datetime | None = None) -> None:
    """forget events a day after they end, or three days after last seen."""
    now = now or datetime.now(UTC)
    for key in list(state):
        st = state[key]
        try:
            end = datetime.fromisoformat(st.end) if st.end else None
            seen = datetime.fromisoformat(st.seen) if st.seen else None
        except ValueError:
            del state[key]
            continue
        if (end and (now - end).days >= 1) or (seen and (now - seen).days >= 3):
            del state[key]
