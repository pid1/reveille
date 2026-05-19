"""shared fetcher utilities: envelopes, safe wrapper, http helpers, html stripping.

every fetcher returns the envelope shape produced by ok()/unavailable().
every http call goes through get_json / get_bytes / post_json -- not raw
urllib -- so retries, headers, and error normalization stay in one place.
"""

from __future__ import annotations

import html as html_module
import json
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Callable

from config import TIMEZONE

DEFAULT_TIMEOUT = 30.0
USER_AGENT = "reveille (github.com/pid1/reveille)"


# -- envelopes --------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def ok(data: Any) -> dict:
    return {
        "status": "ok",
        "data": data,
        "error": None,
        "fetched_at": _now_iso(),
    }


def unavailable(error: str) -> dict:
    return {
        "status": "unavailable",
        "data": None,
        "error": error,
        "fetched_at": _now_iso(),
    }


def safe(fn: Callable[[], Any]) -> dict:
    """run fn() with full exception catch. always returns an envelope."""
    try:
        result = fn()
        if isinstance(result, dict) and result.get("status") in {"ok", "unavailable"}:
            # fetcher already produced an envelope; pass it through
            return result
        return ok(result)
    except Exception as e:
        return unavailable(f"{type(e).__name__}: {e}")


# -- http helpers -----------------------------------------------------------


def _request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """raw http request; returns response body bytes. raises on http >=400 or network failure."""
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        try:
            body_excerpt = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body_excerpt = ""
        raise RuntimeError(f"HTTP {e.code} from {url}: {body_excerpt}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error fetching {url}: {e.reason}") from e


def get_json(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    raw = _request("GET", url, headers=headers, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def get_bytes(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    return _request("GET", url, headers=headers, timeout=timeout)


def get_text(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    raw = _request("GET", url, headers=headers, timeout=timeout)
    return raw.decode("utf-8", errors="replace")


def post_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    h = {"content-type": "application/json"}
    if headers:
        h.update(headers)
    raw = _request(
        "POST",
        url,
        headers=h,
        body=json.dumps(payload).encode("utf-8"),
        timeout=timeout,
    )
    return json.loads(raw.decode("utf-8"))


def post_form(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """POST `payload` as application/x-www-form-urlencoded. parses the response
    as json. used for apis like pushover that don't accept json bodies.
    """
    import urllib.parse
    h = {"content-type": "application/x-www-form-urlencoded"}
    if headers:
        h.update(headers)
    # urlencode keeps non-ascii utf-8 by encoding via quote_plus
    body = urllib.parse.urlencode(
        {k: v for k, v in payload.items() if v is not None}
    ).encode("utf-8")
    raw = _request("POST", url, headers=h, body=body, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


# -- html stripping ---------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data):
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_html(s: str) -> str:
    """strip html tags, decode entities, normalize whitespace."""
    if not s:
        return ""
    p = _TextExtractor()
    try:
        p.feed(s)
    except Exception:
        return s
    return " ".join(html_module.unescape(p.get_text()).split())


_HREF_RE = None  # lazy compile


def extract_hrefs(s: str) -> list[str]:
    """pull all href values out of any <a> tags in s, in order, deduplicated.

    accepts single or double quotes, decodes html entities in the url.
    """
    import re
    global _HREF_RE
    if _HREF_RE is None:
        _HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(['"])(.*?)\1""", re.IGNORECASE | re.DOTALL)
    if not s:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _HREF_RE.finditer(s):
        url = html_module.unescape(m.group(2)).strip()
        if not url or url in seen:
            continue
        if url.startswith(("http://", "https://")):
            seen.add(url)
            out.append(url)
    return out


def truncate(s: str, limit: int) -> str:
    """truncate to `limit` chars on a word boundary if possible, append ellipsis."""
    if not s or len(s) <= limit:
        return s or ""
    cut = s[: limit - 1]
    sp = cut.rfind(" ")
    if sp > limit // 2:
        cut = cut[:sp]
    return cut.rstrip() + "..."
