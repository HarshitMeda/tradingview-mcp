---
name: qmomentum-scan
description: Daily Qullamaggie-style momentum breakout scanner driven by the user's own TradingView screens (any market — IN/US/etc.). Finds names that ran up hard, pulled back into an orderly tight consolidation, and are now coiled near a rising 10/20/50 SMA — ready to break out. Use when asked to "run the momentum scan", "find breakout setups", "Qullamaggie scan", "scan my screens", or "what stocks are setting up today".
---

# Qullamaggie Momentum Scan

Finds continuation/breakout setups in the spirit of Kristjan Kullamägi:
a stock that **ran up 30–100%+ (flagpole)**, **pulled back for 2–8 weeks in an
orderly, tightening range (higher lows)**, is **surfing a rising, stacked
10/20/50 SMA**, and is now **coiled in a quiet, tight range near the highs** —
about to break out on range expansion.

The **universe comes from the user's own TradingView screen** (captured verbatim,
see Screens below); this skill's value-add is the Qullamaggie analysis applied on
top. The two stages are **independent and non-overlapping** — Stage 1 filters,
Stage 2 ranks — so nothing is computed-then-merged twice:
- **Stage 1 — the FILTER (fast, no browser):** a standalone Python script replays
  the screen's exact filter against TradingView's public scanner API, and uses
  snapshot fields (MA stacking, distance from 10SMA in ADRs, pullback from highs,
  pole strength, quiet day, volume dry-up) purely as a **gate** to eliminate
  obvious non-setups and bucket the survivors into **🟢 actionable / 🟡 building /
  🟠 early**. Its score only draws the gate — it does **not** rank.
- **Stage 2 — the RANKER (precise, uses the TradingView MCP):** for the actionable
  shortlist, pull daily bars and compute the tightness metrics the snapshot can't
  express (exact ADR, consolidation length, pullback depth, 5-day range ÷ ADR,
  higher-lows, volume dry-up). It ranks on these bar metrics **alone**
  (`precise_score`); no Stage-1 number feeds the ranking.

## Screens — the source of truth (`screens/*.json`)
Each screen file holds a screener's **exact captured filter** (`market`, `filter`,
`filter2`, `sort`) so the universe always matches what the user sees in
TradingView — no hand-maintained, drift-prone filters. Works for any market
(India, US `america`, …).

