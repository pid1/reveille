"""pure-python rendering. minimal html. no css. browser-default monospace.

every _render_* helper takes the relevant envelope and returns a string.
unavailable envelopes still produce a section -- they don't get dropped.

strategy: the page is essentially a plaintext document.
- a `<pre>` block wraps everything, so the browser default monospace font,
  size, line-height, and color theme are honored without any inline css.
- section headers are bare uppercase text + an underline of '=' characters.
- links use <a href=...> wrapped inline so they're clickable. the rest is
  plain text.
- no font choices, no width constraints, no colors. minimal HTML.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from html import escape as h
from typing import Any

from config import CITY
from fetchers.base import truncate as _truncate

COL = 48  # target line width for plaintext wrap. tuned so output of _wrap()
# fits a typical mobile viewport (~360-430px) at the browser-default
# monospace size WITHOUT requiring the browser to soft-wrap. that matters
# because soft-wrapped continuation lines lose their indent (they reset to
# column 0), so a 'hanging indent' has to be baked in by _wrap() itself.
# desktop viewports just see slightly narrower text, which matches the
# rest of the design.


# -- helpers ----------------------------------------------------------------


# sentinel pair for bolding section headers. _rule() emits these around the
# title text; _linkify()'s post-pass rewrites them to <b>...</b> after html
# escaping has run. printable markers (vs control chars) make the rendered
# html easier to read in 'view source' while still being implausible enough
# to never appear in legitimate fetched content.
_B_OPEN = "[[B]]"
_B_CLOSE = "[[/B]]"


def _rule(title: str) -> str:
    # title is emitted verbatim, wrapped in bold sentinels (rewritten to
    # <b>...</b> by _linkify) and surrounded by a leading + trailing blank
    # line so the header sits in its own visual paragraph. callers pass
    # the exact text they want displayed -- typically short uppercase
    # labels like 'NWS ALERTS', 'ERCOT', 'GHOSTMAPS'.
    return f"\n{_B_OPEN}{title}{_B_CLOSE}\n"


def _wrap(text: str, indent: str = "  ", width: int = COL) -> str:
    """wrap text to width characters with the given indent on continuation lines.

    splits on whitespace; preserves single-word lines that are longer than width.
    returns a single string with embedded newlines.
    """
    import textwrap

    if not text:
        return ""
    return textwrap.fill(
        text,
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


# -- ai summary -------------------------------------------------------------


def _render_summary(env: dict) -> str:
    parts = [_rule("BLUF")]
    if env.get("status") == "ok" and env.get("data"):
        parts.append(env["data"])
    else:
        # why is in the failed line at the top; the raw sections follow
        parts.append("[summary unavailable; raw data below]")
    return "\n".join(parts)


# -- nws --------------------------------------------------------------------


def _render_nws_alerts(env: dict) -> str:
    if env["status"] != "ok":
        return ""  # named in the failed line at the top
    alerts = env["data"] or []
    if not alerts:
        # hide the section entirely. no active alerts is a routine state;
        # making the reader skim past 'no active alerts' every morning
        # is noise. errors stay visible above; this only hides the
        # ok-and-empty case.
        return ""
    out = [_rule("NWS ALERTS")]
    for i, a in enumerate(alerts):
        if i:
            out.append("")
        out.append(f"{a['event']} ({a['severity']} / {a['urgency']})")
        if a.get("headline"):
            out.append(_wrap(a["headline"]))
        if a.get("expires"):
            out.append(f"  expires: {a['expires']}")
        if a.get("description"):
            out.append(_wrap(a["description"]))
    return "\n".join(out)


def _render_nws_forecast(env: dict) -> str:
    if env["status"] != "ok":
        return ""  # named in the failed line at the top
    out = [_rule("NWS FORECAST")]
    periods = env["data"] or []
    if not periods:
        out.append("no forecast periods returned.")
        return "\n".join(out)
    for i, p in enumerate(periods):
        if i:
            out.append("")
        out.append(f"{p['name']}")
        temp = f"{p['temperature']} {p['temperatureUnit']}"
        wind = f"wind {p['windSpeed']} {p['windDirection']}".rstrip()
        precip = ""
        if p.get("precipPct") is not None:
            precip = f", {p['precipPct']}% precip"
        out.append(f"  {temp}, {wind}{precip}")
        if p.get("shortForecast"):
            out.append(_wrap(p["shortForecast"]))
    return "\n".join(out)


# -- ercot ------------------------------------------------------------------


def _render_ercot(env: dict) -> str:
    if env["status"] != "ok":
        return ""  # named in the failed line at the top
    d = env["data"] or {}

    # decide whether to hide the section. there are two fetch paths:
    #   - gridstatus.io: 'alert_level' carries the human-readable 'title'
    #     column verbatim. on calm days every row has title='Normal
    #     Conditions' -- verified against ~30 days of dataset preview;
    #     no other title value observed in that window. hide on that
    #     exact string and only that. anything else (EEA levels,
    #     weather advisories, anything we haven't catalogued yet) falls
    #     through and renders. if new normal-day titles surface in
    #     the wild, extend this set.
    #   - ercot.com html scrape: gives raw frequency/load/capacity but
    #     no EEA signal at all. user said 'scrape and don't draw
    #     conclusions', so the html-fallback path always shows the raw
    #     numbers and lets the reader decide. detect this path via the
    #     'gridstatus_error' field that ercot.fetch() sets when it falls
    #     through, or by the 'source' field that records which path won.
    source = d.get("source") or ""
    on_html_fallback = source.startswith("ercot.com") or bool(d.get("gridstatus_error"))

    _NORMAL_TITLES = {"normal conditions"}
    if not on_html_fallback:
        title = (d.get("alert_level") or "").strip().lower()
        if title in _NORMAL_TITLES:
            return ""

    out = [_rule("ERCOT")]
    if d.get("alert_level"):
        out.append(f"  alert level     {d['alert_level']}")
    if d.get("condition_note"):
        out.append(f"  condition       {d['condition_note']}")
    if d.get("frequency_hz") is not None:
        out.append(f"  frequency       {d['frequency_hz']} hz")
    if d.get("load_mw") is not None:
        out.append(f"  current load    {d['load_mw']:,} MW")
    if d.get("capacity_mw") is not None:
        out.append(f"  total capacity  {d['capacity_mw']:,} MW")
    if d.get("reserve_margin_mw") is not None:
        out.append(f"  reserve margin  {d['reserve_margin_mw']:,} MW")
    if d.get("renewable_share_pct") is not None:
        out.append(f"  renewable share {d['renewable_share_pct']}%")
    if d.get("gridstatus_error"):
        # provenance note, not a conclusion. tells the reader the numbers
        # came from the ercot.com scrape and no EEA signal is available.
        out.append("  note: gridstatus.io failed, showing ercot.com scrape only")
        out.append(f"        ({d['gridstatus_error']})")
    return "\n".join(out)


# -- ghostmaps --------------------------------------------------------------


def _render_ghostmaps(env: dict) -> str:
    """ghostmaps section is special: each incident block contains real <a href>
    hyperlinks to research sources, which means this section cannot live inside
    the outer <pre>. so it's a sequence of (text, link, text, link, ...) lines
    each emitted as its own line outside <pre>.
    """
    if env["status"] != "ok":
        return ""  # named in the failed line at the top
    d = env["data"] or {}
    nearby = d.get("nearby") or []
    if not nearby:
        # hide the section entirely on a clean day. errors stay visible
        # above; this only hides the ok-and-empty case.
        return ""
    out = [_rule("GHOSTMAPS")]
    for i, n in enumerate(nearby):
        if i:
            out.append("")
        folder = f"  ({n['folder']})" if n.get("folder") else ""
        out.append(f"{n['distance_mi']}mi {n['bearing']} -- {n['name']}{folder}")
        for label, value in n.get("fields", []):
            # fields are short; wrap if long
            line = f"  {label:<12} {value}"
            if len(line) > COL:
                line = f"  {label}:\n" + _wrap(value, indent="    ")
            out.append(line)
        cas = n.get("casualties") or {}
        if cas:
            parts = [f"{k} {v}" for k, v in cas.items()]
            out.append(_wrap("casualties: " + ", ".join(parts)))
        urls = n.get("research_urls") or []
        if urls:
            out.append("  research:")
            for u in urls:
                # emit a real anchor; this requires the section to be
                # rendered outside <pre>. handled by the page assembler.
                out.append(f"    {u}")
        elif not n.get("fields") and n.get("fallback_text"):
            out.append(_wrap(n["fallback_text"]))
    return "\n".join(out)


# -- hf bands ---------------------------------------------------------------


def _render_hf_bands(env: dict) -> str:
    """the band x day/night grid, and a vhf line only when something is open.
    the indices go to the summary, not the page; the credit to n0nbh lives
    in fetchers/hf_bands.py and the readme. always shown: the reader looks
    for it every morning.
    """
    from fetchers.hf_bands import BANDS

    if env["status"] != "ok":
        return ""  # named in the failed line at the top
    d = env["data"] or {}
    cond = d.get("conditions") or {}
    header = " ".join(f"{b.replace('m', ''):<5}" for b in BANDS)
    out = [_rule("HF BANDS"), f"      {header}".rstrip()]
    for time in ("day", "night"):
        row = cond.get(time) or {}
        cells = " ".join(f"{row.get(b) or '-':<5}" for b in BANDS)
        out.append(f"{time:<5} {cells}".rstrip())
    open_ = [f"{k} {v}" for k, v in (d.get("vhf") or {}).items() if v and "closed" not in v]
    if open_:
        out.append("vhf   " + ", ".join(open_))
    return "\n".join(out)


# -- spc, changes, city news, source status ----------------------------------

CITY_NEWS_URL = "https://www.highlandvillage.org/CivicAlerts.aspx"


def _render_spc(env: dict) -> str:
    """one line, only when some day has a risk. a failed lookup shows up in
    the source-status line at the bottom instead of as a section."""
    from fetchers import spc

    if env["status"] != "ok" or not spc.any_risk(env["data"]):
        return ""
    d = env["data"]
    cells = "  ".join(f"d{n} {spc.label(d.get(str(n), ''))}" for n in (1, 2, 3))
    return f"\nSPC  {cells}"


def _render_fema(decl: dict, drc: dict) -> str:
    """disaster declarations for the county in the last 60 days, and open
    recovery centers nearby. nothing at all on a normal day."""
    lines = []
    if decl.get("status") == "ok":
        for d in decl["data"] or []:
            lines.append(_wrap(f"declared {d['date']}: {d['title'].lower()} ({d['id']})", indent="").replace("\n", "\n  "))
    if drc.get("status") == "ok":
        for c in drc["data"] or []:
            where = ", ".join(x for x in (c["name"], c["address"], c["city"]) if x).lower()
            hours = f" ({c['hours'].lower()})" if c.get("hours") else ""
            lines.append(_wrap(f"recovery center: {where}{hours}", indent="").replace("\n", "\n  "))
    if not lines:
        return ""
    label = ""
    if drc.get("stale_from"):
        label = "[recovery centers: fetch failed; older copy]\n"
    return f"{_rule('FEMA')}\n{label}" + "\n".join(lines)


def _render_changes(changes: dict | None) -> str:
    """what started and what ended since the previous briefing's alerts."""
    if not changes:
        return ""
    parts = []
    if changes.get("new"):
        parts.append("new: " + ", ".join(changes["new"]).lower() + ".")
    if changes.get("ended"):
        parts.append("ended: " + ", ".join(changes["ended"]).lower() + ".")
    if not parts:
        return ""
    return "\n" + _wrap("since yesterday: " + " ".join(parts), indent="").replace("\n", "\n  ")


