// node --test infra/full/test/*.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import { afdParts, dailyRows, parseHamqsl, parseIpaws, parseRss, durationHours, fieldAt, hourlyRows, priorityFor, shortReason, sunTimes, vtecKey } from "../src/parse.js";
import worker, { alertPushes, cityFromRelay, ipawsPushes, morningRun, run } from "../src/index.js";
import { renderText, spotterMatters, weatherWords } from "../src/render.js";

test("vtec keys match the nas listener's format", () => {
  assert.deepEqual(vtecKey("/O.NEW.KFWD.TO.W.0028.260619T0923Z-260619T1015Z/").key, "2026.KFWD.TO.W.0028");
  assert.equal(vtecKey("/O.CON.KFWD.SV.W.0272.000000T0000Z-261103T0445Z/").key, "2026.KFWD.SV.W.0272");
  assert.equal(vtecKey("/T.NEW.KFWD.TO.W.0028.260619T0923Z-260619T1015Z/"), null);
});

test("priorities", () => {
  assert.equal(priorityFor("TO.W", ""), 2);
  assert.equal(priorityFor("SV.W", ""), 1);
  assert.equal(priorityFor("SV.W", "... FLASH FLOOD EMERGENCY ..."), 2);
  assert.equal(priorityFor("TO.A", ""), 0);
  assert.equal(priorityFor("WI.Y", ""), -1);
  assert.equal(priorityFor("SV.S", ""), null);
});

test("grid fields expand over their durations", () => {
  assert.equal(durationHours("PT3H"), 3);
  assert.equal(durationHours("P1DT6H"), 30);
  const f = fieldAt({ values: [{ validTime: "2026-11-15T21:00:00+00:00/PT3H", value: 10 }] });
  assert.equal(f(Date.parse("2026-11-15T23:30:00Z")), 10);
  assert.equal(f(Date.parse("2026-11-16T00:00:00Z")), null);
});

test("hourly rows convert units", () => {
  const now = Date.parse("2026-11-15T20:10:00Z");
  const span = (v) => ({ values: [{ validTime: "2026-11-15T00:00:00+00:00/P3D", value: v }] });
  const rows = hourlyRows({ properties: { temperature: span(10), apparentTemperature: span(8), dewpoint: span(5), windDirection: span(90), windSpeed: span(20), windGust: span(40), probabilityOfPrecipitation: span(60), skyCover: span(95) } }, now);
  assert.equal(rows.length, 21);
  assert.deepEqual([rows[0].temp, rows[0].dir, rows[0].wind, rows[0].gust, rows[0].sky], [50, "e", 12, 25, "ovc"]);
});

test("daily rows pair day and night", () => {
  const rows = dailyRows([
    { name: "Tonight", isDaytime: false, temperature: 45, probabilityOfPrecipitation: { value: 20 } },
    { name: "Monday", isDaytime: true, temperature: 63, shortForecast: "Sunny" },
    { name: "Monday Night", isDaytime: false, temperature: 47, probabilityOfPrecipitation: { value: 30 } },
  ]);
  assert.deepEqual(rows.map((r) => [r.name, r.hi, r.lo, r.pop]), [["tonight", null, 45, 20], ["monday", 63, 47, 30]]);
});

test("afd key messages and spotter statement", () => {
  const t = "AFDFWD\n\nArea Forecast Discussion\nNational Weather Service Fort Worth TX\n1258 PM CST Sun Nov 15 2026\n\n.KEY MESSAGES...\n\n- Showers exit east.\n\n- Cold today.\n\n&&\n\n.SHORT TERM...\nblah\n\n.SPOTTER INFORMATION STATEMENT...\nSpotter activation is not\nexpected at this time.\n&&\n";
  const p = afdParts(t);
  assert.equal(p.keyMessages, "- Showers exit east.\n\n- Cold today.");
  assert.equal(p.spotter, "Spotter activation is not expected at this time.");
  assert.equal(p.issued, "1258 PM CST Sun Nov 15 2026");
});

test("sun times are plausible for north texas in november", () => {
  const s = sunTimes(33.0, -97.0, new Date("2026-11-15T18:00:00Z"));
  const h = (ms) => new Date(ms).getUTCHours() + new Date(ms).getUTCMinutes() / 60;
  assert.ok(h(s.rise) > 12.5 && h(s.rise) < 13.5, `rise ${new Date(s.rise).toISOString()}`);
  assert.ok(h(s.set) > 23 && h(s.set) < 23.9, `set ${new Date(s.set).toISOString()}`);
});

