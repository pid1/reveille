"""reveille nwws-oi listener.

    python -m nwws_listener              connect and listen (the container's job)
    python -m nwws_listener replay F...  run product files through the rules, no push
    python -m nwws_listener selftest     send one test push and exit

configuration is environment only. see listener/README.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import geo
from .decide import NORMAL, Home, Push, decide, prune
from .io import load_state, ping, report_seen, resolve_ugc, save_state, send_push
from .product import parse_product

log = logging.getLogger("nwws")

HOST = "nwws-oi.weather.gov"
ROOM = "nwws@conference.nwws-oi.weather.gov"
STALL_SECONDS = 300  # nwws carries a product every few seconds; 5 quiet minutes is a stall


class Config:
    def __init__(self, env=os.environ):
        def need(k):
            v = env.get(k, "").strip()
            if not v:
                sys.exit(f"required env var {k} is not set")
            return v

        self.lat = float(need("HOME_LAT"))
        self.lon = float(need("HOME_LON"))
        self.tz = ZoneInfo(env.get("TZ", "America/Chicago") or "America/Chicago")
        self.grid = env.get("HOME_GRID", "").strip() or geo.maidenhead(self.lat, self.lon)
        self.ugc = {c.strip().upper() for c in env.get("HOME_UGC", "").split(",") if c.strip()}
        self.user = env.get("NWWS_USERNAME", "").strip()
        self.password = env.get("NWWS_PASSWORD", "")
        self.resource = env.get("NWWS_RESOURCE", "reveille").strip() or "reveille"
        self.push_token = env.get("PUSHOVER_API_KEY", "").strip()
        self.push_user = env.get("PUSHOVER_USER_KEY", "").strip()
        self.min_priority = int(env.get("PUSH_MIN_PRIORITY", str(NORMAL)))
        self.healthcheck = env.get("HEALTHCHECK_URL", "").strip() or None
        self.seen_url = env.get("REVEILLE_SEEN_URL", "").strip() or None
        self.seen_token = env.get("REVEILLE_SEEN_TOKEN", "").strip() or None
        self.state_path = Path(env.get("STATE_DIR", "/data")) / "state.json"
        self.dry_run = env.get("DRY_RUN", "").lower() in ("1", "true", "yes")


class Engine:
    """everything between a product's text and a push."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = load_state(cfg.state_path)
        self.sps_seen: set[str] = set()
        self.replay = False  # replayed products are old; don't prune them away
        if not cfg.ugc:
            cfg.ugc = self._resolve_ugc()
        self.home = Home(cfg.lat, cfg.lon, cfg.ugc, cfg.grid)
        log.info("home: grid %s, ugc %s, min priority %s%s", cfg.grid, ",".join(sorted(cfg.ugc)),
                 cfg.min_priority, " (dry run)" if cfg.dry_run else "")

    def _resolve_ugc(self) -> set[str]:
        for attempt in range(5):
            try:
                codes = resolve_ugc(self.cfg.lat, self.cfg.lon)
                if codes:
                    return codes
            except Exception as e:
                log.warning("ugc lookup failed (%s), retrying", e)
            time.sleep(10 * (attempt + 1))
        sys.exit("could not resolve the home county and zone; set HOME_UGC")

    def handle(self, text: str) -> list[Push]:
        awips = text[:200]
        if "/O." not in text and "SPS" not in awips:
            return []
        try:
            product = parse_product(text)
            pushes = decide(product, self.home, self.state, self.cfg.tz, self.cfg.min_priority)
            pushes += self._sps(product)
        except Exception as e:
            log.exception("product failed to parse: %s", e)
            return []
        if pushes or product.segments:
            if not self.replay:
                prune(self.state)
            try:
                save_state(self.cfg.state_path, self.state)
            except OSError as e:
                log.error("state not saved: %s", e)
        for p in pushes:
            send_push(p, self.cfg.push_token, self.cfg.push_user, self.cfg.dry_run)
        alerts = [p.key for p in pushes if p.kind == "alert" and not p.key.startswith("SPS.")]
        report_seen(self.cfg.seen_url, self.cfg.seen_token, alerts)
        return pushes

    def _sps(self, product) -> list[Push]:
        """special weather statements carry no vtec. push one only when its
        polygon contains the house."""
        if not product.awips_id.startswith("SPS"):
            return []
        out = []
        for seg in product.segments:
            if seg.vtec or not seg.polygon:
                continue
            if not geo.contains(seg.polygon, (self.home.lat, self.home.lon)):
                continue
            key = "SPS." + hashlib.sha256(seg.text[:400].encode()).hexdigest()[:16]
            if key in self.sps_seen or NORMAL < self.cfg.min_priority:
                continue
            self.sps_seen.add(key)
            lead = ""
            for ln in seg.text.splitlines():
                if ln.strip().lower().startswith("at "):
                    lead = ln.strip()
                    break
            out.append(Push(key, "special weather statement", f"over the house. {lead}".strip(), NORMAL))
        return out