def _render_city_news(env: dict, now: datetime) -> str:
    """the city feeds are no longer listed; the summary reports anything that
    matters. one link, only when something was posted in the last two days."""
    if env["status"] != "ok":
        return ""
    cutoff = now - timedelta(days=2)
    n = 0
    for key in ("emergency", "police", "fire"):
        for e in (env["data"] or {}).get(key) or []:
            try:
                if datetime.fromisoformat(e.get("published") or "") >= cutoff:
                    n += 1
            except TypeError, ValueError:
                continue
    if not n:
        return ""
    posts = "post" if n == 1 else "posts"
    return f"\ncity news: {n} {posts} in 2 days {CITY_NEWS_URL}"


_SOURCE_NAMES = {
    "nws_alerts": "nws alerts",
    "nws_forecast": "nws forecast",
    "ercot": "ercot",
    "ghostmaps": "ghostmaps",
    "hf_bands": "hf bands",
    "spc": "spc outlook",
    "fema_decl": "fema declarations",
    "fema_drc": "fema recovery centers",
}


def short_reason(error: str | None) -> str:
    """'http 500', 'timeout', 'dns' ... never the upstream's own text, which
    has been a whole html error page."""
    e = (error or "").lower()
    if "nas relay" in e:
        return "nas relay stale" if "stale" in e else "nas relay not running" if "nothing" in e else "nas relay"
    m = __import__("re").search(r"http (\d{3})", e)
    if m:
        return f"http {m.group(1)}"
    if "timed out" in e or "timeout" in e or "deadline" in e:
        return "timeout"
    if "nodename" in e or "name or service" in e or "getaddrinfo" in e or "gaierror" in e or "name resolution" in e:
        return "dns"
    if "network error" in e or "connection" in e:
        return "network"
    if "not set" in e:
        return "not configured"
    return "bad data"