test("short reasons never carry upstream text", () => {
  assert.equal(shortReason("HTTP 500"), "http 500");
  assert.equal(shortReason("The operation was aborted due to timeout"), "timeout");
  assert.equal(shortReason("<html>...</html>"), "bad data");
});

const TOR = { event: "Tornado Warning", ends: "2026-11-15T21:00:00Z", vtec: "/O.NEW.KFWD.TO.W.0042.261115T2010Z-261115T2100Z/", text: "", headline: "tornado warning issued" };

test("poll pushes: first run records, then new keys push once, nas keys never", async () => {
  const env = {};
  const state = {};
  const none = async () => false;
  assert.deepEqual(await alertPushes(env, state, [TOR], 0, none), []); // bootstrap
  const NEW = { ...TOR, vtec: TOR.vtec.replace("0042", "0043") };
  const p = await alertPushes(env, state, [TOR, NEW], 1, none);
  assert.equal(p.length, 1);
  assert.equal(p[0].priority, 2);
  assert.equal(p[0].title, "TORNADO WARNING");
  assert.deepEqual(await alertPushes(env, state, [TOR, NEW], 2, none), []);
  const NAS = { ...TOR, vtec: TOR.vtec.replace("0042", "0044") };
  assert.deepEqual(await alertPushes(env, state, [TOR, NEW, NAS], 3, async (k) => k.endsWith("0044")), []);
  const ended = await alertPushes(env, state, [], 4, none);
  assert.deepEqual(ended.map((e) => e.title), ["ended: tornado warning"]);
});

test("a failed source is one short line, never its error text", () => {
  const text = renderText({ failed: ["hourly forecast (http 500)"], office: "FWD" }, {}, Date.parse("2026-11-15T20:00:00Z"));
  assert.match(text, /^failed: hourly forecast \(http 500\)$/m);
  assert.ok(text.length < 200);
});

test("run() survives every source failing", async () => {
  const kv = new Map();
  const env = { HOME_LAT: "33.09", HOME_LON: "-97.05", STORE: { get: async (k, t) => (kv.has(k) ? (t === "json" ? JSON.parse(kv.get(k)) : kv.get(k)) : null), put: async (k, v) => kv.set(k, v) } };
  const real = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("fetch failed"); };
  try {
    await assert.rejects(run(env, Date.parse("2026-11-15T20:00:00Z"))); // no /points yet: nothing to fetch with
    kv.set("state", JSON.stringify({ points: { at: Date.parse("2026-11-15T19:00:00Z"), office: "FWD", grid: "https://api.weather.gov/gridpoints/FWD/1,1", forecast: "https://api.weather.gov/gridpoints/FWD/1,1/forecast", stations: ["KDFW"] } }));
    const r = await run(env, Date.parse("2026-11-15T20:00:00Z"));
    assert.ok(r.failed.length >= 8);
    const page = JSON.parse(kv.get("page"));
    assert.match(page.txt, /^failed: nws alerts \(network\)/m);
    assert.ok(page.html.length < 1500);
  } finally {
    globalThis.fetch = real;
  }
});

test("exactly one 10-minute run a day starts the morning briefing, across dst", () => {
  for (const day of ["2026-01-15", "2026-07-15", "2026-03-08", "2026-11-01"]) {
    let hits = 0;
    const start = Date.parse(`${day}T00:00:00Z`);
    for (let t = start; t < start + 864e5; t += 600e3) if (morningRun({}, t)) hits++;
    assert.equal(hits, 1, day);
  }
});

test("current conditions read as words, with no raw metar", () => {
  assert.equal(weatherWords("-RA BR"), "light rain, mist");
  assert.equal(weatherWords("+TSRA"), "heavy thunderstorm, rain");
  const obs = { icaoId: "KDTO", name: "Denton Enterprise Airport, TX, US", obsTime: 1794775980, temp: 7.8, dewp: 5.6, wdir: 70, wspd: 8, wgst: 19, visib: 7, wxString: "-RA", clouds: [{ cover: "OVC", base: 1000 }], rawOb: "METAR KDTO 152053Z 07008G19KT" };
  const text = renderText({ obs: { data: obs }, failed: [] }, {}, 1794776000000);
  assert.match(text, /^46f, dew point 42f$/m);
  assert.match(text, /^wind ene 9 mph, gusts 22$/m);
  assert.match(text, /^light rain, overcast at 1,000 ft, 7 mi visibility$/m);
  assert.match(text, /^\(denton enterprise airport, 14:53\)$/m);
  assert.doesNotMatch(text, /METAR|TAF/);
});

