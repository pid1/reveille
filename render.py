"""pure-python rendering. one html5 file, ~30 lines of inline css, no js.

every _render_* helper takes the relevant envelope and returns a string.
unavailable envelopes still produce a section -- they don't get dropped.
"""

from __future__ import annotations

from datetime import datetime
from html import escape as h
from typing import Any

from config import CITY, STATE

# ascii note: page is monospace. one column. max ~80ch.

CSS = """
body { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
       max-width: 80ch; margin: 1.5rem auto; padding: 0 1rem;
       background: #f8f8f5; color: #111; line-height: 1.45;
       font-size: 14px; }
h1 { font-size: 1.05rem; margin: 0 0 .25rem 0; }
h2 { font-size: 1rem; margin: 1.4rem 0 .35rem 0; border-bottom: 1px solid #999;
     padding-bottom: .15rem; }
.meta { color: #555; font-size: .85rem; }
.unavail { color: #a00; }
.note { color: #555; font-size: .85rem; }
ul { margin: .25rem 0 .5rem 1.2rem; padding: 0; }
li { margin: .15rem 0; }
pre { white-space: pre-wrap; margin: .25rem 0 .5rem 0; }
.summary { background: #efece2; padding: .6rem .8rem; border: 1px solid #ddd; }
.summary pre { margin: 0; }
.footer { color: #555; font-size: .8rem; margin-top: 2rem;
          border-top: 1px solid #999; padding-top: .4rem; }
""".strip()


# -- helpers ----------------------------------------------------------------


def _section_head(title: str, env: dict | None) -> str:
    if env is None:
        return f"<h2>{h(title)}</h2>"
    ts = env.get("fetched_at") or ""
    return f"<h2>{h(title)} <span class='meta'>[fetched {h(ts)}]</span></h2>"


def _unavail_block(env: dict) -> str:
    return (
        f"<div class='unavail'>[unavailable: {h(env.get('error') or 'unknown')}]</div>"
    )


# -- head / header / footer ------------------------------------------------


def _render_head(title: str) -> str:
    return (
        "<!doctype html>\n"
        "<html lang='en'><head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<meta name='robots' content='noindex'>\n"
        f"<title>{h(title)}</title>\n"
        f"<style>{CSS}</style>\n"
        "</head><body>"
    )


def _render_header(now: datetime, freshness: str) -> str:
    date_str = now.strftime("%a %b %d, %Y")
    time_str = now.strftime("%H:%M %Z")
    return (
        f"<h1>reveille -- {h(CITY)}, {h(STATE)} daily -- {h(date_str)}</h1>"
        f"<div class='meta'>built: {h(time_str)} | {h(freshness)}</div>"
    )


def _render_footer(commit_sha: str | None) -> str:
    sha = commit_sha or "local"
    return (
        "<div class='footer'>"
        "sources: nws.weather.gov | ercot.com / gridstatus.io | "
        "highlandvillage.org | github.com/s2underground/ghostmaps"
        f"<br>build: {h(sha)}"
        "</div></body></html>"
    )


# -- ai summary -------------------------------------------------------------


def _render_summary(env: dict) -> str:
    parts = ["<h2>ai summary</h2>"]
    if env.get("status") == "ok" and env.get("data"):
        parts.append(f"<div class='summary'><pre>{h(env['data'])}</pre></div>")
    else:
        reason = (env or {}).get("error") or "not generated"
        parts.append(
            "<div class='unavail'>[ai summary unavailable -- see raw data below]</div>"
            f"<div class='note'>reason: {h(reason)}</div>"
        )
    return "\n".join(parts)


# -- nws --------------------------------------------------------------------


def _render_nws_alerts(env: dict) -> str:
    out = [_section_head("nws active alerts", env)]
    if env["status"] != "ok":
        out.append(_unavail_block(env))
        return "\n".join(out)
    alerts = env["data"] or []
    if not alerts:
        out.append("<div>no active alerts.</div>")
        return "\n".join(out)
    out.append("<ul>")
    for a in alerts:
        out.append(
            "<li>"
            f"<strong>{h(a['event'])}</strong> "
            f"({h(a['severity'])} / {h(a['urgency'])})<br>"
            f"{h(a['headline'])}<br>"
            f"<span class='meta'>expires {h(str(a.get('expires','')))}</span>"
            f"<div class='note'>{h(a.get('description',''))}</div>"
            "</li>"
        )
    out.append("</ul>")
    return "\n".join(out)


def _render_nws_forecast(env: dict) -> str:
    out = [_section_head("nws forecast (next ~3 days)", env)]
    if env["status"] != "ok":
        out.append(_unavail_block(env))
        return "\n".join(out)
    periods = env["data"] or []
    if not periods:
        out.append("<div>no forecast periods returned.</div>")
        return "\n".join(out)
    lines = []
    for p in periods:
        precip = (
            f" {p['precipPct']}% precip" if p.get("precipPct") is not None else ""
        )
        lines.append(
            f"{p['name']:<14} {p['temperature']}°{p['temperatureUnit']:<2} "
            f"wind {p['windSpeed']} {p['windDirection']}{precip}  "
            f"{p['shortForecast']}"
        )
    out.append(f"<pre>{h(chr(10).join(lines))}</pre>")
    return "\n".join(out)