def _render_failed(sections: dict, summary: dict | None = None) -> str:
    """one line under the timestamp naming what failed this run, with a
    short reason. absent when everything worked."""
    bad = []
    if summary is not None and summary.get("status") != "ok":
        bad.append(f"summary ({short_reason(summary.get('error'))})")
    for key, name in _SOURCE_NAMES.items():
        env = sections.get(key)
        if env is None:
            continue
        if env.get("stale_from"):
            bad.append(f"{name} ({short_reason(env.get('today_error'))}, older copy shown)")
        elif env.get("status") != "ok":
            bad.append(f"{name} ({short_reason(env.get('error'))})")
    rss = sections.get("hv_rss") or {}
    feeds = ("emergency", "police", "fire")
    if rss.get("status") != "ok":
        bad += [f"city {f} feed ({short_reason(rss.get('error'))})" for f in feeds]
    else:
        data = rss.get("data") or {}
        for f in feeds:
            if f in (data.get("stale") or {}):
                why = short_reason((rss.get("today_errors") or {}).get(f) or rss.get("today_error"))
                bad.append(f"city {f} feed ({why}, older copy used)")
            elif f in (data.get("errors") or {}):
                bad.append(f"city {f} feed ({short_reason(data['errors'][f])})")
    bad += [f"{t} (display error)" for t in _RENDER_FAILED]
    if not bad:
        return ""
    return "failed: " + ", ".join(bad)


