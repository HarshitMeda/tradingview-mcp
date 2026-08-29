---
name: qmomentum-scan
description: Daily Qullamaggie-style momentum breakout scanner driven by the user's own TradingView screens (any market — IN/US/etc.). Finds names that ran up hard, pulled back into an orderly tight consolidation, and are now coiled near a rising 10/20/50 SMA — ready to break out. Use when asked to "run the momentum scan", "find breakout setups", "Qullamaggie scan", "scan my screens", or "what stocks are setting up today".
---

# Qullamaggie Momentum Scan

Ranks the user's own TradingView screen for Qullamaggie-style breakout-readiness:
names that ran up, pulled back into a tight coil near a rising 10/20/50 SMA, and
are about to break out. **The screen is the filter; the script does the ranking.**

All scoring, filtering, and threshold logic lives in the code — `class QMomentum`
in `qmomentum.py` (a subclass of the shared `../_lib/screener_ranking.py` base).
**This doc is only how to run it.** To change *what* it selects or how it ranks,
edit the class (its constants + `is_loser` / `bar_metrics` / `disqualify` /
`readiness`), not this file.

## Screens — the source of truth (`screens/*.json`)
Each screen file holds a screener's exact captured filter (`market`, `filter`,
`filter2`, `sort`) so the universe matches what the user sees in TradingView. Works
for any market (India, US `america`, …).

**Add or re-sync a screen** (once per screen, and again whenever the user edits its
filters in TradingView):
1. Have TradingView open on that screen, OR know its share URL.
2. Call the **`screener_capture`** MCP tool (pass `url` for a specific screen, or
   omit to grab the currently-open screener).
3. Save that output to a JSON file and run:
   `python3 .../qmomentum.py add-screen --from-capture <capture.json> --name "<name>" --url "<share url>"`
   → writes `screens/<slug>.json` (re-running overwrites).

## How to run — two steps, bridged by a bar fetch
Python can't call the TradingView MCP, so you pull the bars yourself between the
two script calls.

### Step 1 — `scan` → shortlist (fast, no app needed)
```
python3 .claude/skills/qmomentum-scan/qmomentum.py scan --screen screens/<name>.json --outdir out
# every screen at once:  … scan-all --screens screens --outdir out
```
Replays the screen against the public scanner (no auth, works with the app closed),
writes the survivors to `out/qmomentum_<screen>.json` (stable, overwritten), prints
the shortlist, and lists the exact `EXCHANGE:SYM` symbols to pull bars for on stderr.

### Step 2 — pull bars, then `rank` (the ranking)
Pull daily bars for **every** shortlist symbol to disk (bars stay out of context).
Requires a **chart tab open** (open one with `tab_new`/`tv_launch` if needed).

⚠️ **`out_path` MUST be an ABSOLUTE path to *this skill's* `out/` dir** — the MCP tool
resolves relative paths against the MCP server's cwd (the **repo root**), not the
skill dir, so a bare `out/bars.json` silently writes to `<repo>/out/bars.json` while
`rank` reads the skill's `out/bars.json` — and you'd rank **stale bars from a prior
day without any error**. Always write and read the same absolute file:
```
data_get_ohlcv_batch(symbols=[...all shortlist syms...], timeframe="D", count=60,
    out_path="<skill base dir>/out/bars.json")   # the "Base directory for this skill" path
```
Then rank against **that same absolute bars file** (`--scan` picks up the screen
title/date/funnel and the report path):
```
python3 .claude/skills/qmomentum-scan/qmomentum.py rank \
    --bars <skill base dir>/out/bars.json --scan out/qmomentum_<screen>.json --outdir out
```
Sanity-check before trusting the rank: the ranked names must be a subset of the
Step-1 shortlist. If you see names that weren't in the shortlist, you ranked stale
bars — re-point `--bars` at the freshly-written absolute file and re-run.
`rank` writes two files and prints a one-line confirmation (never the table):
- `out/refined.json` — full ranking: `ranked` (best-first) + `dropped` (bar-stage,
  each with a `drop_reason`) + `col_dropped` (column pre-filter cuts) + `errors`.
  Also feeds the **tv-orb-alerts** skill (top-N by `score`).
- `out/qmomentum_<screen>_<date>.md` — the ranked table **plus a full "Dropped / not
  ranked" ledger** (every scanned name that didn't rank, with its reason — bar-stage
  drops, column pre-filter cuts, and insufficient-bars), folded into a
  `<!-- rank-table:start -->…<!-- rank-table:end -->` block (a re-run replaces only
  that block; the model's `## Read` below it is preserved).

## Then you (the model) author the read
The table is numbers; the interpretation is the deliverable. After `rank`, read
`out/refined.json` and write a short qualitative summary **below the table block**
(after `<!-- rank-table:end -->`, under a `## Read` heading — a re-run won't touch
it): group the standout **🟢 cleanest coils**, flag **⚠️ watch-outs**, and call out
what the numbers can't (gaps, news, sector clustering). Keep it scannable.

**Screenshots are optional and OFF by default** — capture the top 2–3 via
`capture_screenshot` only if the user asks or a name looks borderline.

## Output for the user
Don't re-transcribe the table. In chat give the same short scannable read — the
standout coils, anything borderline, and the funnel counts (universe → shortlist →
ranked, plus what dropped) — and point the user at the dated report for the table.

## File conventions
- **Dated, kept:** `out/qmomentum_<screen>_<date>.md` — one per day (the history).
  Don't overwrite prior dates.
- **Stable, overwritten every run:** `out/qmomentum_<screen>.json` (shortlist),
  `out/bars.json` (bars), `out/refined.json` (ranking).

## Notes / gotchas
- `scan` needs **no auth** and works with TradingView closed. Only `screener_capture`
  (adding/syncing a screen) and the bar fetch need the running MCP + app.
- Run after the close (or near it) — the scanner's snapshot fields update intraday.
- `data_get_ohlcv_batch` drives the open chart and restores it; it reads the internal
  bar model, not the gated `exportData`.
- **breakout-scan** is the sibling scan on the same base (a coil AT the 3M high). To
  add a new scan, subclass `ScreenerRanking` — see `_lib/screener_ranking.py`.