test("the spotter line shows only when activation is possible or happening", () => {
  assert.equal(spotterMatters("Spotter activation is not expected at this time."), false);
  assert.equal(spotterMatters("Spotter activation will not be needed today."), false);
  assert.equal(spotterMatters("Spotter activation is unlikely."), false);
  assert.equal(spotterMatters(""), false);
  assert.equal(spotterMatters("Spotter activation may be requested this afternoon and evening."), true);
  assert.equal(spotterMatters("Spotter activation is likely tonight."), true);
  assert.equal(spotterMatters("Spotters are currently activated."), true);
});

const HAMQSL = `<solar><solardata><calculatedconditions><band name="80m-40m" time="day">Fair</band><band name="30m-20m" time="day">Good</band><band name="17m-15m" time="day">Fair</band><band name="12m-10m" time="day">Fair</band><band name="80m-40m" time="night">Good</band><band name="30m-20m" time="night">Good</band><band name="17m-15m" time="night">Fair</band><band name="12m-10m" time="night">Poor</band></calculatedconditions><calculatedvhfconditions><phenomenon name="vhf-aurora" location="northern_hemi">Band Closed</phenomenon><phenomenon name="E-Skip" location="north_america">50MHz ES</phenomenon><phenomenon name="E-Skip" location="europe">Band Closed</phenomenon></calculatedvhfconditions></solardata></solar>`;

test("hf bands at the bottom, sun under now", () => {
  const hf = parseHamqsl(HAMQSL);
  assert.equal(hf.conditions.night["12m-10m"], "poor");
  const obs = { icaoId: "KDTO", name: "Denton Muni, TX", obsTime: 1794775980, temp: 7.8, dewp: 5.6, wdir: 70, wspd: 8, visib: 7, clouds: [] };
  const text = renderText({ obs: { data: obs }, hf: { data: hf, at: 0 }, lat: 33.0, lon: -97.0, failed: [] }, {}, 1794776000000);
  const lines = text.trimEnd().split("\n");
  assert.deepEqual(lines.slice(-4), ["      80-40 30-20 17-15 12-10", "day   fair  good  fair  fair", "night good  good  fair  poor", "vhf   e-skip 50mhz es"]);
  assert.match(text, /\(denton muni, 14:53\)\nsun  rise \d\d:\d\d  set \d\d:\d\d\n/);
});

const cap = ({ id, sender = "county@example.gov", senderName = "Denton County OEM", same = "048121", msgType = "Alert", refs = "", expires = "2026-11-16T03:33:20-06:00" }) =>
  `<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>${id}</identifier><sender>${sender}</sender><sent>2026-11-15T17:33:20-06:00</sent><status>Actual</status><msgType>${msgType}</msgType><scope>Public</scope>${refs ? `<references>${refs}</references>` : ""}<info><event>Shelter In Place Warning</event><urgency>Immediate</urgency><severity>Extreme</severity><expires>${expires}</expires><senderName>${senderName}</senderName><headline>SHELTER IN PLACE</headline><description></description><parameter><valueName>CMAMlongtext</valueName><value>Hazmat spill on I-35E &amp; FM 407. Shelter in place.</value></parameter><area><areaDesc>x</areaDesc><geocode><valueName>SAME</valueName><value>${same}</value></geocode></area></info></alert>`;
const feed = (...alerts) => `<?xml version="1.0"?><ns1:alerts xmlns:ns1="http://gov.fema.ipaws.services/feed">${alerts.join("")}</ns1:alerts>`;

test("ipaws: local, active, non-nws alerts for the county only", () => {
  const now = Date.parse("2026-11-15T23:40:00Z");
  const xml = feed(
    cap({ id: "A" }),
    cap({ id: "B", same: "048029" }),
    cap({ id: "C", sender: "w-nws.webmaster@noaa.gov", senderName: "NWS Fort Worth TX" }),
    cap({ id: "D", expires: "2026-11-15T10:00:00-06:00" }),
    cap({ id: "E", msgType: "Cancel" }),
  );
  const got = parseIpaws(xml, ["048121"], now);
  assert.deepEqual(got.map((a) => a.id), ["A"]);
  assert.equal(got[0].text, "Hazmat spill on I-35E & FM 407. Shelter in place.");
  assert.equal(got[0].sender, "Denton County OEM");
  assert.deepEqual(parseIpaws(feed(), ["048121"], now), []);
});

