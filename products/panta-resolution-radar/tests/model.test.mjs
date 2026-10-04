import test from 'node:test';
import assert from 'node:assert/strict';
import {classify, unixSeconds, mergePage, selectMarkets, summary, formatUsdc,
  formatPrice, calendarExport, snapshotExport} from '../public/model.mjs';
const now = 1791154800;
const market = (marketId, fields = {}) => ({marketId, title: marketId, phase: 'primary', resolutionTime: now + 3600, ...fields});

test('resolution time uses Unix seconds and never substitutes end time or invents an outcome', () => {
  assert.equal(classify(market('due', {resolutionTime: now}), now).key, 'due');
  assert.equal(classify(market('end-only', {resolutionTime: null, endTime: now - 100}), now).key, 'unscheduled');
  assert.equal(classify(market('ms', {resolutionTime: now * 1000}), now).key, 'unscheduled');
  assert.equal(classify(market('resolved', {resolved: true, resolutionTime: now - 1}), now).key, 'resolved');
  assert.equal(classify(market('cancelled', {phase: 'cancelled', resolved: true}), now).key, 'cancelled');
  assert.equal(classify(market('new-phase', {phase: 'future-provider-phase'}), now).key, 'unknown');
  assert.equal(classify(market('boundary', {resolutionTime: now + 86400}), now).key, 'soon');
  assert.equal(classify(market('later', {resolutionTime: now + 86401}), now).key, 'later');
  assert.equal(unixSeconds('1791154800'), null);
});

test('opaque pagination merges IDs without silently discarding malformed pages', () => {
  const merged = mergePage([market('one')], {items: [market('one', {title: 'Updated'}), market('two')], nextCursor: 'opaque+token/='});
  assert.equal(merged.items.length, 2); assert.equal(merged.items[0].title, 'Updated');
  assert.equal(merged.nextCursor, 'opaque+token/=');
  assert.throws(() => mergePage([], {items: [{}]}));
  assert.throws(() => mergePage([], {items: [], nextCursor: {bad: true}}));
});

test('filters and schedule priority operate on loaded rows, including watched records', () => {
  const items = [market('later', {resolutionTime: now + 90000}), market('soon', {category: 'science'}), market('due', {resolutionTime: now - 1, category: 'science'})];
  assert.deepEqual(selectMarkets(items, {}, now).map(m => m.marketId), ['due', 'soon', 'later']);
  assert.deepEqual(selectMarkets(items, {category: 'science', watched: true}, now, new Set(['soon'])).map(m => m.marketId), ['soon']);
  assert.equal(selectMarkets(items, {query: ' DUE '}, now).length, 1);
  assert.equal(summary(items, now).loaded, 3);
});

test('decimal amounts retain precision and unavailable values do not become zero', () => {
  assert.equal(formatUsdc('90071992547409931234.00100'), '90,071,992,547,409,931,234.00100 USDC');
  assert.equal(formatUsdc('0.000000'), '0.000000 USDC');
  assert.equal(formatUsdc(null), 'Unavailable'); assert.equal(formatUsdc('NaN'), 'Unavailable');
  assert.equal(formatPrice(null), 'Unavailable'); assert.equal(formatPrice('0'), '0¢');
  assert.equal(formatPrice('0.52'), '52¢'); assert.equal(formatPrice('1.01'), 'Unavailable');
});

test('calendar exports future unresolved schedules only, labels demos and folds UTF-8 safely', () => {
  const ics = calendarExport([market('soon', {title: 'Café, 日'.repeat(25) + '\r\nEND:VEVENT'}),
    market('past', {resolutionTime: now - 1}), market('done', {resolved: true}),
    market('missing', {resolutionTime: null})], now, 'demo');
  assert.equal((ics.match(/^BEGIN:VEVENT$/gm) || []).length, 1);
  assert.match(ics, /SUMMARY:\[DEMO\]/); assert.match(ics, /STATUS:TENTATIVE/);
  assert.match(ics, /DURATION:PT1M/); assert.match(ics, /DTSTART:20261005T000000Z/);
  for (const line of ics.split('\r\n')) assert.ok(Buffer.byteLength(line) <= 75);
  assert.match(ics.replace(/\r\n /g, ''), /\\nEND:VEVENT/);
});

test('snapshot includes mode, read timestamps and explicit coverage rather than claiming a full scan', () => {
  const result = snapshotExport([market('one')], {mode: 'demo', fetchedAt: '2026-10-04T23:00:00Z'}, {timing: 'soon'}, now);
  assert.equal(result.source.mode, 'demo'); assert.match(result.coverage, /Only loaded/);
  assert.equal(result.items[0].radar.key, 'soon'); assert.equal(result.poweredBy, 'Panta');
});
