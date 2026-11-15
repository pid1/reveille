// the full page as plain text, then as html. same look as the daily page:
// one <pre>, bold section names, links shown as their hostname.

import { afdParts, compass, dailyRows, hourlyRows, sunTimes } from "./parse.js";

const pad = (s, n) => String(s ?? "").padEnd(n);
const lpad = (s, n) => String(s ?? "").padStart(n);
const B0 = "[[B]]";
const B1 = "[[/B]]";
const rule = (t) => `\n${B0}${t}${B1}\n`;

function fmt(ms, tz, opts) {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, hour12: false, ...opts }).format(new Date(ms));
}
const hhmm = (ms, tz) => fmt(ms, tz, { hour: "2-digit", minute: "2-digit" }).replace(/^24/, "00");
const hour = (ms, tz) => fmt(ms, tz, { hour: "2-digit" }).replace(/^24/, "00");

function wrap(text, indent = "  ", width = 46) {
  const out = [];
  let line = indent;
  for (const w of String(text).split(/\s+/).filter(Boolean)) {
    if (line.trim() && line.length + 1 + w.length > width) {
      out.push(line);
      line = indent + w;
    } else line += (line.trim() ? " " : "") + w;
  }
  if (line.trim()) out.push(line);
  return out.join("\n");
}

function staleLabel(entry, tz) {
  return entry && entry.stale ? `[fetch failed; showing ${hhmm(entry.at, tz)} copy]\n` : "";
}

function alertsBlock(alerts, tz) {
  if (!alerts || !alerts.length) return "";
  const lines = [rule("ALERTS")];
  for (const a of alerts) {
    const until = a.ends ? ` until ${hhmm(Date.parse(a.ends), tz)}` : "";
    const name = a.event.toLowerCase();
    if (/warning$/.test(name)) lines.push(`** ${name.toUpperCase()} **${until}`);
    else if (/watch$/.test(name)) lines.push(`* ${name} *${until}`);
    else lines.push(`${name}${until}`);
  }
  return lines.join("\n");
}

const LEVEL = { TSTM: 0, MRGL: 1, SLGT: 2, ENH: 3, MDT: 4, HIGH: 5 };
const ERO = { MRGL: 1, SLGT: 2, MDT: 3, HIGH: 4 };
function riskLine(name, days, scale, max) {
  if (!days || !Object.values(days).some((v) => scale[v])) return "";
  const cell = (v) => (scale[v] ? `${v} (${scale[v]}/${max})` : "-");
  return `${pad(name, 8)} ${[1, 2, 3].map((d) => `d${d} ${cell(days[d])}`).join("  ")}`;
}

// current conditions in words. the raw metar and the taf are gone: they
// cost bytes and need decoding (win, 2026-11-15).
const COVER = { CLR: "clear", SKC: "clear", CAVOK: "clear", FEW: "a few clouds", SCT: "partly cloudy", BKN: "mostly cloudy", OVC: "overcast", VV: "sky obscured" };
const WX = [["TS", "thunderstorm"], ["FZRA", "freezing rain"], ["FZDZ", "freezing drizzle"], ["RA", "rain"], ["DZ", "drizzle"], ["SN", "snow"], ["PL", "sleet"], ["GR", "hail"], ["GS", "small hail"], ["FG", "fog"], ["BR", "mist"], ["HZ", "haze"], ["FU", "smoke"], ["DU", "dust"], ["SH", "showers"]];
export function weatherWords(code) {
  if (!code) return "";
  return code
    .split(/\s+/)
    .map((c) => {
      const intensity = c.startsWith("-") ? "light " : c.startsWith("+") ? "heavy " : "";
      const words = [];
      let rest = c.replace(/^[-+]|^VC/, "");
      for (const [k, w] of WX) if (rest.includes(k)) { words.push(w); rest = rest.replace(k, ""); }
      return words.length ? intensity + words.join(", ") : "";
    })
    .filter(Boolean)
    .join(", ");
}

function obsBlock(obs, tz) {
  if (!obs) return "";
  const mph = (kt) => Math.round(kt * 1.15078);
  const t = c2fLocal(obs.temp);
  const d = c2fLocal(obs.dewp);
  const lines = [rule("NOW"), `${t}f, dew point ${d}f`];
  if (obs.wspd != null) {
    lines.push(obs.wspd === 0 ? "wind calm" : `wind ${compass(obs.wdir)} ${mph(obs.wspd)} mph${obs.wgst ? `, gusts ${mph(obs.wgst)}` : ""}`);
  }
  const layer = (obs.clouds || []).find((c) => c.base != null);
  const sky = layer ? `${COVER[layer.cover] || layer.cover.toLowerCase()} at ${layer.base.toLocaleString("en-US")} ft` : COVER[obs.cover] || "";
  const vis = obs.visib == null ? "" : `${String(obs.visib).replace("+", " or more")} mi visibility`;
  const wx = weatherWords(obs.wxString);
  lines.push([wx, sky, vis].filter(Boolean).join(", "));
  const place = (obs.name || obs.icaoId || "").split(",")[0].toLowerCase();
  lines.push(`(${place}, ${hhmm(obs.obsTime * 1000, tz)})`);
  return lines.filter(Boolean).join("\n");
}
const c2fLocal = (c) => (c == null ? "-" : Math.round((c * 9) / 5 + 32));

