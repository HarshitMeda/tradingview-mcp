/**
 * Tests for the pure helpers in src/core/screener.js — parseScanBody() and
 * marketFromUrl(). The live CDP capture (captureScreenerFilter) needs a running
 * TradingView and is exercised manually.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseScanBody, marketFromUrl } from '../src/core/screener.js';

// A trimmed but representative capture: simple `filter` conditions + a nested
// `filter2` type tree + `sort`, exactly as TradingView's screener sends.
const SAMPLE = JSON.stringify({
  columns: ['name', 'close', 'Perf.1M'],
  filter: [
    { left: 'SMA50', operation: 'less', right: 'close' },
    { left: 'AvgValue.Traded_10d', operation: 'greater', right: 100000000 },
  ],
  filter2: {
    operator: 'and',
    operands: [
      { operation: { operator: 'or', operands: [{ expression: { left: 'type', operation: 'equal', right: 'stock' } }] } },
    ],
  },
  sort: { sortBy: 'Perf.1M', sortOrder: 'desc' },
  range: [0, 100],
  markets: ['india'],
});

describe('parseScanBody', () => {
  it('extracts filter, filter2, sort, columns, markets from a JSON string', () => {
    const p = parseScanBody(SAMPLE);
    assert.equal(p.filter.length, 2);
    assert.equal(p.filter[0].left, 'SMA50');
    assert.equal(p.filter2.operator, 'and');
    assert.deepEqual(p.sort, { sortBy: 'Perf.1M', sortOrder: 'desc' });
    assert.deepEqual(p.columns, ['name', 'close', 'Perf.1M']);
    assert.deepEqual(p.markets, ['india']);
  });

  it('accepts an already-parsed object', () => {
    const p = parseScanBody(JSON.parse(SAMPLE));
    assert.equal(p.filter2.operator, 'and');
  });

  it('leaves missing parts undefined rather than throwing', () => {
    const p = parseScanBody('{"filter":[]}');
    assert.deepEqual(p.filter, []);
    assert.equal(p.filter2, undefined);
    assert.equal(p.sort, undefined);
  });
});

describe('marketFromUrl', () => {
  it('reads the market slug from a scanner URL', () => {
    assert.equal(marketFromUrl('https://scanner.tradingview.com/india/scan?label-product=screener-stock'), 'india');
    assert.equal(marketFromUrl('https://scanner.tradingview.com/america/scan'), 'america');
  });

  it('returns null for non-scanner URLs', () => {
    assert.equal(marketFromUrl('https://www.tradingview.com/screener/d6aYISyT/'), null);
    assert.equal(marketFromUrl(''), null);
    assert.equal(marketFromUrl(undefined), null);
  });
});
