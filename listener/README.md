# reveille listener

an nwws-oi client for the unraid nas. it holds the nws weather wire connection, and pushes a
warning to pushover within seconds when it covers the house.

## what pushes

| warning | over the house | crosses the home grid square only |
| --- | --- | --- |
| tornado, extreme wind; any tornado or flash flood emergency | emergency (repeats until acknowledged) | high |
| severe thunderstorm, flash flood | high | normal |
| other warnings, tornado and severe thunderstorm watches | normal | not pushed |
| other watches, advisories | quiet, only if `PUSH_MIN_PRIORITY=-1` | not pushed |
| special weather statement with a polygon over the house | normal | not pushed |

"over the house" means the warning's polygon contains the home point, or, for products without a
polygon (watches, advisories), that its county or zone codes include the home's. the grid square
is the 6-character maidenhead square of the home point (about 8 by 5 km here).

- new, expanded and upgraded events push. updates stay silent unless they add emergency or
  "particularly dangerous situation" wording, or move a warning from nearby onto the house.
- an event that was missed while the listener was down pushes once as "in effect".
- when a pushed event ends, or its polygon moves off the house, one quiet all-clear follows.
- test and experimental products never push.

state is a json file in `/data`, so a restart does not push anything twice.

## city feeds relay

highland village's website does not answer cloudflare's network, so neither page could fetch
the city's rss feeds. the container also fetches them every 10 minutes from the house and posts
them to `https://full.reveille.pid1.space/city` with `REVEILLE_SEEN_TOKEN`. both pages read them
back from `/city.json`. a feed the nas could not fetch is sent as an error, and a relay that stops
is noticed from its timestamp (30 minutes on the full page, 3 hours on the daily page), so either
shows in the pages' `failed:` line as `nas relay` or `nas relay stale`.

## the one-connection rule

an nwws-oi account allows one connection. a second one, from a laptop for instance, can lock
the account out, and nws asks for a 30-minute wait before retrying a refused login. so:

- never run `python -m nwws_listener` with the real credentials anywhere but the nas.
- to test the rules, replay recorded products instead. the image ships with the fixtures:

```bash
docker exec reveille-listener python -m nwws_listener replay /app/fixtures/TORFWD-0.txt
```

- `python -m nwws_listener selftest` sends one quiet test push.

## configuration

everything comes from `/mnt/user/appdata/reveille-listener/.env` (mode 600, owned by uid 10001):

```text
NWWS_USERNAME=          # from the nwws-oi request
NWWS_PASSWORD=
HOME_LAT=               # the house. private
HOME_LON=
PUSHOVER_API_KEY=
PUSHOVER_USER_KEY=
REVEILLE_SEEN_URL=https://full.reveille.pid1.space/seen
REVEILLE_SEEN_TOKEN=    # same value as the worker's SEEN_TOKEN
HEALTHCHECK_URL=        # optional, pinged each minute while products flow
# HOME_UGC=TXC121,TXZ103  optional; looked up from api.weather.gov at start
# PUSH_MIN_PRIORITY=0     -1 adds quiet pushes for advisories
```

## deploy

`.github/workflows/listener.yml` tests every pull request and publishes
`ghcr.io/pid1/reveille-listener:latest` on every push to main. on the nas the container is created
from `unraid/my-reveille-listener.xml` (copied to `/boot/config/plugins/dockerMan/templates-user/`),
read-only, no capabilities, not root. update = pull and recreate, as with rally.

## check

```bash
cd listener && uv sync --locked && uv run python -m unittest discover -s tests
docker logs --tail 50 reveille-listener      # on the nas
```

a healthy log says `joined nwws@conference.nwws-oi.weather.gov` and then nothing until a push.
if no product arrives for 5 minutes it reconnects by itself.