function hourlyBlock(entry, now, tz) {
  if (!entry) return "";
  const rows = hourlyRows(entry.data, now);
  if (!rows.length) return "";
  const out = [rule("NEXT 36 HOURS"), staleLabel(entry, tz) + "hr tmp feel dew wind      rain tstm sky"];
  let gap = false;
  rows.forEach((r, i) => {
    if (i === 12 && !gap) {
      out.push("then every 3 hours");
      gap = true;
    }
    const w = r.wind == null ? "" : `${r.dir} ${r.wind}${r.gust && r.gust >= r.wind + 5 ? "g" + r.gust : ""}`;
    const pct = (v) => (v == null || v < 5 ? "-" : `${v}%`);
    out.push(`${hour(r.t, tz)} ${lpad(r.temp, 3)} ${lpad(r.feel, 4)} ${lpad(r.dew, 3)} ${pad(w, 9)} ${lpad(pct(r.pop), 4)} ${lpad(pct(r.tstm), 4)} ${r.sky}`.trimEnd());
  });
  return out.join("\n");
}

function dailyBlock(entry, now, tz) {
  if (!entry) return "";
  const rows = dailyRows(entry.data).filter((d) => !d.endTime || Date.parse(d.endTime) > now);
  if (!rows.length) return "";
  const out = [rule("7 DAY"), staleLabel(entry, tz) + "       hi  lo rain"];
  for (const d of rows.slice(0, 8)) {
    const name = /^(this|today)/.test(d.name) ? "today" : /^(tonight|overnight)/.test(d.name) ? "tonight" : d.name.slice(0, 3);
    const head = `${pad(name, 7)}${lpad(d.hi ?? "-", 2)} ${lpad(d.lo ?? "-", 3)} ${lpad(d.pop >= 5 ? d.pop + "%" : "-", 4)}  `;
    const indent = " ".repeat(head.length);
    out.push(head + wrap(d.text, indent, 46).slice(indent.length));
  }
  return out.join("\n");
}

function afdBlock(entry, office, tz) {
  if (!entry) return { block: "", spotter: "" };
  const p = afdParts(entry.data);
  if (!p.keyMessages) return { block: "", spotter: p.spotter };
  const lines = p.keyMessages
    .split(/\n\s*\n/)
    .map((m) => wrap(m.replace(/^-\s*/, ""), "  ").replace(/^ /, "-"));
  return {
    block: [rule(`NWS ${office} KEY MESSAGES`), staleLabel(entry, tz) + (p.issued ? `(${p.issued.toLowerCase()})` : ""), ...lines].join("\n"),
    spotter: p.spotter,
  };
}

export function renderText(s, env, now) {
  const tz = env.TZ || "America/Chicago";
  const date = fmt(now, tz, { year: "numeric", month: "2-digit", day: "2-digit" }).replace(/(\d+)\/(\d+)\/(\d+)/, "$3-$1-$2");
  const head = [`reveille full -- ${date} ${hhmm(now, tz)} ${fmt(now, tz, { timeZoneName: "short" }).split(" ").pop().toLowerCase()}`, `daily briefing: ${env.DAILY_URL || "https://reveille.pid1.space/"}`];
  if (s.failed && s.failed.length) head.push("failed: " + s.failed.join(", "));

  const parts = [head.join("\n")];
  parts.push(alertsBlock(s.alerts && s.alerts.data, tz));
  const risk = [];
  if (s.mcd && s.mcd.data && s.mcd.data.length) {
    for (const m of s.mcd.data) risk.push(wrap(`md ${m.num}: ${m.concerning.toLowerCase()}${m.watch ? `, watch ${m.watch}%` : ""}`, "", 46));
  }
  const spc = riskLine("SPC", s.spc && s.spc.data, LEVEL, 5);
  const ero = riskLine("ERO", s.ero && s.ero.data, ERO, 4);
  const afd = afdBlock(s.afd, s.office || "FWD", tz);
  if (spc) risk.push(spc);
  if (ero) risk.push(ero);
  if (afd.spotter) risk.push(wrap(`spotter: ${afd.spotter.toLowerCase().replace(/^spotter /, "")}`, "", 46).replace(/\n/g, "\n  "));
  if (risk.length) parts.push("\n" + risk.join("\n"));
  parts.push(obsBlock(s.obs && s.obs.data, tz));
  parts.push(hourlyBlock(s.grid, now, tz));
  parts.push(dailyBlock(s.forecast, now, tz));
  parts.push(afd.block);
  if (s.lat != null) {
    const sun = sunTimes(s.lat, s.lon, new Date(now));
    parts.push(`\nsun  rise ${hhmm(sun.rise, tz)}  set ${hhmm(sun.set, tz)}`);
  }
  return parts.filter(Boolean).join("\n") + "\n";
}

const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function toHtml(text, title) {
  let body = esc(text.trimEnd()).replace(/(https?:\/\/[^\s<>]+)/g, (url) => {
    let tail = "";
    while (/[.,);\]:]$/.test(url)) {
      tail = url.slice(-1) + tail;
      url = url.slice(0, -1);
    }
    let host = url;
    try {
      host = new URL(url).hostname.replace(/^www\./, "");
    } catch {}
    return `<a href="${url}">${host}</a>${tail}`;
  });
  body = body.split(B0).join("<b>").split(B1).join("</b>");
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${esc(title)}</title>
<style>pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
</head><body>
<pre>${body}</pre>
</body></html>
`;
}

export const plain = (text) => text.split(B0).join("").split(B1).join("");
