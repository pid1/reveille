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


COL = 78  # rough target line width for plaintext sections


# -- helpers ----------------------------------------------------------------


def _rule(title: str) -> str:
    bar = "=" * max(len(title), 12)
    return f"\n{title}\n{bar}"


def _section_head(title: str, env: dict | None) -> str:
    if env is None:
        return _rule(title)
    ts = env.get("fetched_at") or ""
    return _rule(f"{title}  [fetched {ts}]")


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
    parts = [_rule("ai summary")]
    if env.get("status") == "ok" and env.get("data"):
        parts.append(env["data"])
    else:
        reason = (env or {}).get("error") or "not generated"
        parts.append("[ai summary unavailable -- see raw data below]")
        parts.append(f"reason: {reason}")
    return "\n".join(parts)


# -- nws --------------------------------------------------------------------


def _render_nws_alerts(env: dict) -> str:
    out = [_section_head("nws active alerts", env)]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    alerts = env["data"] or []
    if not alerts:
        out.append("no active alerts.")
        return "\n".join(out)
    for a in alerts:
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
    out = [_section_head("nws forecast (next ~3 days)", env)]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    periods = env["data"] or []
    if not periods:
        out.append("no forecast periods returned.")
        return "\n".join(out)
    for p in periods:
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
    out = [_section_head("ercot grid status", env)]
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
    out = [_section_head("ghostmaps incidents within radius", env)]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    d = env["data"] or {}
    nearby = d.get("nearby") or []
    if not nearby:
        out.append("no incidents within configured radius.")
    else:
        for n in nearby:
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
    out = [_section_head(title, env)]
    if env["status"] != "ok":
        out.append(_unavail_line(env))
        return "\n".join(out)
    items = (env["data"] or {}).get(key) or []
    if not items:
        out.append("no entries in last 14 days.")
        return "\n".join(out)
    for e in items:
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
    """html-escape `text`, then turn any bare http(s) urls into <a href> anchors.

    operates on the already-escaped text so the surrounding content is safe.
    only matches whitespace-terminated http/https urls.
    """
    import re
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
        return f'<a href="{url}">{url}</a>{trailing}'

    return pat.sub(repl, escaped)


# -- top-level --------------------------------------------------------------


def _freshness(sections: dict) -> str:
    total = len(sections)
    ok_count = sum(1 for v in sections.values() if v.get("status") == "ok")
    return f"sections ok {ok_count}/{total}"


def _header_block(now: datetime, freshness: str) -> str:
    date_str = now.strftime("%a %b %d, %Y")
    time_str = now.strftime("%H:%M %Z")
    title = f"reveille -- {CITY}, {STATE} daily -- {date_str}"
    bar = "=" * len(title)
    return f"{bar}\n{title}\n{bar}\nbuilt: {time_str} | {freshness}"


def _footer_block(commit_sha: str | None) -> str:
    sha = commit_sha or "local"
    return (
        "\n--\n"
        "sources: nws.weather.gov | ercot.com / gridstatus.io | "
        "highlandvillage.org | github.com/s2underground/GhostMaps\n"
        f"build: {sha}\n"
    )


def render_page(
    sections: dict[str, Any],
    summary: dict,
    now: datetime,
    commit_sha: str | None = None,
) -> str:
    # assemble all sections as plaintext, then linkify -- turn any bare
    # http(s) urls into <a href> anchors. the entire body sits inside one
    # <pre> so the browser uses its default monospace font and the user
    # picks their own colors / size via their browser config.
    text = "\n".join(
        [
            _header_block(now, _freshness(sections)),
            _render_summary(summary),
            _render_nws_alerts(sections["nws_alerts"]),
            _render_nws_forecast(sections["nws_forecast"]),
            _render_ercot(sections["ercot"]),
            _render_ghostmaps(sections["ghostmaps"]),
            _render_rss_section("hv emergency alerts (last 14d)", sections["hv_rss"], "emergency"),
            _render_rss_section("hv police news (last 14d)", sections["hv_rss"], "police"),
            _render_rss_section("hv fire news (last 14d)", sections["hv_rss"], "fire"),
            _footer_block(commit_sha),
        ]
    )

    body = _linkify(text)

    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex\">\n"
        f"<title>reveille -- {h(CITY)} -- {now.strftime('%Y-%m-%d')}</title>\n"
        "</head><body>\n"
        f"<pre>{body}</pre>\n"
        "</body></html>\n"
    )
