// Starts reveille's daily build by calling a Workers Builds deploy hook.
//
// This used to call GitHub's workflow_dispatch API, because GitHub's own
// `schedule` event had deferred this repository's cron dispatches by four to
// eight hours at a stretch (see docs/scheduling.md). The clock problem is the
// same; what changed is that the build no longer runs on GitHub at all. The
// deploy hook starts a Cloudflare build that checks out the repository,
// renders the page and deploys it, so the primary path touches GitHub only as
// a git server.
//
// That also retired a credential: the deploy hook URL is the secret, scoped
// to one branch of one Worker, and it does not expire the way the
// fine-grained PAT it replaces did.
//
// This is still one of two independent triggers. If Cloudflare misses a run
// entirely, the fallback crons in .github/workflows/build.yml still fire and
// the gate job there decides whether a briefing is still owed. Neither path
// knows about the other; both are safe to fire on the same morning.

const USER_AGENT = "reveille-trigger";
const MAX_ATTEMPTS = 4;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Hour of day (0-23) in the given IANA timezone, for the given instant. */
function localHour(date, timeZone) {
  // hourCycle h23 rather than hour12:false -- the latter reports midnight as
  // "24" on some ICU builds.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    hour: "2-digit",
  }).formatToParts(date);
  return Number(parts.find((part) => part.type === "hour").value);
}

async function dispatch(env) {
  return fetch(env.DEPLOY_HOOK_URL, {
    method: "POST",
    headers: { "User-Agent": USER_AGENT },
  });
}

// Best-effort failure alert, reusing the pushover credentials the build
// already uses. Same posture as notifier.py: silent when unconfigured, and
// never allowed to throw -- an alerting failure must not mask the real one.
async function alert(env, reason) {
  if (!env.PUSHOVER_API_KEY || !env.PUSHOVER_USER_KEY) return;
  try {
    await fetch("https://api.pushover.net/1/messages.json", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        token: env.PUSHOVER_API_KEY,
        user: env.PUSHOVER_USER_KEY,
        title: "reveille trigger failed",
        message: `Could not start the build: ${reason}\n\nGitHub's fallback crons may still produce a briefing, late.`,
        priority: "1",
      }),
    });
  } catch (err) {
    console.error(`alert failed: ${err.name}: ${err.message}`);
  }
}

async function run(env) {
  if (!env.DEPLOY_HOOK_URL) {
    console.error("DEPLOY_HOOK_URL is not set");
    await alert(env, "DEPLOY_HOOK_URL is not set");
    return;
  }

  const hour = localHour(new Date(), env.TARGET_TZ);
  const target = Number(env.TARGET_HOUR);

  // Cloudflare cron triggers are UTC and have no DST awareness, so both
  // candidate times are scheduled and this check picks the right one. In CDT
  // the 09:17 UTC firing is 04:17 local and proceeds; in CST it is 03:17 and
  // defers to the 10:17 UTC firing. Nothing to change twice a year.
  if (hour !== target) {
    console.log(`skip: ${hour}:xx in ${env.TARGET_TZ}, waiting for ${target}:xx`);
    return;
  }

  let lastError = "no attempts made";
  let attempts = 0;

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    attempts = attempt;
    try {
      const res = await dispatch(env);
      if (res.ok) {
        console.log("build requested via deploy hook");
        return;
      }
      lastError = `HTTP ${res.status} ${(await res.text()).slice(0, 200)}`;
      // A deleted or mistyped hook will be rejected again in two seconds.
      // Everything else, rate limiting included, is worth a retry inside the
      // window we have.
      if (res.status === 401 || res.status === 403 || res.status === 404) break;
    } catch (err) {
      lastError = `${err.name}: ${err.message}`;
    }

    if (attempt < MAX_ATTEMPTS) await sleep(2 ** attempt * 1000);
  }

  console.error(`failed to dispatch after ${attempts} attempt(s): ${lastError}`);
  await alert(env, lastError);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(env));
  },
};
