# reveille

a plaintext daily morning briefing for highland village, tx. one static html page, regenerated at 4am central by github actions, served from github pages.

## what's on it

- nws active alerts and 3-day forecast for highland village
- ercot grid status
- highland village emergency, police, and fire rss feeds (most recent 14 days)
- geolocated incidents from s2 underground's ghostmaps common intelligence picture, filtered to within 25 miles and the last 24 hours
- short ai-generated summary at the top (claude sonnet 5), with graceful fallback to raw data if the summary call fails

## why minimal data transfer matters

the page is under 20KB. no javascript. no external resources. no analytics, no fonts, no cdn css. that is the entire point -- not an aesthetic preference, a design requirement.

- **bandwidth degrades during the emergencies this thing is meant to cover.** congested cell towers, flaky home internet during storms, satellite fallback during outages -- that's exactly when you need this most, and exactly when modern websites stop working. a 15KB page loads on a 2G fallback. a 2MB react app does not.
- **survives hostile network conditions.** hotel wifi, airport wifi, tethering with carrier throttling, starlink during brownouts, hf packet links. every byte costs in those environments. a single small html file is the most efficient possible delivery format.
- **no third-party dependencies at render time.** no google fonts, no jquery cdn, no analytics. the page loads correctly even if half the internet is down. only point of failure is github pages itself.
- **cacheable forever, viewable offline.** save the page to your phone in the morning, refer to it later with no signal. the page is the page. no "loading..." spinners that never finish.
- **outlives the tech stack.** plaintext html from 1995 still renders. a single-page app from 2018 frequently doesn't. plaintext is forward-compatible by default.
- **handles infinite traffic at zero cost.** github pages plus a 15KB static file cached at the cdn edge will serve any volume you throw at it. nothing to scale, nothing to break.

the cost: it looks like the drudge report. no animations, no fancy typography, no dark mode toggle. that's a feature.

## stack

- python 3.14
- [uv](https://docs.astral.sh/uv/) for package management
- one third-party dependency: `feedparser`. all other http and parsing is stdlib (`urllib.request`, `xml.etree.ElementTree`, `html.parser`, `zipfile`).
- github actions for the daily build (triggered externally -- see [scheduling](#scheduling))
- github pages for hosting

the http layer is deliberately stdlib-only. `httpx` was the obvious choice for a python http client, but as of mid-2026 it's in a maintenance transition (upstream stopped accepting bug reports earlier this year, a rewrite is in progress) and we'd rather not adopt a known migration. `urllib.request` is verbose but it's not going anywhere. a small wrapper in `fetchers/base.py` keeps the call sites clean.

## local development

```bash
uv sync
# location env vars are required -- see below
uv run --env-file .env python build.py
open dist/index.html
```

create a `.env` file (gitignored, never commit it) with the location values:

```text
HV_LAT=...
HV_LON=...
HV_ZIP=...
HV_CITY=...
HV_STATE=...
HV_COUNTY=...
HV_TIMEZONE=...
ANTHROPIC_API_KEY=...      # optional, for the ai summary
GRIDSTATUS_API_KEY=...     # optional, for ercot data
PUSHOVER_API_KEY=...       # optional, app token from pushover.net
PUSHOVER_USER_KEY=...      # optional, user key from pushover.net dashboard
REVEILLE_PAGE_URL=...      # optional, overrides the derived GH Pages URL
```

if both pushover values are set, the build sends a push notification with
the BLUF text to your devices after writing the page. the push is skipped
on quiet days (when the BLUF is `NSTR.`) and any pushover failure is logged
but does not affect the page build.

ask the maintainer for the location values if you're working on a fork.

## editing the ai summary

the system prompt is in `prompts/summary_system.md`. edit the markdown file directly, commit, push. no python changes required.

## deployment

setup uses the [github cli](https://cli.github.com/) (`gh`). run from a clone of this repo:

1. create the repo (if forking from scratch, `gh repo create pid1/reveille --public`)
2. set all required `HV_*` secrets via `gh secret set` (location values are private -- get them from a trusted source)
3. set api key secrets via `gh secret set ANTHROPIC_API_KEY`, `gh secret set GRIDSTATUS_API_KEY`, and (optional) `gh secret set PUSHOVER_API_KEY` + `gh secret set PUSHOVER_USER_KEY` (all prompt interactively without echoing)
4. enable pages at `settings -> pages -> source -> "github actions"` (web ui only)
5. push to main, or trigger manually via `gh workflow run "build dashboard"` or the actions tab in the ui
6. page lives at `pid1.github.io/reveille/`
7. set up the external build trigger -- see [scheduling](#scheduling) below.
   github's cron alone will not get you a 4am briefing.

## scheduling

github's `schedule` event is best-effort. it has deferred this repo's cron
dispatches by four to eight hours at a stretch, which turns a morning
briefing into a mid-morning one. so the crons in `build.yml` are a backstop,
not the primary trigger.

the primary trigger is a cloudflare worker in `infra/cloudflare/` that calls
the `workflow_dispatch` api at 04:17 central. that event is dispatched
immediately rather than queued. it lives on cloudflare's edge rather than on
a box at the house on purpose -- a local trigger would just trade github's
unreliable clock for a power cut or an isp outage:

```bash
cd infra/cloudflare
npx wrangler login
npx wrangler secret put GITHUB_TOKEN   # fine-grained pat, `actions: read and write` only
npx wrangler deploy
```

daylight saving is handled in the worker rather than in config -- it
schedules both candidate utc times and dispatches on whichever one is 04:17
central.

`scripts/trigger-build.sh` does the same job from a shell if you would
rather run it from a machine you own, or need to fire one manually.

the measurements behind all of this, the backstop crons, the pat scoping,
and what to check if it starts happening again are in
[docs/scheduling.md](docs/scheduling.md).

## caveats

- **this is not a replacement for hyper-reach.** the city of highland village's real-time alerting system runs over phone/sms/alexa and is faster than rss for genuine emergencies. sign up at highlandvillage.org/notifyme. reveille is for morning situational awareness, not emergency notification.
- **ghostmaps is community-maintained data.** s2 underground aggregates open-source intelligence; coverage and freshness vary. empty result lists are normal.
- **ercot data depends on a third-party api.** if gridstatus.io is unreachable or its api shape changes, that section will be unavailable.
- **the ai summary is best-effort.** if anthropic's api is down or rate-limited, the page renders without it. the raw data is always present.

## license

bsd-3-clause. see [LICENSE](./LICENSE).