test("ipaws pushes: bootstrap records, new pushes once, updates stay silent", () => {
  const now = Date.parse("2026-11-15T23:40:00Z");
  const state = {};
  const [a] = parseIpaws(feed(cap({ id: "A" })), ["048121"], now);
  assert.deepEqual(ipawsPushes(state, [a], now), []); // first run
  const [b] = parseIpaws(feed(cap({ id: "B" })), ["048121"], now);
  const p = ipawsPushes(state, [a, b], now);
  assert.equal(p.length, 1);
  assert.equal(p[0].priority, 1);
  assert.equal(p[0].title, "shelter in place warning (denton county oem)");
  const [c] = parseIpaws(feed(cap({ id: "C", msgType: "Update", refs: "county@example.gov,B,2026-11-15T17:33:20-06:00" })), ["048121"], now);
  assert.deepEqual(ipawsPushes(state, [a, b, c], now), []);
});

test("new emergency sections show only when they have something", () => {
  const now = Date.parse("2026-11-15T23:40:00Z");
  const quiet = renderText({ ipaws: { data: [] }, city: { data: [] }, shelters: { data: [] }, fire: { data: [] }, failed: [] }, {}, now);
  assert.doesNotMatch(quiet, /LOCAL ALERTS|CITY ALERTS|SHELTERS|WILDFIRE/);
  const [a] = parseIpaws(feed(cap({ id: "A" })), ["048121"], now);
  const busy = renderText({
    ipaws: { data: [a] },
    city: { data: parseRss("<rss><channel><item><title>Water main break on Highland Village Rd</title><pubDate>Sun, 15 Nov 2026 17:00:00 -0600</pubDate></item></channel></rss>") },
    shelters: { data: [{ shelter_name: "Lewisville High School", address: "1098 W Main St", city: "Lewisville" }], at: now },
    fire: { data: [{ IncidentName: "Grapevine Lake", IncidentSize: 120.4, PercentContained: 40 }] },
    failed: [],
  }, {}, now);
  assert.match(busy, /\*\* SHELTER IN PLACE WARNING \*\* until 03:33/);
  assert.match(busy, /denton county oem: Hazmat spill/);
  assert.match(busy, /^Water main break on Highland Village Rd$/m);
  assert.match(busy, /^lewisville high school, 1098 w main st,\n  lewisville$/m);
  assert.match(busy, /^grapevine lake, 120 acres, 40% contained$/m);
});

test("city feeds come from the nas relay, and a stale or failed relay is named", async () => {
  const now = Date.parse("2026-11-15T23:40:00Z");
  const xml = "<rss><channel><item><title>Water main break</title><pubDate>x</pubDate></item></channel></rss>";
  assert.deepEqual(cityFromRelay({ at: "2026-11-15T23:35:00Z", feeds: { all: { xml } } }, "all", now).map((i) => i.title), ["Water main break"]);
  assert.throws(() => cityFromRelay(null, "all", now), /nas relay: nothing received/);
  assert.throws(() => cityFromRelay({ at: "2026-11-15T22:00:00Z", feeds: { all: { xml } } }, "all", now), /nas relay stale/);
  assert.throws(() => cityFromRelay({ at: "2026-11-15T23:35:00Z", feeds: { all: { error: "TimeoutError: timed out" } } }, "all", now), /nas relay: TimeoutError/);
  assert.equal(shortReason("nas relay stale"), "nas relay stale");
  assert.equal(shortReason("nas relay: nothing received"), "nas relay not running");

  // the endpoint: token required, then stored and served back
  const kv = new Map();
  const env = { SEEN_TOKEN: "t", STORE: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => kv.set(k, v) } };
  const post = (auth) => worker.fetch(new Request("https://x/city", { method: "POST", headers: auth ? { Authorization: auth } : {}, body: JSON.stringify({ at: "2026-11-15T23:35:00Z", feeds: { all: { xml } } }) }), env);
  assert.equal((await post()).status, 403);
  assert.equal((await post("Bearer t")).status, 200);
  const got = await (await worker.fetch(new Request("https://x/city.json"), env)).json();
  assert.equal(got.feeds.all.xml, xml);
});
