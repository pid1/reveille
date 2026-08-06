"""ai morning-summary generation. claude sonnet 5 via direct anthropic api call.

prompt lives in prompts/summary_system.md and is read at import time. the
formatted data blob lives here, not in the prompt, because it depends on
runtime fetcher state.
"""

from __future__ import annotations

import os
from pathlib import Path

from fetchers.base import post_json

PROMPT_PATH = Path(__file__).parent / "prompts" / "summary_system.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()
if not SYSTEM_PROMPT:
    raise RuntimeError(f"empty or missing system prompt at {PROMPT_PATH}")

ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-5"


# -- data blob formatting --------------------------------------------------


def _fmt_nws_alerts(env: dict) -> str:
    if env["status"] != "ok":
        return f"unavailable ({env['error']})"
    alerts = env["data"] or []
    if not alerts:
        return "none active"
    parts = []
    for a in alerts:
        parts.append(
            f"- {a['event']} / {a['severity']} / {a['urgency']}: {a['headline']} "
            f"(expires {a.get('expires','')})"
        )
    return "\n" + "\n".join(parts)


def _fmt_forecast(env: dict) -> str:
    if env["status"] != "ok":
        return f"unavailable ({env['error']})"
    periods = env["data"] or []
    if not periods:
        return "no periods returned"
    lines = []
    for p in periods:
        precip = f", {p['precipPct']}% precip" if p.get("precipPct") is not None else ""
        lines.append(
            f"- {p['name']}: {p['temperature']}{p['temperatureUnit']}, "
            f"wind {p['windSpeed']} {p['windDirection']}{precip}, "
            f"{p['shortForecast']}"
        )
    return "\n" + "\n".join(lines)


def _fmt_ercot(env: dict) -> str:
    if env["status"] != "ok":
        return f"unavailable ({env['error']})"
    d = env["data"] or {}
    bits = []
    if d.get("alert_level"):
        bits.append(f"alert={d['alert_level']}")
    if d.get("load_mw") is not None:
        bits.append(f"load={d['load_mw']}MW")
    if d.get("capacity_mw") is not None:
        bits.append(f"cap={d['capacity_mw']}MW")
    if d.get("reserve_margin_mw") is not None:
        bits.append(f"margin={d['reserve_margin_mw']}MW")
    if d.get("renewable_share_pct") is not None:
        bits.append(f"renewable={d['renewable_share_pct']}%")
    return ", ".join(bits) if bits else "no data"


def _fmt_rss(env: dict, key: str) -> str:
    if env["status"] != "ok":
        return f"unavailable ({env['error']})"
    items = (env["data"] or {}).get(key) or []
    if not items:
        return "none in last 14d"
    lines = []
    for e in items[:8]:
        lines.append(f"- {e.get('published','?')} :: {e.get('title','')} :: {e.get('summary','')}")
    return "\n" + "\n".join(lines)


def _fmt_ghostmaps(env: dict) -> str:
    if env["status"] != "ok":
        return f"unavailable ({env['error']})"
    d = env["data"] or {}
    nearby = d.get("nearby") or []
    if not nearby:
        return f"none within 25mi (source: {d.get('source_file','?')})"
    lines = []
    for n in nearby[:20]:
        folder = f" [{n['folder']}]" if n.get("folder") else ""
        desc = n.get("description") or ""
        lines.append(
            f"- {n['distance_mi']}mi {n['bearing']} -- {n['name']}{folder}"
            + (f" -- {desc}" if desc else "")
        )
    return "\n" + "\n".join(lines)


def format_blob(sections: dict) -> str:
    return (
        f"NWS_ALERTS: {_fmt_nws_alerts(sections['nws_alerts'])}\n"
        f"NWS_FORECAST: {_fmt_forecast(sections['nws_forecast'])}\n"
        f"ERCOT: {_fmt_ercot(sections['ercot'])}\n"
        f"HV_EMERGENCY: {_fmt_rss(sections['hv_rss'], 'emergency')}\n"
        f"HV_POLICE: {_fmt_rss(sections['hv_rss'], 'police')}\n"
        f"HV_FIRE: {_fmt_rss(sections['hv_rss'], 'fire')}\n"
        f"GHOSTMAPS_NEARBY: {_fmt_ghostmaps(sections['ghostmaps'])}\n"
    )


# -- api call --------------------------------------------------------------


def generate_summary(data_blob_text: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    resp = post_json(
        ANTHROPIC_API,
        payload={
            "model": MODEL,
            # the prompt targets <=80 words (~120 tokens). 200 gives headroom
            # for rare 2-paragraph days without permitting a wall of text.
            "max_tokens": 200,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": data_blob_text}],
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    return _extract_text(resp)


def _extract_text(resp: dict) -> str:
    """pull the assistant text out of a messages-api response.

    the response `content` is a list of typed blocks. we cannot assume the
    first block is text: claude-sonnet-5 runs adaptive thinking by default, so
    content[0] is often a `thinking` block (no "text" key). find the first
    text block instead of indexing blindly -- that's what produced the
    `KeyError: 'text'` in the BLUF.
    """
    content = resp.get("content") or []
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    ]
    if not parts:
        stop = resp.get("stop_reason")
        raise RuntimeError(
            f"no text block in response (stop_reason={stop!r}, "
            f"block types={[b.get('type') for b in content if isinstance(b, dict)]})"
        )
    return "\n".join(parts)
