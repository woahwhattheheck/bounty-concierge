import {classify, mergePage, selectMarkets, summary, formatUsdc, formatPrice,
  utcLabel, calendarExport, snapshotExport} from './model.mjs';

const $ = id => document.getElementById(id);
const state = {mode: 'demo', items: [], cursor: null, meta: null, pages: [], filters: {},
  watched: new Set(), busy: false, retryAt: 0, generation: 0, selected: null,
  listController: null, detailController: null, liveConfigured: false};
const now = () => Math.floor(Date.now() / 1000);
const text = (tag, value, className) => {
  const element = document.createElement(tag); element.textContent = value;
  if (className) element.className = className; return element;
};
const safeString = (value, fallback = 'Not supplied') => typeof value === 'string' && value ? value : fallback;
function setError(message) { $('error').textContent = message; $('error').classList.toggle('hidden', !message); }
function readWatchlist() {
  try { const value = JSON.parse(localStorage.getItem(`radar-watch-${state.mode}`) || '[]');
    state.watched = new Set(Array.isArray(value) ? value.filter(v => typeof v === 'string') : []);
  } catch { state.watched = new Set(); }
}
function toggleWatch(id) {
  if (state.watched.has(id)) state.watched.delete(id); else state.watched.add(id);
  try { localStorage.setItem(`radar-watch-${state.mode}`, JSON.stringify([...state.watched])); }
  catch { $('export-status').textContent = 'Browser storage is unavailable. This watchlist lasts for the current page only.'; }
  render();
}
function controls() {
  const cooling = state.mode === 'live' && state.retryAt > Date.now() / 1000;
  $('refresh').disabled = state.busy || cooling;
  $('load-more').disabled = state.busy || cooling;
  $('refresh').textContent = state.busy ? 'Loading catalog…' : cooling
    ? `Retry available in ${Math.ceil(state.retryAt - Date.now() / 1000)}s` : 'Refresh catalog ↻';
  $('export-json').disabled = !state.meta;
  $('export-ics').disabled = !state.meta;
}
async function request(path, signal) {
  const response = await fetch(path, {signal, cache: 'no-store'});
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Request failed (${response.status}).`);
    error.retryAt = payload.error?.retryAt; throw error;
  }
  return payload;
}
function handleError(error) {
  if (error.name === 'AbortError') return;
  if (Number.isFinite(error.retryAt)) state.retryAt = Math.max(state.retryAt, error.retryAt);
  setError(error.message + (state.items.length ? ' The previous catalog remains visible with its original timestamps.' : ''));
}
function readFilters() {
  state.filters = {...state.filters, query: $('search').value, category: $('category').value,
    phase: $('phase').value, timing: $('timing').value};
  render();
}
function fillCategories() {
  const current = state.filters.category || '';
  $('category').replaceChildren(new Option('All categories', ''));
  for (const category of [...new Set(state.items.map(m => m.category).filter(v => typeof v === 'string' && v))].sort()) {
    $('category').add(new Option(category, category));
  }
  // Retain an active category even when a refresh returns a different first page.
  if (current && ![...$('category').options].some(o => o.value === current)) $('category').add(new Option(current, current));
  $('category').value = current;
}
async function loadCatalog(append = false) {
  if (state.busy || (state.mode === 'live' && state.retryAt > Date.now() / 1000)) return;
  if (append && !state.cursor) return;
  const generation = state.generation, requestedCursor = append ? state.cursor : null;
  const controller = new AbortController(); state.listController = controller;
  state.busy = true; controls(); setError('');
  try {
    const path = state.mode === 'demo' ? '/api/demo' : '/api/catalog' + (requestedCursor ? `?cursor=${encodeURIComponent(requestedCursor)}` : '');
    const payload = await request(path, controller.signal);
    if (generation !== state.generation) return;
    if (payload.meta?.mode !== state.mode) throw new Error('The server returned a different data mode. The response was not mixed into this catalog.');
    const page = mergePage(append ? state.items : [], payload.data);
    const repeated = page.nextCursor && (page.nextCursor === requestedCursor || (append && state.pages.some(p => p.requestCursor === page.nextCursor)));
    state.items = page.items; state.cursor = repeated ? null : page.nextCursor;
    state.meta = payload.meta;
    if (!append) state.pages = [];
    state.pages.push({...payload.meta, requestCursor: requestedCursor, returnedCount: payload.data.items.length});
    state.paginationStopped = Boolean(repeated);
    if (!append) clearDetail();
    fillCategories(); render();
    if (repeated) setError('The provider repeated a pagination cursor. Loaded rows are retained; further paging is stopped to avoid a request loop. Coverage is incomplete.');
  } catch (error) { if (generation === state.generation) handleError(error); }
  finally { if (generation === state.generation) { state.busy = false; controls(); } }
}
function clearDetail() {
  state.detailController?.abort(); state.selected = null;
  $('detail-content').replaceChildren(); $('detail-content').classList.add('hidden');
  $('detail-empty').classList.remove('hidden');
}
function buildDetail(market, meta, detailReady = false, errorMessage = '') {
  const panel = $('detail-content'); panel.replaceChildren();
  $('detail-empty').classList.add('hidden'); panel.classList.remove('hidden');
  const status = classify(market, now());
  panel.append(text('span', status.label, `badge ${status.key}`), text('h2', safeString(market.title, market.marketId)), text('div', market.marketId, 'detail-id'));
  if (errorMessage) panel.append(text('p', errorMessage, 'error'));
  else if (!detailReady) panel.append(text('p', 'Reading this market’s detail…', 'small'));
  panel.append(text('p', safeString(market.description, 'No description supplied.')));
  const details = document.createElement('dl');
  const pairs = [['Phase', safeString(market.phase)], ['Resolution schedule · UTC', utcLabel(market.resolutionTime)],
    ['Market end · UTC', utcLabel(market.endTime)], ['Catalog volume', formatUsdc(market.volumeUsdc)],
    ['Oracle / source label', safeString(market.oracle)], ['Region', safeString(market.region)]];
  for (const [name, value] of pairs) details.append(text('dt', name), text('dd', value));
  panel.append(details);
  const prices = text('div', '', 'price-grid');
  // List prices are intentionally never shown as live detail prices.
  for (const [side, price] of [['YES', market.yesPrice], ['NO', market.noPrice]]) {
    const cell = text('div', `${side} spot price`, 'price-cell');
    cell.append(text('strong', detailReady ? formatPrice(price) : 'Unavailable')); prices.append(cell);
  }
  panel.append(prices, text('p', state.mode === 'demo' ? 'Synthetic detail prices, for interface demonstration only.' : 'Spot data is read on demand. Unavailable means no price was returned; it never means zero.', 'small'));
  const note = text('div', '', 'principle'); note.append(text('strong', 'Scheduled, not guaranteed.'), text('p', 'The API’s resolutionTime is distinct from endTime. A past schedule does not by itself establish a settlement failure or an outcome.')); panel.append(note);
  const provenance = text('p', `${state.mode === 'demo' ? 'DEMO' : 'LIVE'} · ${meta?.fetchedAt || 'Timestamp unavailable'}${meta?.cacheHit ? ' · cached original response' : ''}`, 'detail-source');
  if (state.mode === 'live' && detailReady && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(market.marketId)) {
    const link = text('a', 'Canonical API record ↗'); link.href = `https://live-api.panta.market/api/v1/markets/${encodeURIComponent(market.marketId)}/`; link.target = '_blank'; link.rel = 'noopener noreferrer';
    provenance.append(document.createElement('br'), link, text('span', ' (API authentication required)'));
  } else provenance.append(text('span', ' · no live market evidence'));
  panel.append(provenance);
}
async function openDetail(market) {
  state.detailController?.abort(); const controller = new AbortController(); state.detailController = controller;
  state.selected = market.marketId; const generation = state.generation;
  render(); buildDetail(market, state.meta);
  try {
    if (state.mode === 'live' && state.retryAt > Date.now() / 1000) throw new Error('Panta cooldown is active; no detail request was sent.');
    const prefix = state.mode === 'demo' ? '/api/demo/' : '/api/markets/';
    const payload = await request(prefix + encodeURIComponent(market.marketId), controller.signal);
    if (generation !== state.generation || state.selected !== market.marketId || controller.signal.aborted) return;
    if (payload.data?.marketId !== market.marketId || payload.meta?.mode !== state.mode) throw new Error('Detail identity did not match the selected market.');
    buildDetail(payload.data, payload.meta, true);
  } catch (error) {
    if (error.name !== 'AbortError' && generation === state.generation && state.selected === market.marketId) {
      if (Number.isFinite(error.retryAt)) state.retryAt = Math.max(state.retryAt, error.retryAt);
      buildDetail(market, state.meta, false, error.message); controls();
    }
  }
}
function render() {
  const totals = summary(state.items, now());
  for (const key of ['loaded', 'due', 'soon', 'unscheduled']) $(`count-${key}`).textContent = state.meta ? totals[key] : '—';
  const filtered = selectMarkets(state.items, state.filters, now(), state.watched);
  $('visible-count').textContent = `${filtered.length} of ${state.items.length} loaded`;
  $('watch-count').textContent = state.watched.size;
  for (const [id, active] of [['all-tab', !state.filters.watched], ['watch-tab', !!state.filters.watched]]) {
    $(id).classList.toggle('active', active); $(id).setAttribute('aria-pressed', String(active));
  }
  const fragment = document.createDocumentFragment();
  for (const m of filtered) {
    const status = classify(m, now()), row = text('article', '', 'market-row');
    row.classList.toggle('selected', state.selected === m.marketId);
    const button = text('button', '', 'market-open'); button.type = 'button';
    button.setAttribute('aria-label', `Inspect ${safeString(m.title, m.marketId)}`);
    const main = text('div', '', 'market-main'); main.append(text('span', safeString(m.title, m.marketId), 'market-title'));
    const tags = text('div', '', 'market-tags'); tags.append(text('span', safeString(m.category, 'Uncategorized'), 'category'), text('span', safeString(m.phase, 'Unknown'))); main.append(tags);
    const timing = text('div', '', 'market-timing'); timing.append(text('span', status.label, `badge ${status.key}`), text('span', utcLabel(m.resolutionTime), 'schedule-date'));
    button.append(main, timing); button.addEventListener('click', () => openDetail(m));
    const watched = state.watched.has(m.marketId), watch = text('button', watched ? '★' : '☆', 'watch-button');
    watch.type = 'button'; watch.setAttribute('aria-pressed', String(watched)); watch.setAttribute('aria-label', `${watched ? 'Unwatch' : 'Watch'} ${safeString(m.title, m.marketId)}`);
    watch.addEventListener('click', () => toggleWatch(m.marketId)); row.append(button, watch); fragment.append(row);
  }
  $('market-list').replaceChildren(fragment); $('empty').classList.toggle('hidden', filtered.length > 0 || !state.meta);
  $('load-more').classList.toggle('hidden', !state.cursor);
  if (state.meta) {
    const timestamps = state.pages.map(p => p.fetchedAt).filter(Boolean).sort();
    $('coverage').textContent = `${state.mode === 'demo' ? 'SYNTHETIC DEMO' : 'PANTA CATALOG'} · ${state.pages.length} page(s), ${state.items.length} unique loaded market(s). ${state.paginationStopped ? 'Paging stopped; coverage incomplete.' : state.cursor ? 'More pages exist; filters apply only to loaded rows.' : 'No next cursor returned for this view; this is not a full-chain scan.'} Snapshot time${timestamps[0] !== timestamps.at(-1) ? 's' : ''}: ${timestamps[0]}${timestamps[0] !== timestamps.at(-1) ? ' — ' + timestamps.at(-1) : ''}.`;
  } else $('coverage').textContent = 'No catalog response loaded.';
  controls();
}
async function changeMode(mode) {
  state.generation++; state.listController?.abort(); state.detailController?.abort();
  state.mode = mode; state.items = []; state.cursor = null; state.meta = null; state.pages = []; state.busy = false; state.paginationStopped = false;
  state.filters = {}; $('search').value = ''; $('phase').value = ''; $('timing').value = '';
  readWatchlist(); clearDetail(); fillCategories(); setError('');
  $('mode-badge').textContent = mode === 'demo' ? 'DEMO · SYNTHETIC DATA' : 'LIVE · PANTA API';
  $('mode-badge').classList.toggle('live', mode === 'live');
  $('mode-notice').textContent = mode === 'demo' ? 'Demonstration mode. Every market, address, price and volume below is synthetic. No live Panta API call is made.' : 'Live mode. Catalog rows are read from Panta, not a full-chain scan. A past schedule is not proof of a settlement failure. No automatic polling.';
  $('connection-note').textContent = mode === 'demo' ? (state.liveConfigured ? 'A live key is configured on this server. Select the live catalog to make a read-only request.' : 'No live key is configured. Set PANTA_API_KEY on the server to use the live catalog. Keys never enter this page.') : 'Up to 50 markets per page. Detail reads are on demand; responses share a 60-second server cache.';
  render(); await loadCatalog();
}
function download(contents, type, extension) {
  const url = URL.createObjectURL(new Blob([contents], {type})), link = document.createElement('a');
  link.href = url; link.download = `resolution-radar-${state.mode}-${new Date().toISOString().slice(0, 10)}.${extension}`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('refresh').addEventListener('click', () => loadCatalog());
$('load-more').addEventListener('click', () => loadCatalog(true));
$('mode').addEventListener('change', () => changeMode($('mode').value));
for (const id of ['category', 'phase', 'timing']) $(id).addEventListener('change', readFilters);
$('search').addEventListener('input', readFilters);
$('all-tab').addEventListener('click', () => { state.filters.watched = false; render(); });
$('watch-tab').addEventListener('click', () => { state.filters.watched = true; render(); });
$('clear-filters').addEventListener('click', () => { state.filters = {}; for (const id of ['search', 'category', 'phase', 'timing']) $(id).value = ''; render(); });
for (const button of document.querySelectorAll('[data-timing]')) button.addEventListener('click', () => { $('timing').value = button.dataset.timing; readFilters(); $('markets').scrollIntoView({block: 'start'}); });
$('export-json').addEventListener('click', () => {
  const items = selectMarkets(state.items, state.filters, now(), state.watched);
  const meta = {...state.meta, pages: state.pages, morePagesAvailable: !!state.cursor, paginationStopped: !!state.paginationStopped};
  download(JSON.stringify(snapshotExport(items, meta, state.filters, now()), null, 2), 'application/json', 'json');
  $('export-status').textContent = `Exported ${items.length} filtered ${state.mode} row(s), including source timestamps and coverage.`;
});
$('export-ics').addEventListener('click', () => {
  const items = selectMarkets(state.items, state.filters, now(), state.watched);
  const count = items.filter(m => ['soon', 'later'].includes(classify(m, now()).key)).length;
  download(calendarExport(items, now(), state.mode), 'text/calendar;charset=utf-8', 'ics');
  $('export-status').textContent = `Exported ${count} future ${state.mode} schedule(s). Past, resolved, cancelled and missing-time records are excluded. Calendar times are tentative.`;
});
// This timer updates button labels only. It never issues a network request.
setInterval(controls, 1000);
try {
  const config = await request('/api/config'); state.liveConfigured = config.liveConfigured === true;
  $('mode').querySelector('[value="live"]').disabled = !state.liveConfigured;
  const mode = config.defaultMode === 'live' && state.liveConfigured ? 'live' : 'demo'; $('mode').value = mode;
  await changeMode(mode);
} catch (error) { setError(`Unable to initialize: ${error.message}`); controls(); }
