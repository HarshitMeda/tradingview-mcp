---
name: breakout-scan
description: Daily base-breakout scanner for swing setups coiled AT a 3-month high, about to break out to new highs — driven by the user's own TradingView screens (any market). Covers both the long flat base and the high-tight-flag (sharp advance + short tight coil at the highs). This is the SIBLING of qmomentum-scan: use this one for names sitting ON the high; use qmomentum-scan for names that pulled BACK below the high into a flag. Trigger on "run the breakout scan", "base breakout scan", "what's coiled at the highs", "find breakout-ready names near highs", "scan for stocks about to break out".
---

# Base-Breakout Scan

Finds swing setups **coiled at a 3-month high, about to break out to new highs**:
a stock sitting **on / just under its 3-month high** in a **tight, contracting
range** (measured in ADR, not raw %), on a **flat-to-rising 50SMA**, with
**volume drying up** — about to break out on range expansion. It deliberately
covers **both** shapes of this archetype:
- **flat base** — a long, shallow sideways range at the highs, and
- **high-tight-flag** — a sharp advance into the high, then a short tight coil
  (e.g. SNDK 2025-09-03: ran 37→53, coiled ~6 days at 53, then broke out and
  ran +285% in 8 weeks).

## Why this is a SEPARATE skill from qmomentum-scan
This is the sibling of `qmomentum-scan`, kept fully separate **on purpose** —
they are two different setups and mixing their scoring makes both confusing:

| | `qmomentum-scan` (flag) | `breakout-scan` (this) |
|---|---|---|
| Where price sits | pulled **back below** the high, coiling into a flag | **on / at** the high |
| "High" is… | in the **past** (the flagpole top) | the **current** bar |
| Consolidation | bars **since** the high | the coil **at** the high (length-agnostic) |
| Best state | 2–12% under the high | within ~0.8 ADR of the high (≥2.5%) |

The flag scanner's metrics assume the high is in the past (distance-below-high
sweet spot, bars-since-high consolidation), so they structurally **under-rate**
a name whose high is the current bar. This skill inverts that: being **at** the
high is the best state, and tightness is measured in **ADR units** so a
high-volatility name isn't punished for a wide raw %.

Horizon is **3-month (swing)** — proximity is to the **High.3M**, not the
52-week high (a year is too long a range for swing breakouts).

## Screens — the source of truth (`screens/*.json`)
Same format and mechanism as qmomentum-scan: each screen holds a screener's
**exact captured filter** (`market`, `filter`, `filter2`, `sort`). The default
is **`3m_high_in.json`** — liquid IN names with `close` within 5% of `High.3M`,
`ADRP≥3`, `SMA50<close`, `is_primary`. Add/re-sync a screen with the
`screener_capture` MCP tool, then:
`python3 .../breakout.py add-screen --from-capture <capture.json> --name "<name>"`

## How to run

### Stage 1 — the FILTER (always first, no browser, no auth)
```
python3 .claude/skills/breakout-scan/breakout.py scan --screen screens/3m_high_in.json --outdir out
# or every screen:  breakout.py scan-all --screens screens --outdir out
```
Replays the screen against TradingView's public scanner and gates on snapshot
fields (proximity to 3M high, coil tightness **in ADR**, trend support, volume
dry-up, quiet day, a power/pole bonus) into **🟢 actionable / 🟡 building /
🟠 early**. A **hard pre-filter** runs first: any name more than **1 ADR from
its 10-day SMA** (either extended above it or broken down below it) is dropped
outright, whatever its score — a breakout entry must fire from a tight coil
hugging the 10SMA (tune via `MAX_S10_DIST_ADR` in `breakout.py`). Because the
default screen already pre-selects "near a 3M high",
the **actionable gate is deliberately strict** (within ~0.8 ADR of the high,
floor 2.5% — so a reclaimed close under an old intraday spike wick still counts
— + genuinely tight coil ≤5 ADR/1M + quiet tape) so the daily shortlist stays
short (~5–20).
Saves the dated `out/breakout_<screen>_<date>.md` (history) and
`out/breakout_<screen>.json` (feeds Stage 2, overwritten each run). Show the
**🟢 Actionable** tier and the other counts. The gate score is a funnel, **not**
the ranking.

### Stage 2 — the RANKER (precise, uses the TradingView MCP)
Pull daily bars for **every** `tier == "actionable"` symbol (a chart tab must be
open) — bars go to **disk, never into context**:
```
data_get_ohlcv_batch(symbols=[...actionable syms...], timeframe="D", count=60,
                     out_path="out/bars.json")
python3 .claude/skills/breakout-scan/breakout.py refine \
    --bars out/bars.json --scan out/breakout_<screen>.json --outdir out
```
The refiner ranks purely on **bar-derived coil quality** (`precise_score`) and
writes two files: `out/refined.json` (full ranking, overwritten) and the
**exact ranked table** folded into a `<!-- stage2-table:start -->…end -->` block
of the same dated report. It prints a one-line confirmation — never the table.

**Then you (the model) author the read.** Below the table block (under a
`## Stage 2 — Read` heading, outside the markers so a re-run won't touch it),
write a short qualitative summary: group the standout **🟢 cleanest coils**
(right at the pivot + tight last week + dry volume + real pole behind them),
flag **⚠️ watch-outs** (coils that are wide/loose, already triggered/extended,
or riding a thin pole), and call out anything the metrics miss (gaps, news,
sector clustering). The interpretation is the deliverable — don't re-transcribe
the table into chat; give the same scannable read and point at the dated report.

## What "good" looks like (Stage-2 heuristics)
Ranked by `precise_score` (bar-derived, the sole ranking):
- `dist_to_pivot_adr` ≤ ~1 (within a day's range of the trigger = ready)
- `range5_over_adr` ≤ ~2.5 (tight last week, in ADR)
- `base_depth_pct` ≤ ~15 (shallow coil)
- `pole_pct` 15–200 (real power into the high — the high-tight-flag edge)
- `vol_dryup` < 1, `mas_stacked` true, `contraction` < 1 (range tightening)
- `base_len` is context only (length-agnostic: a 6-day high-tight-flag ranks as
  well as a 40-day flat base) · `trigger` = the 3M/coil high (breakout entry) ·
  `stop~` ≈ coil low.

## File conventions — dated vs. overwritten (same as qmomentum-scan)
- **Dated, kept:** `out/breakout_<screen>_<date>.md` — one per day (history).
- **Overwritten each run:** `out/breakout_<screen>.json` (Stage-1 rows),
  `out/bars.json` (Stage-2 bars), `out/refined.json` (Stage-2 ranking).

## Notes / gotchas
- Stage-1 **scan** needs no auth and works with TradingView closed. Only
  `screener_capture` (adding a screen) and Stage 2 (bars) need the running MCP.
- Thresholds are tuned against the SNDK 2025-09-03 example (correctly rated
  actionable/84) and a live IN scan (9 actionable of 105) — first-pass, meant
  to be tuned against the user's eye. Adjust weights in
  `coarse_score`/`refine_one`/`tier` if the actionable tier drifts.
