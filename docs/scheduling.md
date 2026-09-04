# scheduling

## the problem

reveille is a morning briefing. it is supposed to be built at 04:17 central
and waiting for you when you get up. through most of august 2026 it was
built 30-50 minutes late, which is fine. starting 2026-08-27 it started
being built four to eight hours late, which is not -- an 08:34 briefing is
not a morning briefing, and on some mornings the page you read was still
yesterday's because the day's build had not fired yet.

the page timestamp was never wrong. `render.py` renders
`datetime.now(TIMEZONE)` and reports the truth; the truth was just that the
build ran at 08:34. fixing the timestamp would have been fixing the
thermometer.

## the measurements

scheduled `build.yml` dispatches, target versus actual, from the actions
api (`run_started_at`):

| date       | target (utc) | actual (utc) | late by       |
| ---------- | ------------ | ------------ | ------------- |
| 2026-08-15 | 09:00        | 09:29        | 30 m          |
| 2026-08-16 | 09:00        | 09:31        | 31 m          |
| 2026-08-17 | 09:00        | 09:48        | 48 m          |
| 2026-08-18 | 09:00        | 09:38        | 38 m          |
| 2026-08-19 | 09:00        | 09:37        | 38 m          |
| 2026-08-20 | 09:00        | 09:39        | 39 m          |
| 2026-08-21 | 09:00        | 09:41        | 41 m          |
| 2026-08-22 | 09:00        | 09:31        | 31 m          |
| 2026-08-23 | 09:00        | 09:32        | 32 m          |
| 2026-08-24 | 09:00        | 09:53        | 53 m          |
| 2026-08-25 | 09:00        | 09:40        | 40 m          |
| 2026-08-26 | 09:00        | 09:48        | 49 m          |
| 2026-08-27 | 09:00        | 19:42        | **10 h 43 m** |
| 2026-08-28 | 09:00        | --           | **dropped**   |
| 2026-08-29 | 09:17        | 14:27        | 5 h 10 m      |
| 2026-08-30 | 09:17        | 14:24        | 5 h 07 m      |
| 2026-08-31 | 09:17        | 17:03        | 7 h 46 m      |
| 2026-09-01 | 09:17        | 14:08        | 4 h 51 m      |
| 2026-09-02 | 09:17        | 13:37        | 4 h 20 m      |
| 2026-09-03 | 09:17        | 13:34        | 4 h 17 m      |

median delay went from ~39 minutes to ~5 hours. the 2026-08-28 dispatch
never arrived at all.

this is dispatch delay, not queueing and not runtime. `created_at` equals
`run_started_at` on every run, and the 2026-09-03 run finished 41 seconds
after it was created. the runner was never the bottleneck; github simply
did not hand us the event until 13:34.

## what it is not

**it is not the cron minute.** commit ac8a8d3 moved the daily build from
`0 9` to `17 9` on the theory that `:00` is the platform's most contended
minute. that theory is wrong here, and the data that disproves it is from a
single morning:

| workflow      | cron          | target (utc) | actual (utc) | late by    |
| ------------- | ------------- | ------------ | ------------ | ---------- |
| keepalive.yml | `23 8 * * 1`  | 08:23        | 16:13:31     | 7 h 50 m   |
| build.yml     | `17 9 * * *`  | 09:17        | 17:03:02     | 7 h 46 m   |

two workflows, two different minutes, deferred by the same amount on
2026-08-31 and delivered 49 minutes apart -- almost exactly the 54-minute
gap between their scheduled times. github is holding the repository's
schedule queue and releasing it as a unit. the minute within the hour is
not the variable. the regression also started on 2026-08-27, a day before
that commit landed, so the change neither caused the problem nor fixed it.

**it is not anything in this repository.** there are no other scheduled
workflows. `concurrency: group: pages` blocks nothing when runs take 41
seconds. the repo never approached the 60-day inactivity cutoff that
disables scheduled workflows in public repos.

it is the platform. github documents `schedule` as best-effort and does not
guarantee that it fires on time, or at all. for a daily briefing with a
name like reveille, that is a requirements mismatch, not a bug to file.

## the fix

two paths, because the reliable one lives outside github.

### primary: a cloudflare worker

`infra/cloudflare/` is a worker whose only job is to call the
`workflow_dispatch` api on a schedule. that event is dispatched immediately
-- it never enters the schedule queue that is being deferred.

