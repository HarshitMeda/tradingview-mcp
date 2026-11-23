import { register } from '../router.js';
import * as core from '../../core/data.js';

register('quote', {
  description: 'Get real-time price quote',
  handler: (opts, positionals) => core.getQuote({ symbol: positionals[0] }),
});

register('ohlcv', {
  description: 'Get OHLCV bar data',
  options: {
    count: { type: 'string', short: 'n', description: 'Number of bars (default 100, max 500)' },
    summary: { type: 'boolean', short: 's', description: 'Return summary stats instead of all bars' },
  },
  handler: (opts) => core.getOhlcv({
    count: opts.count ? Number(opts.count) : undefined,
    summary: opts.summary,
  }),
});

register('ohlcv-batch', {
  description: 'Fetch OHLCV bars for MANY symbols, written to disk (drives the chart symbol-by-symbol)',
  options: {
    timeframe: { type: 'string', short: 't', description: 'Resolution (default D)' },
    count: { type: 'string', short: 'n', description: 'Bars per symbol (default 100, max 500)' },
    out: { type: 'string', short: 'o', description: 'ABSOLUTE path to write {"SYM":[[t,o,h,l,c,v],...]}' },
    'symbols-file': { type: 'string', short: 'f', description: 'File of symbols (whitespace/comma/newline separated)' },
  },
  handler: async (opts, positionals) => {
    // Symbols come from positionals and/or a file — a shortlist can run to 50+
    // names, so the file form keeps callers off the shell's argument limits.
    const symbols = [...positionals];
    if (opts['symbols-file']) {
      const { readFileSync } = await import('node:fs');
      symbols.push(...readFileSync(opts['symbols-file'], 'utf8').split(/[\s,]+/));
    }
    const result = await core.getOhlcvBatch({
      symbols,
      timeframe: opts.timeframe,
      count: opts.count ? Number(opts.count) : undefined,
      out_path: opts.out,
    });
    // Partial failures stay in `failed` for the caller to judge; a total miss is
    // an error, so an unattended run exits non-zero instead of ranking nothing.
    if (!result.fetched) {
      throw new Error(`No bars fetched for any of ${result.requested} symbol(s): ` +
        JSON.stringify(result.failed?.slice(0, 5) || []));
    }
    return result;
  },
});

register('values', {
  description: 'Get current indicator values from data window',
  handler: () => core.getStudyValues(),
});

register('data', {
  description: 'Advanced data tools (lines, labels, tables, boxes, strategy, trades, equity, depth)',
  subcommands: new Map([
    ['lines', {
      description: 'Get Pine Script line.new() price levels',
      options: {
        filter: { type: 'string', short: 'f', description: 'Filter by study name substring' },
        verbose: { type: 'boolean', short: 'v', description: 'Include raw line data' },
      },
      handler: (opts) => core.getPineLines({ study_filter: opts.filter, verbose: opts.verbose }),
    }],
    ['labels', {
      description: 'Get Pine Script label.new() annotations',
      options: {
        filter: { type: 'string', short: 'f', description: 'Filter by study name substring' },
        max: { type: 'string', short: 'n', description: 'Max labels per study (default 50)' },
        verbose: { type: 'boolean', short: 'v', description: 'Include raw label data' },
      },
      handler: (opts) => core.getPineLabels({ study_filter: opts.filter, max_labels: opts.max ? Number(opts.max) : undefined, verbose: opts.verbose }),
    }],
    ['tables', {
      description: 'Get Pine Script table.new() data',
      options: {
        filter: { type: 'string', short: 'f', description: 'Filter by study name substring' },
      },
      handler: (opts) => core.getPineTables({ study_filter: opts.filter }),
    }],
    ['boxes', {
      description: 'Get Pine Script box.new() price zones',
      options: {
        filter: { type: 'string', short: 'f', description: 'Filter by study name substring' },
        verbose: { type: 'boolean', short: 'v', description: 'Include raw box data' },
      },
      handler: (opts) => core.getPineBoxes({ study_filter: opts.filter, verbose: opts.verbose }),
    }],
    ['strategy', {
      description: 'Get strategy performance metrics',
      handler: () => core.getStrategyResults(),
    }],
    ['trades', {
      description: 'Get strategy trade list',
      options: {
        max: { type: 'string', short: 'n', description: 'Max trades to return' },
      },
      handler: (opts) => core.getTrades({ max_trades: opts.max ? Number(opts.max) : undefined }),
    }],
    ['equity', {
      description: 'Get strategy equity curve',
      handler: () => core.getEquity(),
    }],
    ['depth', {
      description: 'Get order book / DOM data',
      handler: () => core.getDepth(),
    }],
    ['indicator', {
      description: 'Get indicator info and inputs by entity ID',
      handler: (opts, positionals) => {
        if (!positionals[0]) throw new Error('Entity ID required. Usage: tv data indicator eFu1Ot');
        return core.getIndicator({ entity_id: positionals[0] });
      },
    }],
  ]),
});
