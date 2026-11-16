# reveille-full

the 10-minute text forecast page at [full.reveille.pid1.space](https://full.reveille.pid1.space/),
and the poll-side backup for warning pushes. no llm.

## what it shows

- a `failed:` line under the timestamp naming any source that failed this run, with a short
  reason (`http 500`, `timeout`). absent when everything worked. error text never reaches the page.
- active alerts (warnings in caps between stars, watches between single stars)
- local alerts: active alerts from local authorities (county, city, police, txdot) for denton
  county, from fema's live ipaws feed, with nws alerts left out. each new one also pushes once.
- city alerts: anything in highland village's alert center feed, every category
- open fema / red cross shelters in nearby cities (`SHELTER_CITIES` in wrangler.toml)
- wildfires within 25 miles, prescribed burns left out
- each of these four appears only when it has something
- spc mesoscale discussions covering the point, spc day 1-3 and wpc excessive rain day 1-3
  (only when there is a risk), and the fort worth office's spotter statement (only when
  activation is possible or under way)
- current conditions from the nearest airport, in words
- the next 36 hours: hourly for 12, then every 3 hours, from the raw nws grid
- sunrise and sunset under current conditions
- 7 days and the forecast discussion's key messages
- hf band conditions last: the day/night grid from n0nbh's hamqsl.com feed (credit here, not on
  the page), fetched at most once an hour as n0nbh asks, plus a vhf line when e-skip or aurora is open

the page is about 3 KB. `/full.txt` is the same page as plain text. there is no meta refresh,
because a reload with no signal would wipe the copy you have.

## how it fails

- each source fails on its own. the hourly and 7-day forecasts, discussion and outlooks
  fall back to their last good copy (6 to 24 hours), labelled with its time. alerts and
  current conditions never do: an old copy would read as current.
- if the cron stops, the page says "refresh stopped at 14:10; this is that copy" once the copy
  is more than 25 minutes old.
- if kv can't be read, a small static page links the daily briefing and nws's own text forecast.
- after 3 runs in a row without nws alerts, one push says so, and one more when it recovers.

## warning pushes

the nas listener (`listener/`) pushes warnings within seconds and reports each event it pushed to
`POST /seen`. this worker pushes only warnings that are new and that the nas did not report,
marked "via 10-minute poll", so the house still gets them when the nas or the house internet is
down. the first run after a deploy only records what is already in effect.

## deploy

`.github/workflows/full.yml` tests on every pull request and deploys on every push to main that
touches `infra/full/`. each deploy copies `HV_LAT`, `HV_LON` and the pushover keys from the
repo's secrets into the worker's. set by hand once, and kept across deploys:

```bash
cd infra/full
npx wrangler secret put SEEN_TOKEN        # same value as REVEILLE_SEEN_TOKEN on the nas
npx wrangler secret put HEALTHCHECK_URL   # optional dead-man's switch
```

## check

```bash
node --test infra/full/test/*.test.js
npx wrangler tail reveille-full
```

free plan budget: 144 cron runs a day, two kv writes each (288 of the 1,000 allowed), about a dozen
subrequests per run.