# -- xmpp -------------------------------------------------------------------


async def listen(cfg: Config, engine: Engine) -> None:
    import slixmpp  # imported here so replay and tests need no xmpp stack

    class Client(slixmpp.ClientXMPP):
        def __init__(self):
            super().__init__(f"{cfg.user}@{HOST}/{cfg.resource}", cfg.password)
            self.register_plugin("xep_0045")
            self.register_plugin("xep_0199", {"keepalive": True, "interval": 60, "timeout": 30})
            self.auth_failed = False
            self.products = 0
            self.last = time.monotonic()
            self.add_event_handler("session_start", self.start)
            self.add_event_handler("groupchat_message", self.message)
            self.add_event_handler("failed_auth", self.failed)

        async def start(self, _):
            self.send_presence()
            try:
                await self.plugin["xep_0045"].join_muc_wait(ROOM, f"{cfg.user}-{cfg.resource}", timeout=30)
                log.info("joined %s", ROOM)
            except Exception as e:
                log.error("could not join the room: %s", e)
                self.disconnect()

        def failed(self, _):
            log.error("login refused; check NWWS_USERNAME and NWWS_PASSWORD")
            self.auth_failed = True
            self.disconnect()

        def message(self, msg):
            x = msg.xml.find("{nwws-oi}x")
            if x is None or not x.text:
                return
            if not self.products:
                log.info("first product received: %s", x.get("awipsid") or "?")
            self.products += 1
            self.last = time.monotonic()
            engine.handle(x.text)

    delay = 30
    while True:
        client = Client()
        done = asyncio.Event()
        # only a real disconnect ends a session. connection_failed is not one:
        # slixmpp raises it for each address it can't reach (an ipv6 address
        # on a docker bridge with no ipv6, say) and then tries the next one
        # by itself. treating it as the end once started a second session
        # while the first was still connected, which is the one thing an
        # nwws-oi account must never do. a connect that never succeeds is
        # caught by the stall watchdog below instead.
        client.add_event_handler("disconnected", lambda *_: done.set())
        client.add_event_handler("connection_failed", lambda e: log.info("connect attempt failed (%s); trying the next address", e))
        log.info("connecting to %s", HOST)
        client.connect(HOST, 5222)

        async def watchdog():
            reported = 0
            ticks = 0
            while not done.is_set():
                await asyncio.sleep(60)
                ticks += 1
                if ticks % 10 == 0:
                    log.info("%d products in the last 10 minutes", client.products - reported)
                    reported = client.products
                quiet = time.monotonic() - client.last
                if quiet > STALL_SECONDS:
                    log.warning("no products for %ds, reconnecting", int(quiet))
                    client.abort()
                    done.set()
                elif client.products:
                    await asyncio.to_thread(ping, cfg.healthcheck)

        dog = asyncio.create_task(watchdog())
        await done.wait()
        dog.cancel()
        client.abort()
        if client.auth_failed:
            # nws asks clients not to hammer a refused login
            delay = 1800
        elif client.products:
            delay = 30
        else:
            delay = min(delay * 2, 1800)
        log.info("disconnected after %d products; retrying in %ds", client.products, delay)
        await asyncio.sleep(delay)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("slixmpp").setLevel(logging.WARNING)
    cmd = argv[1] if len(argv) > 1 else "listen"
    if cmd == "replay":
        os.environ["DRY_RUN"] = "1"
        os.environ.setdefault("STATE_DIR", "/tmp/nwws-replay")
        engine = Engine(Config())
        engine.replay = True
        for f in argv[2:]:
            for p in engine.handle(Path(f).read_text(errors="replace")):
                print(f"{f}: p={p.priority} {p.title} | {p.message}")
        return 0
    cfg = Config()
    if cmd == "selftest":
        ok = send_push(
            Push("selftest", "reveille listener", f"push path works ({datetime.now(UTC):%Y-%m-%d %H:%MZ}).", -1),
            cfg.push_token, cfg.push_user, cfg.dry_run,
        )
        return 0 if ok else 1
    if not cfg.user or not cfg.password:
        sys.exit("NWWS_USERNAME and NWWS_PASSWORD are required to listen")
    engine = Engine(cfg)
    asyncio.run(listen(cfg, engine))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
