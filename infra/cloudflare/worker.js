// Starts reveille's daily build by calling GitHub's workflow_dispatch API.
//
// GitHub's own `schedule` event is best-effort and has deferred this
// repository's cron dispatches by four to eight hours at a stretch (see
// docs/scheduling.md). workflow_dispatch is dispatched immediately, so the
// only thing left to get right is the clock -- which is why this runs on
// Cloudflare's edge rather than on a machine at the house. A power cut, a
// dead SD card, or an ISP outage should not cost a morning briefing.
//
// This is one of two independent triggers. If Cloudflare misses a run
// entirely, the fallback crons in .github/workflows/build.yml still fire and
// the gate job there decides whether a briefing is still owed. Neither path
// knows about the other; both are safe to fire on the same morning.

const API = "https://api.github.com";
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
  const url = `${API}/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
  return fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": USER_AGENT,
    },
    body: JSON.stringify({ ref: env.REF }),
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
      if (res.status === 204) {
        console.log(`dispatched ${env.WORKFLOW} on ${env.REPO}@${env.REF}`);
        return;
      }
      lastError = `HTTP ${res.status} ${(await res.text()).slice(0, 200)}`;
      // A rejected or missing credential will be rejected again in two
      // seconds. Everything else, 403 rate limiting included, is worth a
      // retry inside the window we have.
      if (res.status === 401 || res.status === 404) break;
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
