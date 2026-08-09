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
- **Stocks + breakout price each** — the `trigger` of the latest
  [[qmomentum-skill]] ranking (`.../qmomentum-scan/out/refined.json`, the `ranked`
  list). "Top N" = first N by `score`.
- **Budget** ₹/trade (default **₹100,000**) → `qty = round(budget / breakout)`.
- **Dhan secret** (e.g. `K6Zwo`) and **webhook URL**
  (`https://tv-webhook.dhan.co/tv/alert/<uuid>/<code>`). Both usually already live
  in the study/dialog from a prior run — read them (below) rather than asking.
  **If the user supplies a secret/webhook that differs from what's stored** (new Dhan
  account, rotated code), you must **override**: set `in_3` to the new secret on
  *every* stock (step 3), and *replace* the webhook URL on stock #1 (step 7) — the
  old values will otherwise silently persist.

## Input map (study `15m Opening Candle Breakout`, Pine v8.0)
`chart_get_state` → the study's `entity_id` (e.g. `YqK47n`, **session-specific**).
`data_get_indicator(entity_id)` → current inputs, incl. the secret in `in_3`.

| id | field | action |
|----|-------|--------|
| `in_0` | Breakout Value | set per stock |
| `in_1` | Quantity | set per stock |
| `in_2` | Entry Window | leave `90` |
| `in_3` | secret | read it; leave as-is **unless** the user gave a new secret, then set it per stock |

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
3. `indicator_set_inputs(entity_id, {"in_0": <breakout>, "in_1": <qty>})` — add
   `"in_3": "<secret>"` too if the user gave a new secret. The study option text in
   the alert dialog echoes these live, so this is what you verify against in step 7.
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
   `alert() function calls only`. Then click **Create** — resolve its center at runtime
   (`/^Create$/`, see snippet) because its **y drifts** with dialog height (≈562 when the
   notifications panel collapsed on stock #1, ≈615 on later stocks) — don't hardcode it.
   - **First stock only — set the webhook (two separate buttons, don't confuse them):**
     before Create, open Notifications (the `App, Toasts, Email, Webhook` row). Confirm
     the **Webhook** checkbox is checked and the **Webhook URL** field holds the Dhan URL.
     The notifications sub-panel's primary button is **`Apply`** — clicking it applies the
     webhook and **collapses back to the main dialog; it does NOT create the alert.** Only
     the main dialog's **`Create`** button (which reappears after Apply) creates it. So the
     order on stock #1 is: set webhook → `Apply` → main `Create`. TradingView **retains**
     the webhook for every later alert, so skip the whole notifications step after #1.
   - **Replacing an existing webhook URL** (user gave a new one): clicking the field and
     typing inserts **mid-string**, and `Cmd+A`+`Backspace` does **not** reliably clear a
     React-controlled input. Clear it programmatically, then type:
     ```js
     // ui_evaluate: clear the webhook input (note: it has NO explicit type attr, so
     // querySelectorAll('input[type=text]') MISSES it — filter on e.type instead)
     (() => { const el=[...document.querySelectorAll('input')]
       .filter(e=>e.offsetParent && e.type==='text')
       .find(e=>/dhan|example\.com/.test(e.value+e.placeholder));
       if(!el) return 'notfound';
       Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,'');
       el.dispatchEvent(new Event('input',{bubbles:true})); el.focus(); return 'cleared'; })()
     ```
     Then `ui_type_text("<full Dhan URL>")` and re-read the field to confirm it equals the
     URL exactly (right length, no leftover fragments).

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
condition option the same way, and `/^Create$/` (match on a `button`/`[role=button]`,
not any leaf) to resolve the moving **Create** button before clicking it.

The two dropdowns and Create live in a centered modal, so their button anchors are
stable within a session even with side panels open: **Condition dropdown ≈(798,307)**,
**trigger dropdown ≈(782,349)**. The overlay *options* they open (study option ≈y538,
`alert()`-only ≈y452) must still be re-resolved each time with the snippet above.

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

A **just-created** alert shows `active:false` for up to ~1 min — it's mid-`Activating`
(the alerts-panel row literally reads "Activating"), **not** paused. Don't re-create it.
Just re-poll `alert_list` a moment later and confirm it flips to `active:true`. (A truly
paused prior alert — e.g. yesterday's, or a different secret — also reads `active:false`;
distinguish by `created` timestamp and `in_3`.)

Alerts fire on the **next session** when a 15m candle breaks the Breakout Value, and
are deletable until then (`alert_delete`).

## Gotchas
- **Condition defaults to Price** — the dialog does **not** remember the study across
  alerts; every new dialog reopens on `Price / Crossing`, so run steps 5–6 each time.
- **`Apply` ≠ `Create`** — on stock #1 the notifications sub-panel's `Apply` only applies
  the webhook and returns to the main dialog. The alert is created **only** by the main
  dialog's `Create`. After Apply, verify `Create` reappeared, then click it, then
  confirm via `alert_list` (it's the sole source of truth — the dialog closing isn't).
- **`Create` needs a real click** — `ui_click`/`ui_mouse_click` only; a synthetic
  `.click()` via `ui_evaluate` closes the dialog **without creating** the alert. Its y
  drifts with dialog height — resolve `/^Create$/` at runtime, don't hardcode.
- **Webhook input has no `type` attribute** — find it with
  `[...document.querySelectorAll('input')].filter(e=>e.type==='text')`, not the
  `input[type=text]` selector (which silently returns nothing). To replace its value,
  clear via the native setter + `input` event first — typing alone inserts mid-string.
- **`active:false` right after Create = "Activating"**, not paused — re-poll, it flips.
- **Confirm the symbol (step 2)** — `chart_ready:false` lag otherwise binds the old symbol.
- **Webhook is retained** — set/verify once (stock #1), don't re-type after.
- **Duplicates = double orders** — clear same-ticker+secret alerts in preflight.
  (A *paused* same-ticker alert with a *different* secret is not a duplicate — leave it.)

## Sizing
`qty = round(budget / breakout)`, `≈value = qty * breakout`. At ₹45,000:
LODHA 1219.95→37 (₹45,138), QUESS 308.1→146 (₹44,983), TIRUPATIFL 74.9→601 (₹45,015).
