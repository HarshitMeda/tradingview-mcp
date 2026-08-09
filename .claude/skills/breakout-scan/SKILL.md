---
name: breakout-scan
description: Daily base-breakout scanner for swing setups coiled AT a 3-month high, about to break out to new highs — driven by the user's own TradingView screens (any market). Covers both the long flat base and the high-tight-flag (sharp advance + short tight coil at the highs). This is the SIBLING of qmomentum-scan: use this one for names sitting ON the high; use qmomentum-scan for names that pulled BACK below the high into a flag. Trigger on "run the breakout scan", "base breakout scan", "what's coiled at the highs", "find breakout-ready names near highs", "scan for stocks about to break out".
---

# Base-Breakout Scan

Ranks the user's own TradingView screen for names coiled **at a 3-month high,
about to break out to new highs** (both the flat base and the high-tight-flag).
**The screen is the filter; the script does the ranking.**

All scoring, filtering, and threshold logic lives in the code — `class Breakout`
in `breakout.py` (a subclass of the shared `../_lib/screener_ranking.py` base,
also used by `qmomentum-scan`). **This doc is only how to run it.** To change
*what* it selects or how it ranks, edit the class (its constants + `is_loser` /
`bar_metrics` / `disqualify` / `readiness` / `render_table`), not this file.

## Screens — the source of truth (`screens/*.json`)
Each screen file holds a screener's exact captured filter (`market`, `filter`,
`filter2`, `sort`) so the universe matches what the user sees in TradingView. The
default is **`3m_high_in.json`**. Works for any market (India, US `america`, …).

**Add or re-sync a screen** (once per screen, and again whenever the user edits
its filters in TradingView):
1. Have TradingView open on that screen, OR know its share URL.
2. Call the **`screener_capture`** MCP tool (pass `url` for a specific screen, or
   omit to grab the currently-open screener).
3. Save that output to a JSON file and run:
   `python3 .../breakout.py add-screen --from-capture <capture.json> --name "<name>"`
   → writes `screens/<slug>.json` (re-running overwrites).

## How to run — two steps, bridged by a bar fetch
Python can't call the TradingView MCP, so you pull the bars yourself between the
two script calls.

### Step 1 — `scan` → shortlist (fast, no app needed)
```
python3 .claude/skills/breakout-scan/breakout.py scan --screen screens/3m_high_in.json --outdir out
# every screen at once:  … scan-all --screens screens --outdir out
```
Replays the screen against the public scanner (no auth, works with the app
closed), writes the survivors to `out/breakout_<screen>.json` (stable,
overwritten), prints the shortlist, and lists the exact `EXCHANGE:SYM` symbols to
pull bars for on stderr.

### Step 2 — pull bars, then `rank` (the ranking)
Pull daily bars for **every** shortlist symbol to disk (bars stay out of
context). Requires a **chart tab open** (open one with `tab_new`/`tv_launch` if
needed):
```
data_get_ohlcv_batch(symbols=[...all shortlist syms...], timeframe="D",
                     count=60, out_path="out/bars.json")
```
Then rank (pass `--scan` so it picks up the screen title/date/funnel and the
report path):
```
python3 .claude/skills/breakout-scan/breakout.py rank \
    --bars out/bars.json --scan out/breakout_<screen>.json --outdir out
```
`rank` writes two files and prints a one-line confirmation (never the table):
- `out/refined.json` — full ranking: `ranked` (best-first) + `dropped` (each with
  a `drop_reason`) + `errors`.
- `out/breakout_<screen>_<date>.md` — the ranked table, folded into a
  `<!-- rank-table:start -->…<!-- rank-table:end -->` block (a re-run replaces
  only that block).

## Then you (the model) author the read
The table is numbers; the interpretation is the deliverable. After `rank`, read
`out/refined.json` and write a short qualitative summary **below the table block**
(after `<!-- rank-table:end -->`, under a `## Read` heading — a re-run won't touch
it): group the standout **🟢 cleanest coils**, flag **⚠️ watch-outs**
(wide/loose coils, already triggered/extended, thin pole), and call out what the
numbers can't (gaps, news, sector clustering). Keep it scannable.

## Output for the user
Don't re-transcribe the table. In chat give the same short scannable read — the
standout coils, anything borderline, and the funnel counts (universe → shortlist
→ ranked, plus what dropped) — and point the user at the dated report for the table.

## File conventions
- **Dated, kept:** `out/breakout_<screen>_<date>.md` — one per day (the history).
  Don't overwrite prior dates.
- **Stable, overwritten every run:** `out/breakout_<screen>.json` (shortlist),
  `out/bars.json` (bars), `out/refined.json` (ranking).

## Notes / gotchas
- `scan` needs **no auth** and works with TradingView closed. Only
  `screener_capture` (adding/syncing a screen) and the bar fetch need the running
  MCP + app.
- Run after the close (or near it) — the scanner's snapshot fields update intraday.
- `data_get_ohlcv_batch` drives the open chart and restores it; it reads the
  internal bar model, not the gated `exportData`.
- **qmomentum-scan** is the sibling scan on the same base (a coil that pulled
  BACK below the high into a flag). To add a new scan, subclass `ScreenerRanking`
  — see `_lib/screener_ranking.py`.