# -- linkification ----------------------------------------------------------


def _linkify(text: str) -> str:
    """html-escape `text`, then turn any bare http(s) urls into <a href> anchors
    whose visible text is just the hostname.

    rationale: full urls on a narrow mobile viewport wrap awkwardly because they
    contain no break points until pre-wrap+overflow-wrap forces character-level
    breaks. displaying just the hostname (with any leading 'www.' stripped)
    keeps the anchor short enough to never need wrapping while still telling
    the reader where the link goes; the href stays exact, so the destination
    is unchanged.

    operates on the already-escaped text so the surrounding content is safe.
    only matches whitespace-terminated http/https urls.
    """
    import re
    from urllib.parse import urlsplit

    # quote=False: inside <pre> text we only need to escape & < >, not ' or ".
    # this keeps Austin Metcalf's as a literal apostrophe in view-source.
    escaped = h(text, quote=False)
    # match http(s)://... up to whitespace or end-of-line. allow trailing
    # punctuation but strip it from the link.
    pat = re.compile(r"(https?://[^\s<>]+)")

    def repl(m):
        url = m.group(1)
        trailing = ""
        # peel back trailing punctuation that's usually sentence punctuation
        # (':' too: error notices read "fetching <url>: <reason>")
        while url and url[-1] in ".,);]:":
            trailing = url[-1] + trailing
            url = url[:-1]
        # derive a short display label from the hostname. fall back to the
        # full url if urlsplit can't parse one out (shouldn't happen for a
        # well-formed match, but defensive).
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        host = host.removeprefix("www.")
        label = host or url
        # the href value still needs the same escape pass; h() over the
        # already-escaped url is fine because & < > stay encoded.
        return f'<a href="{url}">{label}</a>{trailing}'

    linked = pat.sub(repl, escaped)
    # finally, rewrite the section-header bold sentinels emitted by _rule()
    # into real <b>...</b> tags. has to happen after html escaping so the
    # tags survive into the output.
    return linked.replace(_B_OPEN, "<b>").replace(_B_CLOSE, "</b>")


# -- last good copies -------------------------------------------------------


def _mark_stale(title: str, text: str, stale_from: str) -> str:
    """label a section rendered from the previous build's copy. a copy is
    never hidden as a quiet day: the reader needs to see that today's fetch
    failed even when the copy had nothing to show.
    """
    try:
        when = datetime.fromisoformat(stale_from).strftime("%a %H:%M")
    except TypeError, ValueError:
        when = "an earlier build"
    label = f"[fetch failed; showing copy from {when}]"
    header = _rule(title)
    if not text:
        return f"{header}\n{label}\n  nothing to report in that copy"
    return f"{header}\n{label}{text[len(header):]}"


def _failed_today(env: dict) -> bool:
    return env.get("status") != "ok" or bool(env.get("today_error"))


# -- top-level --------------------------------------------------------------


_RENDER_FAILED: list[str] = []


