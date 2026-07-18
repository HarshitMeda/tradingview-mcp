---
name: tv-orb-alerts
description: Arm Dhan auto-buy webhook alerts on the "15m Opening Candle Breakout" indicator for a list of NSE stocks in TradingView Desktop. For each stock it sets the indicator's Breakout Value + Quantity (sized to a rupee budget) + secret, then creates a 15-minute "alert() function calls only" alert wired to the Dhan webhook. Use when asked to "set up breakout alerts", "arm the ORB alerts", "create 15m opening candle breakout alerts", or "set alerts for the top N stocks" (usually the output of the qmomentum-scan skill).
---

# TradingView 15m Opening Candle Breakout → Dhan Webhook Alerts

Automates the exact manual flow the user does by hand:
> open the **15m Opening Candle Breakout** indicator settings → set **Breakout
> Value, Quantity, secret** → OK → create an **alert** → condition = the indicator,
> **alert() function calls only**, **15-minute** interval, **Webhook URL** = the
> Dhan endpoint → Create.

It drives the **live TradingView Desktop chart over CDP** using the `tradingview`
MCP tools. The indicator itself builds the JSON order payload inside its Pine
`alert()` call from its inputs (breakout/qty/secret), so **the alert only needs the
webhook URL** — no message body is typed.

## Inputs you need before running
- **Stock list with a breakout (trigger) price each.** Normally this is the
  `trigger` column from the latest [[qmomentum-skill]] Stage-2 table
  (`.claude/skills/qmomentum-scan/out/refined_<date>.json` /
  `..._<date>.md`). "Top 10" = the first N by `precise_score`.
- **Per-trade budget** in ₹ (default **₹45,000**). Quantity = `round(budget /
  breakout_value)` to whole shares.
- **Dhan secret** (short token, e.g. `h3SRz`) and **Dhan webhook URL**
  (`https://tv-webhook.dhan.co/tv/alert/<uuid>/<code>`). Confirm both with the user.

## The indicator (study `15m Opening Candle Breakout`, Pine v8.0)
`chart_get_state` lists it; note its `entity_id` (e.g. `YqK47n`, **session-specific,
re-read every run**). Its user inputs map like this (v8.0 — verified via the
settings dialog):

| input id | field | notes |
|---|---|---|
| `in_0` | **Breakout Value** | the breakout/trigger price — set per stock |
| `in_1` | **Quantity** | whole shares — set per stock = round(budget ÷ breakout) |
| `in_2` | Entry Window (min) | leave default (**90**) unless asked |
| `in_3` | **secret** | Dhan secret — set **once**, persists across symbol changes |

> `in_8`–`in_28` are Pine `strategy()` boilerplate — never touch. Older alerts on
> disk may show Pine **v6.0** where the map was shifted (`in_2` was the secret and
> there was no Entry Window) — that's the pre-update schema; the live study is v8.0.

## Preflight
1. `tv_health_check` → confirm `cdp_connected` and `api_available`.
2. `chart_get_state` → grab the `entity_id` of `15m Opening Candle Breakout`.
   If it isn't on the chart, add it / ask the user to add it (it's a private script).
3. `chart_set_timeframe` → **`15`**. The chart MUST be on 15m so the study evaluates
   on 15-minute bars and the alert inherits that interval ("Same as chart").
4. `alert_list` → snapshot existing alerts. Check for **duplicates by ticker** with
   the current secret (would double-fire) and note stale alerts on an old secret
   (usually harmless if "Stopped manually", but offer cleanup).

## Per-stock loop (repeat for each symbol)
Order matters. **Switch the symbol first** — the reload forces the study to
recompute so the new alert reads the freshly-set inputs. (If you configure a symbol
that's *already loaded* without switching, the alert can snapshot **stale** inputs;
in that case open the indicator Settings and click **OK** to commit before creating,
or switch away and back.)

1. **`chart_set_symbol` → `NSE:<SYM>`.** It returns `chart_ready:false` and lags.
2. **`chart_get_state` → confirm `symbol` == `NSE:<SYM>`** before doing anything
   else. Skipping this creates the alert on the *previous* symbol with the new
   values — a silent, dangerous mismatch. Retry `chart_set_symbol` if it hasn't
   landed.
3. **`indicator_set_inputs`** `entity_id=<id>`, `inputs={"in_0": <breakout>, "in_1":
   <qty>}` (set `"in_3": "<secret>"` too on the **first** stock only; it persists).
