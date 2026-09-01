"""pushover notifier. sends the BLUF text as a daily push notification.

design constraints:

- runs after the static page is written. any failure here logs and returns;
  it never raises and never affects the page build's exit status. pushover
  being down or misconfigured must not prevent the page from being published.
- only fires when the BLUF has real content. specifically, skipped when the
  ai summary path failed, when the summary text is empty, or when it is
  exactly the 'NSTR.' sentinel (case- and whitespace-insensitive). on quiet
  days you get no notification. github actions surfaces build failures via
  its own channels, so silence here is not ambiguous.
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


def _page_url() -> str | None:
    """resolve the live page url. preference order:
    1. REVEILLE_PAGE_URL env var (explicit override).
    2. derived from GITHUB_REPOSITORY in github actions (owner/repo ->
       https://owner.github.io/repo/, which matches the README's stated
       hosting location).
    3. None -- the notifier still works, the supplementary url just gets
       omitted from the push.
    """
    explicit = os.environ.get("REVEILLE_PAGE_URL", "").strip()
    if explicit:
        return explicit
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if owner and name:
            return f"https://{owner}.github.io/{name}/"
    return None


def _should_send(summary_envelope: dict | None) -> tuple[bool, str]:
    """return (should_send, reason). reason is for logging when we skip."""
    if not summary_envelope:
        return False, "no summary envelope"
    if summary_envelope.get("status") != "ok":
        return False, f"summary status={summary_envelope.get('status')}"
    text = (summary_envelope.get("data") or "").strip()
    if not text:
        return False, "summary text is empty"
    # NSTR sentinel match. claude may emit it with or without a trailing
    # period; either way is a 'nothing to report' day and we suppress.
    bare = text.rstrip(".").strip().upper()
    if bare == "NSTR":
        return False, "summary is NSTR (quiet day)"
    return True, ""


def send_pushover(summary_envelope: dict | None, now: datetime) -> None:
    """send a pushover push with the BLUF text. never raises.

    skipped silently if PUSHOVER_API_KEY or PUSHOVER_USER_KEY are not set,
    or if the summary doesn't warrant a push (see _should_send).
    """
    token = os.environ.get("PUSHOVER_API_KEY", "").strip()
    user_key = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    if not token or not user_key:
        print(
            "[pushover] skipped: PUSHOVER_API_KEY or PUSHOVER_USER_KEY not set",
            flush=True,
        )
        return

    should, reason = _should_send(summary_envelope)
    if not should:
        print(f"[pushover] skipped: {reason}", flush=True)
        return

    text = (summary_envelope["data"] or "").strip()
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
