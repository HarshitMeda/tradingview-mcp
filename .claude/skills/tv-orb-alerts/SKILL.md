---
name: tv-orb-alerts
description: Arm Dhan auto-buy webhook alerts on the "15m Opening Candle Breakout" indicator for a list of NSE stocks in TradingView Desktop. For each stock it sets the indicator's Breakout Value + Quantity (sized to a rupee budget) + secret, then creates a 15-minute "alert() function calls only" alert wired to the Dhan webhook. Use when asked to "set up breakout alerts", "arm the ORB alerts", "create 15m opening candle breakout alerts", or "set alerts for the top N stocks" (usually the output of the qmomentum-scan skill).
---

# TradingView 15m Opening Candle Breakout → Dhan Webhook Alerts

Arms one 15-minute `alert() function calls only` alert per NSE stock on the
**15m Opening Candle Breakout** study, wired to the Dhan webhook. The study builds
the JSON order payload inside its Pine `alert()` from its inputs
(breakout/qty/secret), so **the alert body is empty — only the webhook URL matters**.
Everything is driven over CDP with the `tradingview` MCP tools.

## Inputs
- **Stocks + breakout price each** — the `trigger` column of the latest
  [[qmomentum-skill]] Stage-2 ranking (`.../qmomentum-scan/out/refined.json`).
  "Top N" = first N by `precise_score`.
- **Budget** ₹/trade (default **₹45,000**) → `qty = round(budget / breakout)`.
- **Dhan secret** (e.g. `K6Zwo`) and **webhook URL**
  (`https://tv-webhook.dhan.co/tv/alert/<uuid>/<code>`). Both usually already live
  in the study/dialog from a prior run — read them (below) rather than asking.

## Input map (study `15m Opening Candle Breakout`, Pine v8.0)
`chart_get_state` → the study's `entity_id` (e.g. `YqK47n`, **session-specific**).
`data_get_indicator(entity_id)` → current inputs, incl. the secret in `in_3`.

| id | field | action |
|----|-------|--------|
| `in_0` | Breakout Value | set per stock |
| `in_1` | Quantity | set per stock |
| `in_2` | Entry Window | leave `90` |
| `in_3` | secret | already set; leave it, just read it for verification |

`in_4`–`in_28` are `strategy()` boilerplate — never touch.

## Preflight (once)
1. `tv_health_check` → `cdp_connected` + `api_available`.
2. `chart_get_state` → study `entity_id`. (Not on chart? It's a private script — ask
   the user to add it.) Also confirm a chart tab is open (screener page has no chart API).
3. `data_get_indicator(entity_id)` → read `in_3` (the secret) and confirm v8.0.
4. `chart_set_timeframe("15")` → the alert inherits "Same as chart" = 15m.
5. `alert_list` → snapshot. Any existing active alert on a ticker you're about to arm
   with the same secret = a **duplicate → double orders**; delete it first.

## Per-stock loop
Do these in order for each symbol. The alert binds whatever symbol is loaded, so the
**switch + confirm** (steps 1–2) is what prevents a silent wrong-symbol alert.

1. `chart_set_symbol("NSE:<SYM>")` — returns `chart_ready:false`, lags.
2. `chart_get_state` → **confirm `symbol == NSE:<SYM>`** before touching anything else.
3. `indicator_set_inputs(entity_id, {"in_0": <breakout>, "in_1": <qty>})`.
4. `ui_click(aria-label="Create alert")`.
5. **Set the condition to the study** (⚠️ it does NOT auto-select — it defaults to
   `Price / Crossing`, which would arm a plain price alert with no order payload):
   - Open the Condition dropdown (the button reading `Price`).
   - Pick the **`15m Opening Candle Breakout (…)`** option (last in the study list).
6. **Set trigger type** — open the type dropdown (defaults to
   `Order fills and alert() function calls`) and pick **`alert() function calls only`**.
7. **Verify, then create.** One `ui_evaluate` (below) reads the two dropdown buttons —
   proceed only if the condition string is
   `15m Opening Candle Breakout (<breakout>, <qty>, 90, <secret>)` and the trigger is
   `alert() function calls only`. Then `ui_click(text="Create")`.
   - **First stock only:** before Create, open Notifications (`App, Toasts, Email,
     Webhook`) and confirm the **Webhook URL** field == the Dhan URL and its checkbox
     is checked, then `Apply`. TradingView **retains** the webhook for every later
     alert, so skip this after #1.

### Clicking & verifying (window-independent)
Menu options render in an overlay; `ui_click(by text)` is unreliable for them and
hardcoded pixel coordinates drift with window size. Instead resolve an option's
center at runtime and click it:

```js
// ui_evaluate → returns {x,y} center of a visible leaf whose text matches
(() => { const m=[...document.querySelectorAll('*')]
  .filter(e=>e.children.length===0 && e.offsetParent &&
     /alert\(\) function calls only/.test(e.textContent.trim()));
  if(!m.length) return null; const r=m[m.length-1].getBoundingClientRect();
  return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()
```
Then `ui_mouse_click(x, y)`. Use `/^15m Opening Candle Breakout \(/` to find the
condition option the same way.

Verify snapshot (reads the committed dialog state — no screenshot needed):
```js
(() => { const b=[...document.querySelectorAll('button,[role="button"]')].map(e=>e.textContent.trim());
  return JSON.stringify({
    cond: b.find(t=>/^15m Opening Candle Breakout \(/.test(t)),
    trig: b.find(t=>/function calls only|Order fills/.test(t)),
    hdr:  [...document.querySelectorAll('*')].map(e=>e.textContent).find(t=>/^Create alert on/.test(t.trim())).trim().slice(0,30) }); })()
```
`cond` shows the full tuple incl. the secret, e.g. `…(958.2, 47, 90, K6Zwo)`.

> As a fallback only, the option coordinates in a ~1470px-wide window were roughly:
> Condition dropdown (795,306) → study option (776,537); trigger dropdown (779,348)
> → alert()-only option (766,452); Notifications row (745,550). Re-resolve with the
> snippet above rather than trusting these.

## Final verify (authoritative)
`alert_list` (source `internal_api`) — expect **N** alerts, each `type:"strategy"`,
`active:true`, `strategy_mode:"alerts"`, `resolution:"15"`, `pine_version:"8.0"`,
with `in_0`/`in_1` == breakout/qty and `in_3` == secret. `alert_list` does **not**
expose the webhook URL — that's why step 7 verifies it in the UI on stock #1.

Alerts fire on the **next session** when a 15m candle breaks the Breakout Value, and
are deletable until then (`alert_delete`).

## Gotchas
- **Condition defaults to Price** — always run step 5, or you arm a payload-less price alert.
- **`Create` needs a real click** — `ui_click`/`ui_mouse_click` only; a synthetic
  `.click()` via `ui_evaluate` closes the dialog **without creating** the alert.
- **Confirm the symbol (step 2)** — `chart_ready:false` lag otherwise binds the old symbol.
- **Webhook is retained** — set/verify once (stock #1), don't re-type after.
- **Duplicates = double orders** — clear same-ticker+secret alerts in preflight.

## Sizing
`qty = round(budget / breakout)`, `≈value = qty * breakout`. At ₹45,000:
LODHA 1219.95→37 (₹45,138), QUESS 308.1→146 (₹44,983), TIRUPATIFL 74.9→601 (₹45,015).
