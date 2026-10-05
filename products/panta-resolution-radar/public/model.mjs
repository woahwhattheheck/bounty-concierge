/** Read-only projections of the Panta USDC catalog. No inferred outcomes. */
export const PHASES = new Set(['primary', 'secondary', 'resolved', 'cancelled']);
const SECOND_MAX = 253402300799; // Last second of year 9999; milliseconds are not seconds.

export function unixSeconds(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 && value <= SECOND_MAX
    ? value : null;
}

export function classify(market, nowSeconds) {
  if (market.phase === 'cancelled') return {key: 'cancelled', label: 'Cancelled', at: null};
  if (market.resolved === true || market.phase === 'resolved') return {key: 'resolved', label: 'Resolved', at: null};
  const at = unixSeconds(market.resolutionTime);
  if (!PHASES.has(market.phase)) return {key: 'unknown', label: 'Unknown phase', at};
  if (at === null) return {key: 'unscheduled', label: 'Resolution time missing', at: null};
  if (at <= nowSeconds) return {key: 'due', label: 'Schedule passed', at};
  if (at <= nowSeconds + 86400) return {key: 'soon', label: 'Next 24 hours', at};
  return {key: 'later', label: 'Later', at};
}

export function validatePage(data) {
  if (!data || typeof data !== 'object' || !Array.isArray(data.items)) throw new Error('Unexpected catalog response: items must be an array.');
  const cursor = data.nextCursor ?? null;
  if (cursor !== null && (typeof cursor !== 'string' || !cursor || cursor.length > 256)) throw new Error('Unexpected catalog cursor.');
  if (data.items.some(m => !m || typeof m !== 'object' || typeof m.marketId !== 'string' || !m.marketId)) {
    throw new Error('A catalog row is missing its marketId.');
  }
  return {items: data.items, nextCursor: cursor};
}

export function mergePage(previous, page) {
  const valid = validatePage(page);
  const byId = new Map(previous.map(m => [m.marketId, m]));
  for (const item of valid.items) byId.set(item.marketId, item);
  return {items: [...byId.values()], nextCursor: valid.nextCursor};
}

export function summary(items, nowSeconds) {
  const totals = {loaded: items.length, due: 0, soon: 0, unscheduled: 0, later: 0, resolved: 0, cancelled: 0, unknown: 0};
  for (const m of items) totals[classify(m, nowSeconds).key]++;
  return totals;
}

export function selectMarkets(items, filters, nowSeconds, watched = new Set()) {
  const query = (filters.query || '').trim().toLocaleLowerCase();
  return items.filter(m => {
    if (filters.category && m.category !== filters.category) return false;
    if (filters.phase && m.phase !== filters.phase) return false;
    if (filters.watched && !watched.has(m.marketId)) return false;
    if (filters.timing && classify(m, nowSeconds).key !== filters.timing) return false;
    return !query || [m.title, m.description, m.marketId, m.category].some(v =>
      typeof v === 'string' && v.toLocaleLowerCase().includes(query));
  }).sort((a, b) => {
    const rank = {due: 0, soon: 1, unscheduled: 2, unknown: 3, later: 4, resolved: 5, cancelled: 6};
    const ca = classify(a, nowSeconds), cb = classify(b, nowSeconds);
    return rank[ca.key] - rank[cb.key] || (ca.at ?? Infinity) - (cb.at ?? Infinity)
      || String(a.title || a.marketId).localeCompare(String(b.title || b.marketId));
  });
}

/** Decimal strings remain exact; unknown volume is not converted to zero. */
export function formatUsdc(value) {
  if (typeof value !== 'string' || !/^\d+(?:\.\d+)?$/.test(value)) return 'Unavailable';
  const [whole, fraction] = value.split('.');
  return `${BigInt(whole).toLocaleString('en-US')}${fraction !== undefined ? '.' + fraction : ''} USDC`;
}

export function formatPrice(value) {
  if (typeof value !== 'string' || !/^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(value)) return 'Unavailable';
  return `${(Number(value) * 100).toLocaleString('en-US', {maximumFractionDigits: 4})}¢`;
}

export function utcLabel(value) {
  const seconds = unixSeconds(value);
  return seconds === null ? 'Not supplied' : new Date(seconds * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
}

const escapeIcs = value => String(value).replace(/\\/g, '\\\\').replace(/\r\n|\r|\n/g, '\\n').replace(/;/g, '\\;').replace(/,/g, '\\,');
function foldIcs(line) {
  let result = '', current = '', bytes = 0;
  for (const character of line) {
    const size = new TextEncoder().encode(character).length;
    if (bytes + size > 75) { result += current + '\r\n'; current = ' '; bytes = 1; }
    current += character; bytes += size;
  }
  return result + current;
}
const icsTime = seconds => new Date(seconds * 1000).toISOString().replace(/[-:]/g, '').replace('.000', '');

export function calendarExport(items, nowSeconds, mode) {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//TJLabs//Resolution Radar//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH'];
  for (const m of items) {
    const status = classify(m, nowSeconds);
    if (!['soon', 'later'].includes(status.key)) continue;
    lines.push('BEGIN:VEVENT', `UID:${escapeIcs(m.marketId)}-${escapeIcs(mode)}@resolution-radar`,
      `DTSTAMP:${icsTime(nowSeconds)}`, `DTSTART:${icsTime(status.at)}`, `DURATION:PT1M`,
      `SUMMARY:${escapeIcs(`${mode === 'demo' ? '[DEMO] ' : ''}Panta resolution schedule: ${m.title || m.marketId}`)}`,
      `DESCRIPTION:${escapeIcs(`Powered by Panta. Catalog-scheduled time, not a guaranteed outcome or settlement. Market: ${m.marketId}. Data mode: ${mode}.`)}`,
      'STATUS:TENTATIVE', 'END:VEVENT');
  }
  return lines.concat('END:VCALENDAR').map(foldIcs).join('\r\n') + '\r\n';
}

export function snapshotExport(items, meta, filters, nowSeconds) {
  return {schemaVersion: 1, product: 'Resolution Radar', poweredBy: 'Panta',
    exportedAt: new Date(nowSeconds * 1000).toISOString(), source: meta, filters,
    coverage: 'Only loaded rows matching these filters; not a full-chain scan.',
    items: items.map(m => ({...m, radar: classify(m, nowSeconds)}))};
}
