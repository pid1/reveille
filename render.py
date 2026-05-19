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

from datetime import datetime
from html import escape as h
from typing import Any

from config import CITY, STATE


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


def _unavail_line(env: dict) -> str:
    return f"[unavailable: {env.get('error') or 'unknown'}]"


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
        reason = (env or {}).get("error") or "not generated"
        parts.append("[ai summary unavailable -- see raw data below]")
        parts.append(f"reason: {reason}")
    return "\n".join(parts)


# -- nws --------------------------------------------------------------------


def _render_nws_alerts(env: dict) -> str:
    out = [_rule("NWS ALERTS")]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    alerts = env["data"] or []
    if not alerts:
        out.append("no active alerts.")
        return "\n".join(out)
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
    out = [_rule("NWS FORECAST (NEXT ~3 DAYS)")]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
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
    out = [_rule("ERCOT")]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    d = env["data"] or {}
    if d.get("alert_level"):
        out.append(f"  alert level     {d['alert_level']}")
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
        out.append(f"  note: gridstatus.io failed, fell back to ercot html")
        out.append(f"        ({d['gridstatus_error']})")
    return "\n".join(out)


# -- ghostmaps --------------------------------------------------------------


def _render_ghostmaps(env: dict) -> str:
    """ghostmaps section is special: each incident block contains real <a href>
    hyperlinks to research sources, which means this section cannot live inside
    the outer <pre>. so it's a sequence of (text, link, text, link, ...) lines
    each emitted as its own line outside <pre>.
    """
    out = [_rule("GHOSTMAPS")]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    d = env["data"] or {}
    nearby = d.get("nearby") or []
    if not nearby:
        out.append("no incidents within radius in the last 14 days.")
    else:
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


# -- rss --------------------------------------------------------------------


def _render_rss_section(title: str, env: dict, key: str) -> str:
    out = [_rule(title)]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    items = (env["data"] or {}).get(key) or []
    if not items:
        out.append("no entries in last 14 days.")
        return "\n".join(out)
    for i, e in enumerate(items):
        if i:
            out.append("")
        pub = e.get("published") or ""
        title_s = e.get("title") or ""
        out.append(f"{pub}  {title_s}")
        if e.get("link"):
            out.append(f"  link: {e['link']}")
        if e.get("summary"):
            out.append(_wrap(e["summary"]))
    return "\n".join(out)


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
        while url and url[-1] in ".,);]":
            trailing = url[-1] + trailing
            url = url[:-1]
        # derive a short display label from the hostname. fall back to the
        # full url if urlsplit can't parse one out (shouldn't happen for a
        # well-formed match, but defensive).
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        if host.startswith("www."):
            host = host[4:]
        label = host or url
        # the href value still needs the same escape pass; h() over the
        # already-escaped url is fine because & < > stay encoded.
        return f'<a href="{url}">{label}</a>{trailing}'

    linked = pat.sub(repl, escaped)
    # finally, rewrite the section-header bold sentinels emitted by _rule()
    # into real <b>...</b> tags. has to happen after html escaping so the
    # tags survive into the output.
    return linked.replace(_B_OPEN, "<b>").replace(_B_CLOSE, "</b>")


# -- top-level --------------------------------------------------------------


def _header_block(now: datetime) -> str:
    # the only thing the page-top still needs is the freshness indicator.
    # the 'reveille' brand line and 'generated' prefix are both redundant
    # given the page url, the page title, and the timestamp's position at
    # the top of the page. the bare timestamp earns its keep because the
    # page is a morning briefing; how stale the data is matters.
    return now.strftime("%Y-%m-%d %H:%M %Z")


def render_page(
    sections: dict[str, Any],
    summary: dict,
    now: datetime,
    commit_sha: str | None = None,  # kept for build.py compatibility; unused
) -> str:
    # assemble all sections as plaintext, then linkify -- turn any bare
    # http(s) urls into <a href> anchors. the entire body sits inside one
    # <pre> so the browser uses its default monospace font and the user
    # picks their own colors / size via their browser config.
    text = "\n".join(
        [
            _header_block(now),
            _render_summary(summary),
            _render_nws_alerts(sections["nws_alerts"]),
            _render_nws_forecast(sections["nws_forecast"]),
            _render_ercot(sections["ercot"]),
            _render_ghostmaps(sections["ghostmaps"]),
            _render_rss_section("HV EMERGENCY ALERTS", sections["hv_rss"], "emergency"),
            _render_rss_section("HV POLICE", sections["hv_rss"], "police"),
            _render_rss_section("HV FIRE DEPT", sections["hv_rss"], "fire"),
        ]
    )

    body = _linkify(text)

    # the only css on the page: make the <pre> wrap on narrow viewports so
    # mobile users don't have to scroll horizontally. pre-wrap preserves
    # the existing newlines and indentation; overflow-wrap lets long
    # unbreakable tokens (urls, the '====' underlines, iso timestamps)
    # break at any character rather than push the viewport wider.
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex\">\n"
        f"<title>reveille -- {h(CITY)} -- {now.strftime('%Y-%m-%d')}</title>\n"
        "<style>pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>\n"
        "</head><body>\n"
        f"<pre>{body}</pre>\n"
        "</body></html>\n"
    )