def _guard(title: str, fn, *args) -> str:
    """render one section; if a bad value got past its fetcher and the
    renderer chokes on it, that section says so and the page still builds.
    """
    try:
        return fn(*args)
    except Exception as e:
        print(f"[render] {title} failed: {type(e).__name__}: {e}", flush=True)
        _RENDER_FAILED.append(title.lower())
        return ""


def _header_block(now: datetime) -> str:
    # the only thing the page-top still needs is the freshness indicator.
    # the 'reveille' brand line and 'generated' prefix are both redundant
    # given the page url, the page title, and the timestamp's position at
    # the top of the page. the bare timestamp earns its keep because the
    # page is a morning briefing; how stale the data is matters.
    return now.strftime("%Y-%m-%d %H:%M %Z")


def render_text(
    sections: dict[str, Any],
    summary: dict,
    now: datetime,
    changes: dict | None = None,
) -> str:
    """the page as plain text, before html escaping and links. also served
    as /today.txt."""
    # assemble all sections as plaintext, then linkify -- turn any bare
    # http(s) urls into <a href> anchors. the entire body sits inside one
    # <pre> so the browser uses its default monospace font and the user
    # picks their own colors / size via their browser config.
    # each _render_* helper returns either a rendered section (starting with
    # _rule()'s leading newline) or an empty string when the section is
    # ok-and-empty and the policy is to hide it. errors always produce a
    # rendered section so silent failures are not possible. filter empties
    # before joining so we don't end up with double blank lines.
    # the city feeds are not listed any more (the summary reads them and
    # reports what matters); a single link appears when they have news.
    # (title, renderer, envelope)
    parts = [
        ("NWS ALERTS", _render_nws_alerts, sections["nws_alerts"]),
        ("NWS FORECAST", _render_nws_forecast, sections["nws_forecast"]),
        ("ERCOT", _render_ercot, sections["ercot"]),
        ("GHOSTMAPS", _render_ghostmaps, sections["ghostmaps"]),
        ("HF BANDS", _render_hf_bands, sections["hf_bands"]),
    ]
    _RENDER_FAILED.clear()
    rendered = [
        _header_block(now),
        _guard("BLUF", _render_summary, summary),
        _guard("CHANGES", _render_changes, changes),
    ]
    for title, fn, env in parts:
        text = _guard(title, fn, env)
        if env.get("stale_from"):
            text = _mark_stale(title, text, env["stale_from"])
        rendered.append(text)
        if title == "NWS ALERTS" and "spc" in sections:
            rendered.append(_guard("SPC", _render_spc, sections["spc"]))
        if title == "NWS ALERTS" and "fema_decl" in sections:
            rendered.append(_guard("FEMA", _render_fema, sections["fema_decl"], sections.get("fema_drc") or {}))
    rendered.append(_guard("CITY NEWS", _render_city_news, sections["hv_rss"], now))
    failed = _render_failed(sections, summary)
    if failed:
        # right under the timestamp: a reader has to know the alerts fetch
        # failed before reading a quiet page as a quiet day
        rendered[0] += "\n" + failed
    if all(_failed_today(env) for env in sections.values()):
        # every section below is unavailable or an older copy; say it once up
        # top too, so a reader on a bad link doesn't take any of it as current.
        rendered.insert(
            1,
            "\nevery source failed this run. anything below is an"
            "\nolder copy.",
        )
    return "\n".join(s for s in rendered if s) + "\n"


def plain(text: str) -> str:
    """render_text() output with the bold markers removed, for today.txt."""
    return text.replace(_B_OPEN, "").replace(_B_CLOSE, "")


def render_page(
    sections: dict[str, Any],
    summary: dict,
    now: datetime,
    commit_sha: str | None = None,  # kept for build.py compatibility; unused
    changes: dict | None = None,
    text: str | None = None,
) -> str:
    if text is None:
        text = render_text(sections, summary, now, changes)
    body = _linkify(text.rstrip("\n"))

    # the only css on the page: make the <pre> wrap on narrow viewports so
    # mobile users don't have to scroll horizontally. pre-wrap preserves
    # the existing newlines and indentation; overflow-wrap lets long
    # unbreakable tokens (urls, the '====' underlines, iso timestamps)
    # break at any character rather than push the viewport wider.
    return (
        "<!doctype html>\n"
        '<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>reveille -- {h(CITY)} -- {now.strftime('%Y-%m-%d')}</title>\n"
        "<style>pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>\n"
        "</head><body>\n"
        f"<pre>{body}</pre>\n"
        "</body></html>\n"
    )