4. **`ui_click` `aria-label="Create alert"`** to open the alert dialog.
5. **Verify the dialog** with `ui_evaluate` — read the first button (symbol) and the
   condition button. Proceed only if:
   - symbol == `<SYM>`, and
   - condition == `15m Opening Candle Breakout (<breakout>, <qty>, 90, <secret>)`.
6. **Set trigger type to "alert() function calls only":**
   - `ui_click` `text="Order fills and alert() function calls"` (the default) to
     open the dropdown.
   - Click the **`alert() function calls only`** option. Its position is stable at
     ~**(796, 467)** via `ui_mouse_click`; if unsure, read its rect first with
     `ui_evaluate` (element with exact text, `offsetParent!==null`).
   - `ui_evaluate` → confirm the trigger button now reads `alert() function calls only`.
7. **Webhook** — set it **only on the first stock**; TradingView **retains** the
   webhook URL + enabled checkbox for every subsequent alert, so skip this step
   after #1 (the user confirmed this). To set it the first time:
   - `ui_mouse_click` the notifications summary button (`App, Toasts, Email,
     Webhook`, ~(749, 565)) to open the Notifications sub-page.
   - Find the URL input by **placeholder `https://example.com/alert-hook`**. Clear
     it reliably: `ui_evaluate` → `inp.focus(); inp.setSelectionRange(0,
     inp.value.length)` then `ui_keyboard Backspace`, verify empty, then
     `ui_type_text <dhan url>`. (Don't rely on `Cmd/Meta+A`, and don't set `.value`
     via a synthetic setter alone — it may not commit to React state.)
   - Confirm the **Webhook URL** checkbox is `aria-checked=true` (it defaults on when
     a URL is present) and the URL exactly equals the Dhan URL.
   - `ui_click` `text="Apply"` to return to the main dialog.
8. **`ui_click` `text="Create"`.** Use the **real** `ui_click`/`ui_mouse_click`
   tools — a synthetic `.click()` via `ui_evaluate` closes the dialog **without
   creating** the alert (silent failure).

## Verify (authoritative — do at the end)
- `ui_evaluate` scan of the alerts panel: collect every `[data-name="alert-item-name"]`
  whose text contains the **secret**, with its `alert-item-ticker` and
  `alert-item-status`. Expect **N rows, all "Active"**, each reading
  `(<breakout>, <qty>, 90, <secret>): alert() function calls only`.
- Or `alert_list` (source `internal_api`, the ground truth) — each new alert is
  `type:"strategy"`, `active:true`, `resolution:"15"`, `pine_version:"8.0"`, with
  `inputs.in_0/in_1/in_3` == breakout/qty/secret. Note `alert_list` does **not**
  expose the webhook URL, so the webhook can only be checked in the UI (step 7) —
  verify it before Create, not after.
- The alerts fire on the **next session** when a 15m candle breaks the Breakout
  Value. They're deletable before then (`alert_delete` / panel) if anything's wrong.

## Gotchas (learned the hard way)
- **`chart_ready:false` lag** — always re-check `chart_get_state` after
  `chart_set_symbol`; the alert will otherwise bind the old symbol.
- **Stale condition snapshot** — `indicator_set_inputs` updates the study model, but
  on an *unchanged* symbol the alert dialog can still show the *previous* committed
  values. The symbol switch (or Settings→OK) is what commits them. Always eyeball the
  condition string in step 5.
- **Real events only for Create** — synthetic DOM `.click()` on Create silently
  no-ops; use the MCP `ui_click`/`ui_mouse_click` tools (genuine CDP input).
- **Webhook is retained** — set once; verify (don't re-type) on later alerts.
- **Class-hash selectors are version-fragile** — the dialog root (`.dialog-qyCw0PaN`),
  legend title (`title-YTFIJ62h`), etc. are hashed class names that change across
  TradingView builds. Prefer **aria-label / visible text / data-name**, and
  re-discover element coordinates with `ui_evaluate` rather than hardcoding.
- **Duplicates = double orders** — before arming, check `alert_list` for an existing
  active alert on the same ticker+secret.

## Sizing quick-reference
`qty = round(budget / breakout)`, `≈value = qty * breakout` (aim within a few % of
budget). Example at ₹45,000: PGIL 2108.5→21 (₹44,279), PAISALO 74.2→606 (₹44,965),
TIRUPATIFL 70.9→635 (₹45,022).