# -- ercot ------------------------------------------------------------------


def _render_ercot(env: dict) -> str:
    out = [_section_head("ercot grid status", env)]
    if env["status"] != "ok":
        out.append(_unavail_block(env))
        return "\n".join(out)
    d = env["data"] or {}
    lines = []
    if d.get("alert_level"):
        lines.append(f"alert level     {d['alert_level']}")
    if d.get("frequency_hz") is not None:
        lines.append(f"frequency       {d['frequency_hz']} hz")
    if d.get("load_mw") is not None:
        lines.append(f"current load    {d['load_mw']:,} MW")
    if d.get("capacity_mw") is not None:
        lines.append(f"total capacity  {d['capacity_mw']:,} MW")
    if d.get("reserve_margin_mw") is not None:
        lines.append(f"reserve margin  {d['reserve_margin_mw']:,} MW")
    if d.get("renewable_share_pct") is not None:
        lines.append(f"renewable share {d['renewable_share_pct']}%")
    if not lines:
        lines.append("(no fields returned)")
    out.append(f"<pre>{h(chr(10).join(lines))}</pre>")
    out.append(
        f"<div class='note'>source: {h(str(d.get('source','?')))}</div>"
    )
    if d.get("gridstatus_error"):
        out.append(
            "<div class='note'>note: gridstatus.io failed, fell back to ercot html "
            f"({h(d['gridstatus_error'])})</div>"
        )
    return "\n".join(out)


# -- ghostmaps --------------------------------------------------------------


def _render_ghostmaps(env: dict) -> str:
    out = [_section_head("ghostmaps incidents within radius", env)]
    if env["status"] != "ok":
        out.append(_unavail_block(env))
        return "\n".join(out)
    d = env["data"] or {}
    nearby = d.get("nearby") or []
    if not nearby:
        out.append("<div>no incidents within configured radius.</div>")
    else:
        out.append("<ul>")
        for n in nearby:
            folder = f" [{h(n['folder'])}]" if n.get("folder") else ""
            desc = (
                f"<div class='note'>{h(n.get('description',''))}</div>"
                if n.get("description")
                else ""
            )
            out.append(
                f"<li><strong>{n['distance_mi']}mi {h(n['bearing'])}</strong> -- "
                f"{h(n['name'])}{folder}{desc}</li>"
            )
        out.append("</ul>")
    out.append(
        "<div class='note'>"
        f"source: {h(str(d.get('source_file','?')))} "
        f"(selected via {h(str(d.get('source_strategy','?')))}), "
        f"{d.get('total_placemarks','?')} total placemarks, "
        f"{d.get('skipped_geometry','?')} skipped"
        "</div>"
    )
    return "\n".join(out)


# -- rss --------------------------------------------------------------------


def _render_rss_section(title: str, env: dict, key: str) -> str:
    out = [_section_head(title, env)]
    if env["status"] != "ok":
        out.append(_unavail_block(env))
        return "\n".join(out)
    items = (env["data"] or {}).get(key) or []
    if not items:
        out.append("<div>no entries in last 14 days.</div>")
        return "\n".join(out)
    out.append("<ul>")
    for e in items:
        pub = e.get("published") or ""
        title_s = e.get("title") or ""
        link = e.get("link") or ""
        summary = e.get("summary") or ""
        title_html = (
            f"<a href='{h(link)}'>{h(title_s)}</a>" if link else h(title_s)
        )
        out.append(
            f"<li><span class='meta'>{h(pub)}</span> {title_html}"
            + (f"<div class='note'>{h(summary)}</div>" if summary else "")
            + "</li>"
        )
    out.append("</ul>")
    return "\n".join(out)


# -- top-level --------------------------------------------------------------


def _freshness(sections: dict) -> str:
    total = len(sections)
    ok_count = sum(1 for v in sections.values() if v.get("status") == "ok")
    return f"sections ok {ok_count}/{total}"


def render_page(
    sections: dict[str, Any],
    summary: dict,
    now: datetime,
    commit_sha: str | None = None,
) -> str:
    parts = [
        _render_head(f"reveille -- {CITY} -- {now.strftime('%Y-%m-%d')}"),
        _render_header(now, _freshness(sections)),
        _render_summary(summary),
        _render_nws_alerts(sections["nws_alerts"]),
        _render_nws_forecast(sections["nws_forecast"]),
        _render_ercot(sections["ercot"]),
        _render_ghostmaps(sections["ghostmaps"]),
        _render_rss_section("hv emergency alerts (last 14d)", sections["hv_rss"], "emergency"),
        _render_rss_section("hv police news (last 14d)", sections["hv_rss"], "police"),
        _render_rss_section("hv fire news (last 14d)", sections["hv_rss"], "fire"),
        _render_footer(commit_sha),
    ]
    return "\n".join(parts)