**Add or re-sync a screen** (do this once per screen, and again whenever the user
edits the screen's filters in TradingView):
1. Have TradingView open on that screen, OR know its share URL.
2. Call the **`screener_capture`** MCP tool (pass `url` for a specific screen, or
   omit to grab the currently-open screener). It reloads the screen and returns
   `{ market, filter, filter2, sort, sourceUrl }`.
3. Save that tool output to a JSON file and run:
   `python3 .../qmomentum.py add-screen --from-capture <capture.json> --name "<name>" --url "<share url>"`
   → writes `screens/<slug>.json`. Re-running overwrites (re-sync, no drift).

## How to run

### Stage 1 — always do this first
```
# one screen:
python3 .claude/skills/qmomentum-scan/qmomentum.py scan --screen screens/<name>.json --outdir out
# every screen the user has:
python3 .claude/skills/qmomentum-scan/qmomentum.py scan-all --screens screens --outdir out
```
Prints a tiered markdown table per screen and saves two files: the dated
`out/qmomentum_<screen>_YYYY-MM-DD.md` (human-readable, **kept per date** as
history) and `out/qmomentum_<screen>.json` (the intermediate that feeds Stage 2,
a **single stable filename overwritten every run**). Show the **🟢 Actionable**
tier (and the counts of the others). Running `scan` without `--screen` falls back to a
built-in Qullamaggie filter (`--min-perf-1m/--min-price/--min-value/--market`),
handy for a quick ad-hoc look when no screen is set up.

Coarse scores saturate at 100 easily — that is expected. Stage 1 is a **funnel**
(the screen's universe → ~200 setups → ~40 actionable), not the final ranking.

### Stage 2 — refine the actionable tier (the real ranking)
The coarse pass can't see the *tightness of the current range*. Pull daily bars
for the actionable tier straight from the chart to a JSON file — bars go to
**disk, never into the model's context** — then the Python refiner ranks them.

Use the **`data_get_ohlcv_batch` MCP tool** — it reads the internal bar model,
works without the paid exportData feature, and restores the chart afterward.
Take **every** `tier == "actionable"` symbol from the Stage-1 JSON's `rows`
(rank the whole actionable tier, not a truncated top-N), then call:

```
data_get_ohlcv_batch(
  symbols=[...all actionable syms...],
  timeframe="D", count=60,
  out_path="out/bars.json"     # bars stay out of context; returns a summary
)
```

Then the precise ranking (pass `--scan` — it attaches each name's Stage-1 gate
label AND tells the refiner which dated report to write into):
```
python3 .claude/skills/qmomentum-scan/qmomentum.py refine \
    --bars out/bars.json --scan out/qmomentum_<screen>.json --outdir out
```
The refiner writes results **to disk, not into context**, in two places:
- `out/refined.json` — the full machine-readable ranking (one stable file,
  **overwritten every run**). Read this to author the read (below) and when you
  need every metric for a name.
- `out/qmomentum_<screen>_<date>.md` — the refiner folds the **exact ranked table**
  (deterministic, rules-based numbers) into a marked
  `<!-- stage2-table:start -->…<!-- stage2-table:end -->` block of the same dated
  report Stage 1 wrote. Re-running replaces only that block (no duplicates), so
  the whole day's analysis lives in one file. It prints a one-line confirmation
  to stdout — never the table.

**Then you (the model) author the read.** The table is just numbers; the
interpretation is yours and is the point of this skill. After `refine` runs, read
`out/refined.json` and write a short qualitative summary **into the dated report,
BELOW the table block** (outside the `stage2-table` markers, i.e. after the
`<!-- stage2-table:end -->` line, under a `## Stage 2 — Read` heading — a re-run
of `refine` won't touch it). This is a
judgment call, not a formula: group the standout **🟢 cleanest coils** (tight last
week + dry volume + orderly higher-low pullback), flag **⚠️ watch-outs** (deep or
loose/wet pullbacks; coils that only just started and need more days), and call
out anything the metrics can't — gaps, news, sector clustering. Keep it scannable.

**Prerequisite:** a TradingView **chart tab must be open** (the screener page has
no charting API). If none is open, open one first (e.g. `tab_new`, or the
`tv_launch`/default chart). `data_get_ohlcv_batch` drives that chart, restores
its original symbol when done, and reads bars from the internal
`mainSeries().bars()` model — NOT `exportData` (a gated feature that errors
"not supported").

Refiner output per name, ranked by `precise_score` (bar-derived tightness, the
sole ranking): `adr_pct`, `gate` (Stage-1 bucket, context only),
`pole_pct`, `consol_days`, `pullback_pct`, `range5_over_adr` (lower = tighter,
want ≤ ~2.5), `higher_lows`, `vol_dryup` (<1 good), `mas_stacked`, and `trigger`
(breakout price = top of the recent consolidation).

**Screenshots are optional and OFF by default.** The bar metrics already
quantify the flag shape, so a screenshot only re-confirms the numbers and is the
most expensive step (switches the chart, waits to render, loads a ~300KB image
into context). Only capture the top 2–3 — via `capture_screenshot` (region
`chart`, after `chart_set_symbol` + `wait_for_render:true`) — if the user
explicitly wants a visual, or a name's metrics look good but borderline
(e.g. `range5_over_adr` near the cutoff, or a suspicious gap). Default to
presenting the numbers.

## What "good" looks like (Stage-2 heuristics)
- `pole_pct` 30–150 (strong but not parabolic)
- `consol_days` ~5–40 (2–8 week digestion)
- `pullback_pct` ≤ ~25 (orderly, shallow)
- `range5_over_adr` ≤ ~2.5 (the last week is genuinely tight)
- `higher_lows` true, `vol_dryup` < 1, `mas_stacked` true

## Output for the user
The refiner already rendered the exact ranked table (symbol, score, close,
`trigger`, `stop~` ≈ 1× ADR below entry, pole/consol/pullback/tightness) into the
dated `.md`, so **don't re-transcribe the table** — into chat or the report. Your
job is **interpretation**: the `## Stage 2 — Read` summary you write below the
table (see Stage 2) is the deliverable. In chat, give the same short scannable
read — the standout coils to watch, anything borderline, and the building/early
counts for the wider funnel — and point the user at the dated report for the full
table. Screenshots only if asked (see above).

## File conventions — what's dated vs. overwritten
The **markdown result is the history**; the **JSONs are working intermediates**:
- **Dated, kept:** `out/qmomentum_<screen>_<date>.md` — one per day, the
  screener-history dataset (see the project overview). Don't overwrite prior dates.
- **Stable, overwritten every run** (all intermediate JSONs, date-independent):
  - `out/qmomentum_<screen>.json` — Stage-1 scored rows (feeds `refine --scan`)
  - `out/bars.json` — Stage-2 daily bars pulled via `data_get_ohlcv_batch`
  - `out/refined.json` — Stage-2 full precise ranking

Both stages write results to disk by default rather than dumping them into
context, so you keep control over what you actually load. (For now everything but
the daily `.md` is overwritten; if per-date Stage-2 history is wanted later, date
`refined.json` too.)

## Notes / gotchas
- Stage-1 **scan** (replaying a captured screen) needs **no auth** and works even
  if TradingView Desktop is closed. Only **`screener_capture`** (adding/syncing a
  screen) and Stage 2 (bars + screenshots) need the running MCP + app.
- The universe comes from the captured screen's own filter (e.g. the IN screen
  uses `ADRP>=3`, `AvgValue.Traded_10d>1e8`, `SMA50<close`, `Perf.1M>15`,
  `is_primary`) — the skill does not re-impose its own filters over a screen.
- NSE is preferred over BSE on dedup (belt-and-suspenders; most screens already
  set `is_primary`).
- The scanner's snapshot `High.1M` etc. are intraday-updating; run the scan
  after the close (or near it) for stable setups.
- Thresholds are first-pass and meant to be tuned against the user's eye over a
  few days — adjust the weights in `coarse_score`/`refine_one` if the actionable
  tier drifts from what they'd pick manually.
