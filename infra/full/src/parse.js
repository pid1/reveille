// pure parsing and formatting for the full page. no network, no bindings,
// so test/full.test.js runs it under plain node.

export const KMH_TO_MPH = 0.621371;
export const c2f = (c) => (c == null ? null : Math.round((c * 9) / 5 + 32));
export const kmh2mph = (k) => (k == null ? null : Math.round(k * KMH_TO_MPH));

const DIRS = ["n", "nne", "ne", "ene", "e", "ese", "se", "sse", "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw"];
export const compass = (deg) => (deg == null ? "" : DIRS[Math.round(deg / 22.5) % 16]);

export function sky(pct) {
  if (pct == null) return "";
  if (pct < 10) return "clr";
  if (pct < 30) return "few";
  if (pct < 60) return "sct";
  if (pct < 90) return "bkn";
  return "ovc";
}

// "PT3H" / "P1DT6H" -> hours
export function durationHours(d) {
  const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(d || "");
  if (!m) return 1;
  return (+(m[1] || 0)) * 24 + (+(m[2] || 0)) + (+(m[3] || 0)) / 60;
}

// a raw gridpoint field ({values: [{validTime: "start/PT3H", value}]})
// -> a function returning the value covering a given epoch ms.
export function fieldAt(field) {
  const spans = ((field && field.values) || []).map((v) => {
    const [start, dur] = v.validTime.split("/");
    const t0 = Date.parse(start);
    return [t0, t0 + durationHours(dur) * 3600e3, v.value];
  });
  return (t) => {
    for (const [a, b, v] of spans) if (t >= a && t < b) return v;
    return null;
  };
}

// the hours shown: every hour for the next 12, then every 3rd to 36.
export function hourSlots(now) {
  const first = Math.ceil(now / 3600e3) * 3600e3;
  const out = [];
  for (let h = 0; h < 12; h++) out.push(first + h * 3600e3);
  for (let h = 12; h <= 36; h += 3) out.push(first + h * 3600e3);
  return out;
}

export function hourlyRows(grid, now) {
  const p = grid.properties || {};
  const f = {
    t: fieldAt(p.temperature),
    feel: fieldAt(p.apparentTemperature),
    dew: fieldAt(p.dewpoint),
    wd: fieldAt(p.windDirection),
    ws: fieldAt(p.windSpeed),
    wg: fieldAt(p.windGust),
    pop: fieldAt(p.probabilityOfPrecipitation),
    tstm: fieldAt(p.probabilityOfThunder),
    sky: fieldAt(p.skyCover),
  };
  return hourSlots(now)
    .map((t) => ({
      t,
      temp: c2f(f.t(t)),
      feel: c2f(f.feel(t)),
      dew: c2f(f.dew(t)),
      dir: compass(f.wd(t)),
      wind: kmh2mph(f.ws(t)),
      gust: kmh2mph(f.wg(t)),
      pop: f.pop(t),
      tstm: f.tstm(t),
      sky: sky(f.sky(t)),
    }))
    .filter((r) => r.temp != null);
}

// /forecast periods -> one row per day: name, hi, lo, max pop, day text
export function dailyRows(periods) {
  const days = [];
  for (const p of periods || []) {
    const pop = (p.probabilityOfPrecipitation || {}).value;
    if (p.isDaytime || !days.length) {
      days.push({ name: (p.name || "").toLowerCase(), hi: p.isDaytime ? p.temperature : null, lo: p.isDaytime ? null : p.temperature, pop: pop || 0, text: (p.shortForecast || "").toLowerCase(), endTime: p.endTime });
    } else {
      const d = days[days.length - 1];
      d.lo = p.temperature;
      d.pop = Math.max(d.pop, pop || 0);
      d.endTime = p.endTime;
    }
  }
  return days;
}

// afd: the key messages and the spotter statement, verbatim
const AFD_KEY_MESSAGES = /\n\.KEY MESSAGES\.\.\.\s*\n([\s\S]*?)(?:\n&&|\n\.[A-Z][A-Z /]+\.\.\.)/;
const AFD_SPOTTER = /\n\.SPOTTER INFORMATION STATEMENT\.\.\.\s*\n([\s\S]*?)(?:\n&&|\n\.[A-Z][A-Z /]+\.\.\.)/;
export function afdParts(text) {
  const section = (re) => {
    const m = re.exec(text || "");
    return m ? m[1].replace(/[ \t]+\n/g, "\n").trim() : "";
  };
  const issued = /^(\d{3,4} [AP]M [A-Z]{3} .*\d{4})$/m.exec(text || "");
  const spotterRaw = section(AFD_SPOTTER);
  return {
    keyMessages: section(AFD_KEY_MESSAGES),
    spotter: spotterRaw.replace(/\s+/g, " "),
    issued: issued ? issued[1] : "",
  };
}

// vtec string -> stable event key, same format as the nas listener's
// "2026.KFWD.TO.W.0028"
export function vtecKey(vtec) {
  const m = /\/([OTEX])\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)\//.exec(vtec || "");
  if (!m || m[1] !== "O") return null;
  const [, , action, office, ph, sig, etn, b, e] = m;
  const stamp = b.startsWith("000000") ? e : b;
  const year = 2000 + +stamp.slice(0, 2);
  return { key: `${year}.${office}.${ph}.${sig}.${etn}`, action, code: `${ph}.${sig}`, office, ph, sig, etn: +etn, year };
}

