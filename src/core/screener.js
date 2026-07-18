/**
 * Screener capture — turn a live/opened TradingView screener into its exact
 * scan payload by observing the outgoing request via CDP's Network domain.
 *
 * A screener share URL (/screener/<id>/) only resolves to its filter through an
 * undocumented TradingView service, and the app issues the scan from a Web
 * Worker, so page-level fetch/XHR interception is unreliable. CDP Network
 * capture sees the real request regardless of origin, so we attach to the
 * screener page target, force a fresh scan (reload/navigate), and grab the
 * exact { filter, filter2, sort } + market from the request body.
 */
import CDP from 'chrome-remote-interface';
import { CDP_HOST, CDP_PORT } from '../connection.js';

const SCAN_RE = /scanner\.tradingview\.com\/([a-z-]+)\/scan/i;

/** Extract the 8-ish char screener id from a share URL, e.g. .../screener/d6aYISyT/. */
function screenerId(url) {
  const m = String(url || '').match(/\/screener\/([A-Za-z0-9]+)/);
  return m ? m[1] : null;
}

async function listTargets() {
  const resp = await fetch(`http://${CDP_HOST}:${CDP_PORT}/json/list`);
  return resp.json();
}

/** Open a browser-level CDP session (where Target.createTarget/closeTarget live). */
async function browserSession() {
  const resp = await fetch(`http://${CDP_HOST}:${CDP_PORT}/json/version`);
  const info = await resp.json();
  return CDP({ target: info.webSocketDebuggerUrl });
}

/**
 * Capture the scan payload for a screener.
 * @param {object} opts
 * @param {string} [opts.url] Screener URL to capture. If omitted, uses the
 *   currently-open screener tab.
 * @param {number} [opts.timeoutMs=12000] How long to wait for a scan request.
 * @returns {Promise<{success:true, market:string, sourceUrl:string,
 *   filter:any, filter2:any, sort:any, columns:number, capturedAt:string}>}
 */
export async function captureScreenerFilter({ url, timeoutMs = 12000 } = {}) {
  const targets = await listTargets();
  const pages = targets.filter(t => t.type === 'page');
  const wantId = screenerId(url);

  // Prefer an already-open tab for the requested screener (or any screener).
  let target = wantId
    ? pages.find(t => screenerId(t.url) === wantId)
    : pages.find(t => /tradingview\.com\/screener\//i.test(t.url || ''));

  // No matching screener tab? Open the requested URL in a NEW tab (left open
  // for the user) rather than navigating one of their existing tabs away from
  // what they had open.
  let browser = null;
  let createdTargetId = null;
  let navigateTo = null;
  if (!target) {
    if (!url) {
      throw new Error('No screener tab is open. Open your TradingView screener first, or pass its url.');
    }
    browser = await browserSession();
    try {
      const { targetId } = await browser.Target.createTarget({ url: 'about:blank' });
      createdTargetId = targetId;
      const refreshed = await listTargets();
      target = refreshed.find(t => t.id === targetId);
    } catch (e) {
      try { await browser.close(); } catch { /* ignore */ }
      throw new Error(`Failed to open a new screener tab: ${e.message}`);
    }
    if (!target) {
      try { await browser.Target.closeTarget({ targetId: createdTargetId }); } catch { /* ignore */ }
      try { await browser.close(); } catch { /* ignore */ }
      throw new Error('Failed to open a new screener tab.');
    }
    // Attach and start capturing on the blank tab first, then navigate to the
    // screener so we never miss the scan request that fires on load.
    navigateTo = url;
  }

  const client = await CDP({ target: target.webSocketDebuggerUrl });
  let captured = null;

  const tryBody = async (net, params, sessionId) => {
    if (captured) return;
    const req = params.request;
    if (!SCAN_RE.test(req.url) || req.method !== 'POST') return;
    let body = req.postData;
    if (!body && req.hasPostData) {
      try {
        const r = sessionId
          ? await client.send('Network.getRequestPostData', { requestId: params.requestId }, sessionId)
          : await net.getRequestPostData({ requestId: params.requestId });
        body = r.postData;
      } catch { /* body unavailable */ }
    }
    if (body) captured = { market: req.url.match(SCAN_RE)[1], body };
  };

  try {
    const { Network, Page, Target } = client;
    await Network.enable();
    Network.requestWillBeSent(p => tryBody(Network, p));

    // Defensive: catch a worker-originated scan too (page-origin confirmed in
    // testing, but keep the fallback for robustness).
    await Target.setAutoAttach({ autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
    client.on('Target.attachedToTarget', async ({ sessionId }) => {
      try { await client.send('Network.enable', {}, sessionId); } catch { /* ignore */ }
    });
    client.on('event', msg => {
      if (msg.sessionId && msg.method === 'Network.requestWillBeSent') {
        tryBody(null, msg.params, msg.sessionId);
      }
    });

    await Page.enable();
    if (navigateTo) await Page.navigate({ url: navigateTo });
    else await Page.reload({ ignoreCache: true });

    const start = Date.now();
    while (!captured && Date.now() - start < timeoutMs) {
      await new Promise(r => setTimeout(r, 200));
    }
    if (!captured) {
      throw new Error('No scanner request seen within timeout — is this a screener page that runs a scan? Try increasing timeout_ms.');
    }

    const parsed = parseScanBody(captured.body);
    return {
      success: true,
      market: captured.market,
      sourceUrl: navigateTo || target.url,
      filter: parsed.filter ?? null,
      filter2: parsed.filter2 ?? null,
      sort: parsed.sort ?? null,
      columns: Array.isArray(parsed.columns) ? parsed.columns.length : 0,
      capturedAt: new Date().toISOString(),
    };
  } finally {
    try { await client.close(); } catch { /* already closed */ }
    // Leave any tab we opened in place for the user — just drop our CDP session.
    if (browser) { try { await browser.close(); } catch { /* ignore */ } }
  }
}

/**
 * Parse a captured scan request body and pull out the reusable query parts.
 * Pure function (no CDP) so it is unit-testable without a live TradingView.
 */
export function parseScanBody(body) {
  const obj = typeof body === 'string' ? JSON.parse(body) : body;
  return {
    filter: obj.filter,
    filter2: obj.filter2,
    sort: obj.sort,
    columns: obj.columns,
    markets: obj.markets,
  };
}

/** Extract the market slug from a scanner URL (exported for tests). */
export function marketFromUrl(url) {
  const m = String(url || '').match(SCAN_RE);
  return m ? m[1] : null;
}
