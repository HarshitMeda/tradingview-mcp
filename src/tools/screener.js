import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/screener.js';

export function registerScreenerTools(server) {
  server.tool(
    'screener_capture',
    'Capture the exact filter of a TradingView Stock Screener by observing its live scan request via CDP. Returns { market, filter, filter2, sort } — the reusable query behind a saved/shared screener — which can be replayed against scanner.tradingview.com/<market>/scan (public, no auth). Pass a screener share url to capture a specific screen (navigates to it if not already open), or omit url to capture whichever screener tab is currently open. Reloads the screener to force a fresh scan.',
    {
      url: z.string().optional().describe('Screener share URL (e.g. https://www.tradingview.com/screener/d6aYISyT/). Omit to use the currently-open screener tab.'),
      timeout_ms: z.coerce.number().optional().describe('Max ms to wait for the scan request (default 12000)'),
    },
    async ({ url, timeout_ms }) => {
      try {
        return jsonResult(await core.captureScreenerFilter({ url, timeoutMs: timeout_ms }));
      } catch (err) {
        return jsonResult({ success: false, error: err.message }, true);
      }
    },
  );
}