const HERE_PRIORITY = { "TO.W": 2, "EW.W": 2, "SV.W": 1, "FF.W": 1 };
export function priorityFor(code, text) {
  if (/tornado emergency|flash flood emergency/i.test(text || "")) return 2;
  if (code in HERE_PRIORITY) return HERE_PRIORITY[code];
  const sig = code.split(".")[1];
  if (sig === "W") return 0;
  if (code === "TO.A" || code === "SV.A") return 0;
  if (sig === "A" || sig === "Y") return -1;
  return null;
}

// sunrise/sunset (noaa's simplified algorithm), local times as epoch ms
export function sunTimes(lat, lon, date) {
  const rad = Math.PI / 180;
  const day = Math.floor((Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) - Date.UTC(date.getUTCFullYear(), 0, 0)) / 864e5);
  const g = ((2 * Math.PI) / 365) * (day - 1);
  const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const ha = Math.acos(Math.cos(90.833 * rad) / (Math.cos(lat * rad) * Math.cos(decl)) - Math.tan(lat * rad) * Math.tan(decl)) / rad;
  const midnight = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const rise = midnight + (720 - 4 * (lon + ha) - eqt) * 60e3;
  const set = midnight + (720 - 4 * (lon - ha) - eqt) * 60e3;
  return { rise, set };
}

export function shortReason(err) {
  const e = String(err || "").toLowerCase();
  if (/nas relay/.test(e)) return /stale/.test(e) ? "nas relay stale" : /nothing/.test(e) ? "nas relay not running" : "nas relay";
  const m = /http (\d{3})/.exec(e);
  if (m) return `http ${m[1]}`;
  if (/timeout|timed out|abort/.test(e)) return "timeout";
  if (/dns|resolve|getaddrinfo/.test(e)) return "dns";
  if (/network|fetch failed|connection/.test(e)) return "network";
  return "bad data";
}

// hamqsl's solar xml (n0nbh, credited in infra/full/README.md) -> the band
// grid and the two vhf entries that apply here. regex, not an xml parser:
// workers have no DOMParser and the shape is flat and stable.
export const HF_BANDS = ["80m-40m", "30m-20m", "17m-15m", "12m-10m"];
export function parseHamqsl(xml) {
  const cond = { day: {}, night: {} };
  for (const m of String(xml).matchAll(/<band name="([^"]+)" time="(day|night)">([^<]*)<\/band>/g)) {
    if (HF_BANDS.includes(m[1])) cond[m[2]][m[1]] = m[3].trim().toLowerCase().slice(0, 12);
  }
  if (!Object.keys(cond.day).length && !Object.keys(cond.night).length) throw new Error("no band conditions in hamqsl response");
  const vhf = {};
  for (const m of String(xml).matchAll(/<phenomenon name="([^"]+)" location="([^"]+)">([^<]*)<\/phenomenon>/g)) {
    const key = m[1] === "E-Skip" && m[2] === "north_america" ? "e-skip" : m[1] === "vhf-aurora" && m[2] === "northern_hemi" ? "aurora" : null;
    if (key) vhf[key] = m[3].trim().toLowerCase().slice(0, 16);
  }
  return { conditions: cond, vhf };
}

// the text of the first <name>...</name> element. a string search, so no
// regex is built from the name.
const tag = (xml, name) => {
  const open = `<${name}>`;
  const start = xml.indexOf(open);
  if (start < 0) return "";
  const end = xml.indexOf(`</${name}>`, start + open.length);
  if (end < 0) return "";
  return xml.slice(start + open.length, end).replace(/<!\[CDATA\[|\]\]>/g, "").trim();
};
const unescapeXml = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, "&");

// fema ipaws live feed (cap 1.2) -> active, non-nws alerts naming one of
// the given same codes (e.g. 048121, denton county). nws alerts are already
// covered by api.weather.gov and the nas listener.
export function parseIpaws(xml, sameCodes, now) {
  const out = [];
  for (const m of String(xml).matchAll(/<alert[\s>][\s\S]*?<\/alert>/g)) {
    const a = m[0];
    if (tag(a, "status") !== "Actual" || tag(a, "msgType") === "Cancel" || tag(a, "scope") !== "Public") continue;
    const sender = tag(a, "sender");
    const senderName = tag(a, "senderName");
    if (/nws|noaa/i.test(sender) || /nws|national weather service/i.test(senderName)) continue;
    const same = [...a.matchAll(/<geocode><valueName>SAME<\/valueName><value>(\d{6})<\/value><\/geocode>/g)].map((g) => g[1]);
    if (!same.some((c) => sameCodes.includes(c))) continue;
    const expires = tag(a, "expires");
    if (expires && Date.parse(expires) <= now) continue;
    const long = /<valueName>CMAMlongtext<\/valueName><value>([\s\S]*?)<\/value>/.exec(a);
    const refs = tag(a, "references");
    out.push({
      id: tag(a, "identifier"),
      refs: refs ? refs.split(/\s+/).map((r) => r.split(",")[1]).filter(Boolean) : [],
      event: unescapeXml(tag(a, "event")),
      sender: unescapeXml(senderName || sender),
      headline: unescapeXml(tag(a, "headline")),
      text: unescapeXml(long ? long[1] : tag(a, "description")).replace(/\s+/g, " ").slice(0, 400),
      severity: tag(a, "severity"),
      urgency: tag(a, "urgency"),
      expires,
    });
  }
  return out;
}

// a plain rss 2.0 feed -> items
export function parseRss(xml) {
  return [...String(xml).matchAll(/<item>([\s\S]*?)<\/item>/g)].map((m) => ({
    title: unescapeXml(tag(m[1], "title")),
    published: tag(m[1], "pubDate"),
  }));
}
