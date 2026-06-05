/** Lightweight date helpers; luxon loads only with Chart.js on demand. */

function pad2(n) {
  return String(n).padStart(2, "0");
}

export function formatTimeHms(date = new Date()) {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

export function formatDateTimeHmsLocal(date = new Date()) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${formatTimeHms(date)}`;
}

export function parseIsoUtcMs(value) {
  if (value == null || value === "") return null;
  const ms = Date.parse(String(value).trim());
  return Number.isFinite(ms) ? ms : null;
}

export function parseTimestampMs(msOrIso) {
  if (msOrIso == null || msOrIso === undefined) return null;
  if (typeof msOrIso === "number") return Number.isFinite(msOrIso) ? msOrIso : null;
  return parseIsoUtcMs(msOrIso);
}

export function daysUntilUtc(fromMs) {
  return (fromMs - Date.now()) / 86_400_000;
}

export function formatTimeLocal(msOrIso) {
  const ms = parseTimestampMs(msOrIso);
  if (ms === null) return "—";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function formatDateLocal(msOrIso) {
  const ms = parseTimestampMs(msOrIso);
  if (ms === null) return "—";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export function utcStartOfDayMs(date = new Date()) {
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

export function utcDayMsFromIso(dateStr) {
  const ms = parseIsoUtcMs(dateStr);
  if (ms === null) return NaN;
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

export function utcDayMsFromMillis(ms) {
  if (!Number.isFinite(ms)) return NaN;
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

export function utcDaysAgoMs(days) {
  return utcStartOfDayMs() - Math.max(days, 0) * 86_400_000;
}
