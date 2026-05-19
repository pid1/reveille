"""orchestrator: run all fetchers, generate ai summary, render the page.

never exits non-zero on data-source failure. only fails if config import
fails (i.e., required env vars unset) or if writing dist/index.html fails.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# import config first so missing env vars fail loudly before any fetching
from config import TIMEZONE  # noqa: F401  (triggers env validation)

from fetchers import ercot, ghostmaps, hv_rss, nws_alerts, nws_forecast
from fetchers.base import safe, unavailable

DIST = Path(__file__).parent / "dist"


def _gather() -> dict:
    print("[build] fetching nws_alerts ...", flush=True)
    sections = {}
    sections["nws_alerts"] = safe(nws_alerts.fetch)

    print("[build] fetching nws_forecast ...", flush=True)
    sections["nws_forecast"] = safe(nws_forecast.fetch)

    print("[build] fetching ercot ...", flush=True)
    sections["ercot"] = safe(ercot.fetch)

    print("[build] fetching hv_rss ...", flush=True)
    sections["hv_rss"] = safe(hv_rss.fetch)

    print("[build] fetching ghostmaps ...", flush=True)
    sections["ghostmaps"] = safe(ghostmaps.fetch)

    for k, v in sections.items():
        print(f"[build]   {k}: {v['status']}" + (f" ({v['error']})" if v["error"] else ""), flush=True)
    return sections


def _generate_summary(sections: dict) -> dict:
    """try the ai summary, return an envelope. failure is fine."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return unavailable("ANTHROPIC_API_KEY not set")
    try:
        # import here so a missing prompt file doesn't kill the whole build
        from summarize import format_blob, generate_summary  # local import on purpose

        blob = format_blob(sections)
        print("[build] generating ai summary ...", flush=True)
        text = generate_summary(blob)
        from fetchers.base import ok
        return ok(text.strip())
    except Exception as e:
        print(f"[build] ai summary failed: {type(e).__name__}: {e}", flush=True)
        return unavailable(f"{type(e).__name__}: {e}")


def main() -> int:
    # import render after config has validated env
    from render import render_page

    now = datetime.now(TIMEZONE)
    sections = _gather()
    summary = _generate_summary(sections)

    commit_sha = (
        os.environ.get("GITHUB_SHA")
        or os.environ.get("GIT_COMMIT")
        or None
    )
    if commit_sha:
        commit_sha = commit_sha[:7]

    html = render_page(sections, summary, now, commit_sha=commit_sha)

    DIST.mkdir(parents=True, exist_ok=True)
    out_path = DIST / "index.html"
    out_path.write_text(html, encoding="utf-8")
    size = out_path.stat().st_size
    print(f"[build] wrote {out_path} ({size} bytes)", flush=True)
    if size > 50_000:
        print(f"[build] WARN: page is {size} bytes, above the 20KB target", flush=True)

    # opportunistic pushover notification. runs after the page is written
    # so any pushover failure cannot affect publishing. the notifier itself
    # never raises -- it logs and returns. only fires when the BLUF is
    # present and not the NSTR sentinel.
    try:
        from notifier import send_pushover  # local import; module is optional
        send_pushover(summary, now)
    except Exception as e:
        print(f"[build] pushover step crashed unexpectedly: {type(e).__name__}: {e}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
