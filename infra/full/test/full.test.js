// node --test infra/full/test/*.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import { afdParts, dailyRows, durationHours, fieldAt, hourlyRows, priorityFor, shortReason, sunTimes, vtecKey } from "../src/parse.js";
import { alertPushes, morningRun, run } from "../src/index.js";
import { renderText, weatherWords } from "../src/render.js";

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
