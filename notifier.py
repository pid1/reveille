"""pushover notifier. sends the BLUF text as a daily push notification.

design constraints:

- runs after the static page is written. any failure here logs and returns;
  it never raises and never affects the page build's exit status. pushover
  being down or misconfigured must not prevent the page from being published.
- sends the BLUF when it has real content, and is skipped when it is exactly
  the 'NSTR.' sentinel (case- and whitespace-insensitive): on quiet days you
  get no notification.
- when the ai summary failed or came back empty, it sends FALLBACK_MESSAGE
  instead. silence on those mornings would read as a quiet day, which it
  is not -- nobody read the data (win's call, 2026-11-06).
- message body is the raw BLUF paragraph, no decorations, no HTML, no wrap.
  pushover's monospace mode renders it in a fixed-width font on the client,
  and the client manages its own wrap to the device viewport.
- the live page URL is attached as a supplementary URL (pushover's `url`
  parameter), giving the user a tap target to the full briefing without
  consuming message-body characters.
"""

from __future__ import annotations

import os
from datetime import datetime

from fetchers.base import post_form

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

# pushover limits, per their api docs:
#   message: 1024 utf-8 characters
#   title: 250
# we cap defensively below those.
_MAX_MESSAGE_CHARS = 1024
_MAX_TITLE_CHARS = 250

# where the briefing is published. overridable with REVEILLE_PAGE_URL.
PAGE_URL = "https://reveille.pid1.space/"

# sent in place of the BLUF when the summary step failed.
FALLBACK_MESSAGE = "briefing up, summary unavailable."


def _page_url() -> str | None:
    """resolve the live page url. preference order:
    1. REVEILLE_PAGE_URL env var (explicit override).
    2. PAGE_URL, where the page actually lives.

    this used to derive the url from GITHUB_REPOSITORY, on the assumption
    that the page was a github pages project site. it is not any more, and
    that derivation would also have produced nothing at all when the build
    runs on cloudflare, where GITHUB_REPOSITORY is unset -- dropping the
    tap target from the push without saying so.
    """
    explicit = os.environ.get("REVEILLE_PAGE_URL", "").strip()
    return explicit or PAGE_URL


def _message(summary_envelope: dict | None) -> tuple[str | None, str]:
    """return (message, reason). message None means skip; reason is for the log."""
    text = ""
    if summary_envelope and summary_envelope.get("status") == "ok":
        text = (summary_envelope.get("data") or "").strip()
    if not text:
        return FALLBACK_MESSAGE, "summary unavailable, sending fallback"
    # NSTR sentinel match. claude may emit it with or without a trailing
    # period; either way is a 'nothing to report' day and we suppress.
    bare = text.rstrip(".").strip().upper()
    if bare == "NSTR":
        return None, "summary is NSTR (quiet day)"
    return text, ""


def send_pushover(summary_envelope: dict | None, now: datetime) -> None:
    """send a pushover push with the BLUF text. never raises.

    skipped silently if PUSHOVER_API_KEY or PUSHOVER_USER_KEY are not set,
    or on a quiet (NSTR) day (see _message).
    """
    token = os.environ.get("PUSHOVER_API_KEY", "").strip()
    user_key = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    if not token or not user_key:
        print(
            "[pushover] skipped: PUSHOVER_API_KEY or PUSHOVER_USER_KEY not set",
            flush=True,
        )
        return

    text, reason = _message(summary_envelope)
    if text is None:
        print(f"[pushover] skipped: {reason}", flush=True)
        return
    if reason:
        print(f"[pushover] {reason}", flush=True)

    # defensive truncation. the BLUF is prompt-capped at ~80 words (~500
    # chars), so this almost never fires, but if claude returns a wall of
    # text we still respect pushover's 1024-char cap rather than getting
    # rejected with a 4xx.
    if len(text) > _MAX_MESSAGE_CHARS:
        # leave room for the ellipsis token. cut on a word boundary if one
        # is reasonably close to the cap.
        cut = _MAX_MESSAGE_CHARS - 4
        space = text.rfind(" ", 0, cut)
        if space > cut - 100:
            cut = space
        text = text[:cut].rstrip() + " ..."

    title = f"reveille {now.strftime('%Y-%m-%d')}"
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS]

    payload: dict = {
        "token": token,
        "user": user_key,
        "title": title,
        "message": text,
        # monospace renders the message in a fixed-width font in the
        # pushover client, matching the rest of the page's aesthetic. it
        # also means we don't have to strip the BLUF for html escaping.
        "monospace": "1",
    }
    page_url = _page_url()
    if page_url:
        payload["url"] = page_url
        payload["url_title"] = "full briefing"

    try:
        resp = post_form(PUSHOVER_URL, payload, timeout=15.0)
        if isinstance(resp, dict) and resp.get("status") == 1:
            print("[pushover] sent ok", flush=True)
        else:
            # pushover returned 200 but signalled a logical error
            errors = resp.get("errors") if isinstance(resp, dict) else None
            print(f"[pushover] api rejected: {errors or resp}", flush=True)
    except Exception as e:
        # network failure, timeout, http 4xx/5xx, json decode -- all swallowed.
        # we log so a human investigating can find out, but the page build is
        # already complete by the time we get here, so this is informational.
        print(f"[pushover] send failed: {type(e).__name__}: {e}", flush=True)