it runs on cloudflare's edge rather than on a machine at the house on
purpose. a trigger that lives on local hardware trades github's unreliable
clock for a power cut, a dead sd card, or an isp outage, which is not an
improvement. cloudflare cron triggers are on the free plan.

setup, from `infra/cloudflare/`:

```bash
npx wrangler login
npx wrangler secret put GITHUB_TOKEN        # paste the pat, see below
npx wrangler deploy
```

optionally, to get a push when every dispatch attempt fails -- reusing the
same pushover app the briefing itself uses:

```bash
npx wrangler secret put PUSHOVER_API_KEY
npx wrangler secret put PUSHOVER_USER_KEY
```

the token is a fine-grained personal access token, scoped to this
repository only, with exactly one permission: **actions: read and write**.
that is enough to start a workflow run and does not grant reading secrets,
writing contents, or pushing. `workflow_dispatch` is used rather than
`repository_dispatch` precisely because of this: `repository_dispatch`
would require **contents: read and write**, a much broader grant for the
same result.

fine-grained pats expire. set a calendar reminder, or the trigger will fail
silently on whatever morning it lapses -- which is what the optional
pushover alert is for.

#### daylight saving

cloudflare cron triggers are utc and do not follow dst, so `wrangler.toml`
schedules both candidate times and the worker decides which one is 04:17
central:

```toml
crons = ["17 9 * * *", "17 10 * * *"]
```

in cdt the 09:17 utc firing is 04:17 local and proceeds, and the 10:17 one
is 05:17 and skips. in cst it is the other way round. verified across every
day of 2026: exactly one firing dispatches per day, at 04:17 local, on both
sides of both transitions. nothing to change twice a year.

#### checking on it

```bash
npx wrangler tail                    # live logs
npx wrangler deployments list        # what is actually deployed
```

a normal morning logs one `dispatched build.yml on pid1/reveille@main` and
one `skip: 5:xx in America/Chicago` an hour either side of it.

### alternative: cron on a machine you own

`scripts/trigger-build.sh` does the same thing from a shell, for a local
box, a vps, or a one-off manual run:

```cron
17 4 * * *  REVEILLE_DISPATCH_TOKEN=... /path/to/reveille/scripts/trigger-build.sh
```

same token, same api. it exits non-zero when every attempt fails, so cron
will mail you on the mornings the briefing did not get triggered. unlike
the worker it has no dst handling -- system cron already runs it in local
time.

### fallback: spread crons plus a gate

github's own scheduler still runs, as a backstop for when the worker is
down -- the two paths know nothing about each other, and both are safe to
fire on the same morning. rather than one dispatch that may land anywhere in the day,
`build.yml` asks for fifteen across a 4.5-hour window:

```yaml
- cron: "17,37,57 9-13 * * *" # 09:17-13:57 utc, 04:17-08:57 cdt
```

whichever one github actually delivers first wins. the `gate` job turns
every other attempt into a no-op, so this still produces exactly one
briefing per day. the gate skips a scheduled dispatch when either:

- the live page already carries today's local date, or
- local time is outside 03:00-20:00, meaning the dispatch is so late that
  the briefing would be read as the wrong day. the next morning's attempts
  are the better recovery path.

`push` and `workflow_dispatch` are explicit requests and always build; only
the best-effort schedule event gets second-guessed.

two properties worth knowing about the gate:

- **it fails open.** if the live page cannot be fetched, that reads as
  "nothing published yet" and the build proceeds. a duplicate briefing
  costs a duplicate pushover notification; a missed one costs the morning.
- **it does not race.** the whole workflow, gate included, is in the
  `pages` concurrency group with `cancel-in-progress: false`, so runs
  strictly serialize. by the time a second attempt reaches the freshness
  check, the first attempt's pages deployment is live. this is what makes
  the check hold when github releases a backlog of dispatches at once.

on a normal morning the first attempt builds and the other fourteen skip
in about ten seconds each, which shows up as skipped runs in the actions
tab. that noise is the cost of the backstop.

## if it happens again

check whether the delay is dispatch or queue before changing anything:

```bash
gh run list --workflow build.yml --event schedule --limit 20 \
  --json createdAt,runStartedAt,updatedAt,conclusion
```

if `createdAt` is already late, github deferred the event and nothing in
this repository can fix it -- confirm it is repository-wide by comparing
against `keepalive.yml`'s monday dispatch, and lean harder on the external
trigger. if `createdAt` is on time but `runStartedAt` is late, that is
runner queueing and is a different problem.
