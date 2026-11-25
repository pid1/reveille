// reveille-full: the 10-minute text forecast page, plus the poll-side
// alert pushes that back up the nas nwws-oi listener.
//
// scheduled(): fetch every source on its own, keep the last good copy of
// the slow-moving ones, render, write the page to kv, push new warnings
// the nas has not already pushed.
// fetch(): serve the page from kv; accept the nas's "already pushed" report.

import { parseHamqsl, parseIpaws, parseRss, priorityFor, shortReason, vtecKey } from "./parse.js";
import { plain, renderText, toHtml } from "./render.js";

const UA = "reveille-full (github.com/pid1/reveille)";
const NWS = "https://api.weather.gov";
const IEM = "https://mesonet.agron.iastate.edu/json";
const STUCK_MS = 25 * 60e3;

// how long a copy may stand in for a failed fetch. alerts and current
// conditions never carry over: an old copy of either reads as current.
const COPY_HOURS = { grid: 6, forecast: 24, afd: 24, spc: 12, ero: 12, hf: 48, shelters: 2 };

async function get(url, { json = true, timeout = 8000, headers = {} } = {}) {
  const r = await fetch(url, { headers: { "User-Agent": UA, Accept: json ? "application/geo+json, application/json" : "application/xml, text/xml, text/plain, */*", ...headers }, signal: AbortSignal.timeout(timeout) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return json ? r.json() : r.text();
}

async function points(env, state, now) {
  const p = state.points;
  if (p && now - p.at < 24 * 3600e3) return p;
  try {
    return await refreshPoints(env, state, now);
  } catch (e) {
    if (p) return p; // yesterday's grid and stations are still right
    throw e;
  }
}

async function refreshPoints(env, state, now) {
  const d = await get(`${NWS}/points/${(+env.HOME_LAT).toFixed(4)},${(+env.HOME_LON).toFixed(4)}`);
  const pr = d.properties;
  const st = await get(pr.observationStations);
  const stations = (st.features || []).map((f) => f.properties.stationIdentifier).filter((s) => /^K[A-Z0-9]{3}$/.test(s)).slice(0, 3);
  return (state.points = { at: now, office: pr.gridId, grid: pr.forecastGridData, forecast: pr.forecast, stations });
}

// iem point lookups are slow (several seconds each), so the three days go
// in parallel with a longer timeout than the nws calls.
async function iemDays(base, now) {
  const days = await Promise.all([1, 2, 3].map((day) => get(`${base}&day=${day}&time=${iemTime(now, day)}`, { timeout: 20000 })));
  return Object.fromEntries(days.map((d, i) => [i + 1, ((d && d.outlook) || {}).threshold || ""]));
}

const iemTime = (now, day) => new Date(now + (day - 1) * 864e5).toISOString().slice(0, 16) + "Z";

async function gather(env, state, now) {
  const lat = (+env.HOME_LAT).toFixed(3);
  const lon = (+env.HOME_LON).toFixed(3);
  const pts = await points(env, state, now);
  const jobs = {
    alerts: async () => {
      const d = await get(`${NWS}/alerts/active?point=${lat},${lon}`);
      return (d.features || []).map((f) => {
        const p = f.properties;
        return { id: p.id, event: p.event, ends: p.ends || p.expires, vtec: ((p.parameters || {}).VTEC || [])[0] || "", text: `${p.headline || ""} ${p.description || ""}`.slice(0, 2000), headline: p.headline || "" };
      });
    },
    obs: async () => {
      for (const id of pts.stations) {
        const d = await get(`https://aviationweather.gov/api/data/metar?ids=${id}&format=json`);
        if (Array.isArray(d) && d[0] && d[0].obsTime) return d[0];
      }
      throw new Error("no current observation");
    },
    grid: () => get(pts.grid),
    forecast: async () => (await get(pts.forecast)).properties.periods,
    afd: async () => (await get(`${NWS}/products/types/AFD/locations/${pts.office}/latest`)).productText,
    spc: () => iemDays(`${IEM}/spcoutlook.py?lat=${lat}&lon=${lon}&cat=categorical`, now),
    ero: () => iemDays(`${IEM}/wpcoutlook.py?lat=${lat}&lon=${lon}`, now),
    // n0nbh asks for no more than one request an hour; reuse the last copy
    // in between (it is marked fresh, not stale: nothing failed)
    hf: async () => {
      const c = (state.copies || {}).hf;
      if (c && now - c.at < 3600e3) return { reuse: c };
      return parseHamqsl(await get("https://www.hamqsl.com/solarxml.php", { json: false }));
    },
    // local authorities' alerts (county, city, police, txdot) from fema's
    // live ipaws feed. nws alerts are dropped: they are covered above.
    ipaws: async () => {
      const since = new Date(now - 24 * 3600e3).toISOString().slice(0, 19) + "Z";
      const xml = await get(`https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/${since}`, { json: false, timeout: 15000 });
      if (!/<(ns1:)?alerts/.test(xml)) throw new Error("not an ipaws feed");
      return parseIpaws(xml, (env.SAME_CODES || "048121").split(","), now);
    },
    // fema's open-shelter list (the national shelter system, which includes
    // red cross shelters), limited to nearby cities
    shelters: async () => {
      const cities = (env.SHELTER_CITIES || "").split(",").map((c) => `'${c.trim().toUpperCase().replace(/'/g, "")}'`).join(",");
      const where = encodeURIComponent(`state='TX' AND UPPER(city) IN (${cities})`);
      // gis.fema.gov often holds a request open and answers the next one in
      // half a second, so three short tries rather than one long one
      const url = `https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/MapServer/0/query?where=${where}&outFields=shelter_name,address,city,shelter_status,total_population,evacuation_capacity&returnGeometry=false&f=json`;
      let d;
      for (let i = 0; i < 3; i++) {
        try {
          d = await get(url, { timeout: 8000 });
          break;
        } catch (e) {
          if (i === 2) throw e;
        }
      }
      if (!Array.isArray(d.features)) throw new Error("bad shelter response");
      return d.features.map((f) => f.attributes).filter((a) => /open/i.test(a.shelter_status || "open"));
    },
    // wildfires (not prescribed burns) within 25 miles
    fire: async () => {
      const d = await get(`https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=${encodeURIComponent("IncidentTypeCategory='WF'")}&geometry=${lon},${lat}&geometryType=esriGeometryPoint&inSR=4326&distance=25&units=esriSRUnit_StatuteMile&outFields=IncidentName,IncidentSize,PercentContained&returnGeometry=false&f=json`, { timeout: 15000 });
      if (!Array.isArray(d.features)) throw new Error("bad wildfire response");
      return d.features.map((f) => f.attributes);
    },
    // the city's alert center, every category. the city's site does not
    // answer cloudflare, so the nas fetches it and posts it to /city; a
    // relay older than 30 minutes is a failure, named as such.
    city: async () => {
      const relay = await env.STORE.get("city", "json");
      return cityFromRelay(relay, "all", now);
    },
    mcd: async () => {
      const d = await get(`${IEM}/spcmcd.py?lat=${lat}&lon=${lon}`, { timeout: 20000 });
      return (d.mcds || []).filter((m) => Date.parse(m.utc_expire) > now).map((m) => ({ num: m.product_num, concerning: m.concerning || "", watch: m.watch_confidence }));
    },
  };
  const names = Object.keys(jobs);
  const results = await Promise.allSettled(names.map((n) => jobs[n]()));
  const s = { office: pts.office, lat: +env.HOME_LAT, lon: +env.HOME_LON, failed: [] };
  state.copies = state.copies || {};
  names.forEach((n, i) => {
    const r = results[i];
    if (r.status === "fulfilled") {
      if (r.value && r.value.reuse) {
        s[n] = r.value.reuse;
        return;
      }
      s[n] = { data: r.value, at: now };
      if (n in COPY_HOURS) state.copies[n] = s[n];
      return;
    }
    const copy = state.copies[n];
    const reason = shortReason(r.reason && r.reason.message);
    console.log(`[full] ${n} failed: ${r.reason && r.reason.message}`);
    if (copy && n in COPY_HOURS && now - copy.at <= COPY_HOURS[n] * 3600e3) {
      s[n] = { ...copy, stale: true };
      s.failed.push(`${NAMES[n]} (${reason}, older copy shown)`);
    } else {
      s.failed.push(`${NAMES[n]} (${reason})`);
    }
  });
  return s;
}

const NAMES = { alerts: "nws alerts", obs: "current conditions", grid: "hourly forecast", forecast: "7-day forecast", afd: "forecast discussion", spc: "spc outlook", ero: "excessive rain outlook", mcd: "mesoscale discussions", hf: "hf bands", ipaws: "local alerts", shelters: "shelters", fire: "wildfires", city: "city alerts" };

// -- pushes -------------------------------------------------------------------

async function push(env, { title, message, priority, url }) {
  if (!env.PUSHOVER_API_KEY || !env.PUSHOVER_USER_KEY) {
    console.log(`[full] push (not configured) p=${priority} ${title} | ${message}`);
    return;
  }
  const form = new URLSearchParams({ token: env.PUSHOVER_API_KEY, user: env.PUSHOVER_USER_KEY, title: title.slice(0, 250), message: message.slice(0, 1024), priority: String(priority) });
  if (url) {
    form.set("url", url);
    form.set("url_title", "details");
  }
  if (priority === 2) {
    form.set("retry", "60");
    form.set("expire", "1800");
  }
  try {
    const r = await fetch("https://api.pushover.net/1/messages.json", { method: "POST", body: form, signal: AbortSignal.timeout(8000) });
    console.log(`[full] push ${r.ok ? "sent" : "rejected " + r.status} p=${priority} ${title}`);
  } catch (e) {
    console.log(`[full] push failed: ${e.message}`);
  }
}

// warnings the nas listener missed: new keys only, and nothing the nas
// already reported. keys the poll pushed get an all-clear when they go.
export async function alertPushes(env, state, alerts, now, nasSeen) {
  const min = +(env.PUSH_MIN_PRIORITY || 0);
  const known = (state.alerts = state.alerts || {});
  const tz = env.TZ || "America/Chicago";
  const out = [];
  const active = new Set();
  for (const a of alerts) {
    const v = vtecKey(a.vtec);
    if (!v) continue;
    active.add(v.key);
    if (known[v.key]) continue;
    const p = priorityFor(v.code, a.text);
    known[v.key] = { by: "poll", pushed: false, name: a.event.toLowerCase(), at: now };
    // the very first run only records what is already in effect
    if (!state.alertsInit || p == null || p < min || (await nasSeen(v.key))) continue;
    known[v.key].pushed = true;
    const until = a.ends ? ` until ${new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit" }).format(new Date(a.ends)).toLowerCase()}` : "";
    const name = a.event.toLowerCase();
    out.push({ title: p >= 1 ? name.toUpperCase() : name, message: `in effect for the house${until} (via 10-minute poll). ${a.headline}`.trim(), priority: p, url: `https://mesonet.agron.iastate.edu/vtec/event/${v.year}-O-NEW-${v.office}-${v.ph}-${v.sig}-${String(v.etn).padStart(4, "0")}` });
  }
  for (const [key, k] of Object.entries(known)) {
    if (active.has(key)) continue;
    if (k.pushed) out.push({ title: `ended: ${k.name}`, message: `${k.name} is no longer in effect for the house.`, priority: -1 });
    delete known[key];
  }
  state.alertsInit = true;
  return out;
}

export const RELAY_STALE_MS = 30 * 60e3;
export function cityFromRelay(relay, name, now) {
  if (!relay || !relay.at) throw new Error("nas relay: nothing received");
  if (now - Date.parse(relay.at) > RELAY_STALE_MS) throw new Error("nas relay stale");
  const f = (relay.feeds || {})[name];
  if (!f) throw new Error("nas relay: feed missing");
  if (f.error) throw new Error(`nas relay: ${f.error}`);
  return parseRss(f.xml);
}

// local-authority alerts: push each new one once. an update that references
// one already seen stays silent. the first run only records what is active.
export function ipawsPushes(state, alerts, now) {
  const seen = (state.ipaws = state.ipaws || {});
  const out = [];
  for (const a of alerts) {
    if (seen[a.id]) continue;
    const update = a.refs.some((r) => seen[r]);
    seen[a.id] = now;
    if (!state.ipawsInit || update) continue;
    const p = /extreme|severe/i.test(a.severity) && /immediate/i.test(a.urgency) ? 1 : 0;
    out.push({ title: `${a.event.toLowerCase()} (${a.sender.toLowerCase()})`, message: (a.text || a.headline).slice(0, 1000), priority: p });
  }
  for (const [id, at] of Object.entries(seen)) if (now - at > 3 * 864e5) delete seen[id];
  state.ipawsInit = true;
  return out;
}

// -- pipeline health -------------------------------------------------------------

async function health(env, state, alertsOk, now) {
  const h = (state.health = state.health || { fails: 0, notified: false });
  const tz = env.TZ || "America/Chicago";
  if (alertsOk) {
    if (h.notified) await push(env, { title: "reveille full recovered", message: "nws alerts are reachable again.", priority: -1 });
    state.health = { fails: 0, notified: false };
    return;
  }
  h.fails += 1;
  h.since = h.since || now;
  if (h.fails >= 3 && !h.notified) {
    const at = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(h.since));
    await push(env, { title: "reveille full: alerts failing", message: `the 10-minute poll has not reached nws alerts since ${at}. the nas listener is unaffected.`, priority: 0 });
    h.notified = true;
  }
}

// -- handlers ---------------------------------------------------------------

// kv allows 1,000 writes a day across the account, so state is written only
// when it has to be. the parts that stop a push going out twice (alerts,
// ipaws, health), the station lookup and the hf fetch time (n0nbh's hourly
// ask) save on the run they change. the other fallback copies save at most
// every 30 minutes: their shortest cutoff is 2 hours.
export const STATE_SAVE_MS = 30 * 60e3;
const mustSave = (st) => JSON.stringify([st.alerts, st.alertsInit, st.ipaws, st.ipawsInit, st.health, st.points, st.copies && st.copies.hf && st.copies.hf.at]);
export function shouldSaveState(before, state, now) {
  return mustSave(state) !== before || !(now - (state.savedAt || 0) < STATE_SAVE_MS);
}

export async function run(env, now = Date.now()) {
  const state = (await env.STORE.get("state", "json")) || {};
  const before = mustSave(state);
  const s = await gather(env, state, now);
  const text = renderText(s, env, now);
  const html = toHtml(text, `reveille full -- ${new Date(now).toISOString().slice(0, 10)}`);
  await env.STORE.put("page", JSON.stringify({ html, txt: plain(text), at: now }));
  const alertsOk = !!(s.alerts && !s.alerts.stale);
  if (alertsOk) {
    const pushes = await alertPushes(env, state, s.alerts.data, now, async (k) => !!(await env.STORE.get(`seen:${k}`)));
    for (const p of pushes) await push(env, p);
  }
  if (s.ipaws && !s.ipaws.stale) {
    for (const p of ipawsPushes(state, s.ipaws.data, now)) await push(env, p);
  }
  await health(env, state, alertsOk, now);
  if (shouldSaveState(before, state, now)) {
    state.savedAt = now;
    await env.STORE.put("state", JSON.stringify(state));
  }
  if (env.HEALTHCHECK_URL) {
    try {
      await fetch(env.HEALTHCHECK_URL, { signal: AbortSignal.timeout(5000) });
    } catch (e) {
      console.log(`[full] healthcheck ping failed: ${e.message}`);
    }
  }
  return { bytes: html.length, failed: s.failed };
}

const FALLBACK = (daily) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>reveille full</title></head><body><pre>the full page has not been built yet, or its store
cannot be read right now.

daily briefing: <a href="${daily}">reveille.pid1.space</a>
nws text forecast: <a href="https://forecast.weather.gov/">forecast.weather.gov</a></pre></body></html>`;

const HEADERS = { "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex" };

// true on the one 10-minute run that should start the morning briefing
export function morningRun(env, now) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: env.TZ || "America/Chicago", hourCycle: "h23", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(now));
  const hour = +parts.find((p) => p.type === "hour").value;
  const minute = +parts.find((p) => p.type === "minute").value;
  const m0 = +(env.MORNING_MINUTE || 20);
  return hour === +(env.MORNING_HOUR || 4) && minute >= m0 && minute < m0 + 10;
}

async function morning(env, now) {
  if (!env.TRIGGER || !morningRun(env, now)) return;
  try {
    const r = await env.TRIGGER.fetch("https://reveille-trigger/dispatch", { method: "POST" });
    console.log(`[full] morning briefing trigger: ${r.status}`);
  } catch (e) {
    console.log(`[full] morning briefing trigger failed: ${e.message}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    // the morning trigger goes first and on its own, so nothing in the page
    // build can keep the briefing from starting
    ctx.waitUntil(morning(env, event.scheduledTime || Date.now()));
    ctx.waitUntil(run(env).then((r) => console.log(`[full] wrote ${r.bytes} bytes; failed: ${r.failed.join(", ") || "none"}`)));
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    // the nas posts the city feeds here; both pages read them back from
    // /city.json (public: it's the city's own public rss)
    if (url.pathname === "/city" && req.method === "POST") {
      const auth = req.headers.get("Authorization") || "";
      if (!env.SEEN_TOKEN || auth !== `Bearer ${env.SEEN_TOKEN}`) return new Response("forbidden\n", { status: 403 });
      const body = await req.text();
      if (body.length > 400_000) return new Response("too large\n", { status: 413 });
      let doc;
      try {
        doc = JSON.parse(body);
      } catch {
        return new Response("bad request\n", { status: 400 });
      }
      if (!doc || typeof doc.at !== "string" || typeof doc.feeds !== "object") return new Response("bad request\n", { status: 400 });
      await env.STORE.put("city", JSON.stringify({ at: doc.at, feeds: doc.feeds }));
      return new Response("ok\n");
    }
    if (url.pathname === "/city.json" && req.method === "GET") {
      const relay = await env.STORE.get("city");
      return new Response(relay || "{}", { headers: { ...HEADERS, "Content-Type": "application/json" } });
    }
    if (url.pathname === "/seen" && req.method === "POST") {
      const auth = req.headers.get("Authorization") || "";
      if (!env.SEEN_TOKEN || auth !== `Bearer ${env.SEEN_TOKEN}`) return new Response("forbidden\n", { status: 403 });
      let keys = [];
      try {
        keys = ((await req.json()).keys || []).filter((k) => /^\d{4}\.[A-Z]{4}\.[A-Z]{2}\.[A-Z]\.\d{4}$/.test(k)).slice(0, 50);
      } catch {
        return new Response("bad request\n", { status: 400 });
      }
      await Promise.all(keys.map((k) => env.STORE.put(`seen:${k}`, "nas", { expirationTtl: 2 * 86400 })));
      return new Response(`ok ${keys.length}\n`);
    }
    if (req.method !== "GET" && req.method !== "HEAD") return new Response("method not allowed\n", { status: 405 });
    const daily = env.DAILY_URL || "https://reveille.pid1.space/";
    let page = null;
    try {
      page = await env.STORE.get("page", "json");
    } catch (e) {
      console.log(`[full] kv read failed: ${e.message}`);
    }
    if (!page) return new Response(FALLBACK(daily), { status: 503, headers: { ...HEADERS, "Content-Type": "text/html; charset=utf-8" } });
    const stuck = Date.now() - page.at > STUCK_MS;
    const tz = env.TZ || "America/Chicago";
    const at = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(page.at));
    const note = `refresh stopped at ${at}; this is that copy.`;
    if (url.pathname === "/full.txt") {
      return new Response((stuck ? note + "\n" : "") + page.txt, { headers: { ...HEADERS, "Content-Type": "text/plain; charset=utf-8" } });
    }
    if (url.pathname !== "/") return Response.redirect(new URL("/", url).toString(), 302);
    const html = stuck ? page.html.replace("<pre>", `<pre>${note}\n`) : page.html;
    return new Response(html, { headers: { ...HEADERS, "Content-Type": "text/html; charset=utf-8" } });
  },
};
