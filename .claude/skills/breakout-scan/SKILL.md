---
name: breakout-scan
description: Daily scan for stocks where a volume-led candle closed through the 3-month high and every close since has held above that level, ranked by how long the break has been sustained and how calmly it's holding — driven by the user's own TradingView screens (any market). This is the SIBLING of qmomentum-scan and its mirror image: use this one for names whose pivot is already TAKEN and holding; use qmomentum-scan for names still UNDER the high, coiled in a flag. Trigger on "run the breakout scan", "what broke out", "which names broke their 3-month high", "find confirmed breakouts", "what's holding its breakout".
---

# 3M-High Breakout Scan

Takes the user's own TradingView screen and answers one binary question per name:
**did a volume-led candle close through its 3-month high, and has every close since
held above that level?** Survivors are then ranked on two things only. Output is that ranked table plus a full ledger of why
everything else didn't confirm. **The screen is the filter; the script makes the
call.**

All thresholds and judgement live in the code — `class Breakout` in `breakout.py`
(a subclass of the shared `../_lib/screener_ranking.py` base, also used by
`qmomentum-scan`). **This doc is only how to run it.** To change *what* counts as
a breakout, edit the identification constants (`BO_SEARCH_WINDOW`, `MIN_BO_VOL_X`,
`MIN_BO_CLOSE_POS`, `PIVOT_LOOKBACK`) and the gate constants (`MIN_SUSTAIN`,
`HOLD_TOL_ADR`, `MAX_EXT_ADR`) in `_find_break` / `disqualify`; to
change the ranking, edit the `DAYS_*` / `CHOP_*` constants and `readiness`. Not
this file.

## Identification — the candle first, then the level
This order is the whole design. A breakout is an event with a signature, so
`_find_break` looks for **the candle**, then takes whatever level it broke:

1. volume ≥ **1.5×** the average of the 20 bars before it (`MIN_BO_VOL_X`),
2. closes in the **upper half of its own range** (`MIN_BO_CLOSE_POS`) — a bar that
   spikes through and closes near its low is a rejection, not a breakout,
3. closes **above the highest high of the 60 bars before it** — and *that* is the
   level (`Pivot`).

Because the level comes from bars strictly before the candle, **the move can never
set its own resistance**. Searched most-recent-first over the last **10 sessions**
(`BO_SEARCH_WINDOW`), which doubles as the freshness limit — nothing older can be
found, so there is no `MAX_AGE` and no "stale" bucket.

## The gate — sustaining it
Then, in `disqualify`:

4. **Every close since** the candle has held at/above the level, tolerating a dip of
   up to **0.3 ADR** (rounding noise, not a failed breakout).
5. The **latest** close is at/above the level — sustaining a level you're currently
   trading under isn't sustaining it.
6. At least **2 sessions** have closed above it (`MIN_SUSTAIN`); 0–1 is tomorrow's
   candidate, kept in the ledger.
7. Price is **no more than 3 ADR past** the level (`MAX_EXT_ADR`, farther = chased).

Gate 7 matters: identification finds the most recent qualifying break rather than
the move's origin, so a name deep into a long advance can show a fresh `d+2` off
its newest level. Extension is the guard against entering one of those.

## The rank — two terms, 50/50
Applied to the survivors, in `readiness`:

- **`d+` sessions sustained (50 pts)** — a plateau: **full marks across 3-8
  sessions**, the sweet spot. Below is premature (d+2 scores half), above ramps to
  zero by 11. `BO_SEARCH_WINDOW = 10` bounds `d+` at 10, so the top of the ramp is
  only partly reachable by construction — that's intentional, not a mis-set
  constant: the search window and the staleness notion are the same idea.
- **`Chop` (50 pts)** — mean absolute daily close-to-close move since the break,
  in ADR units. Lower = holding calmly; erratic post-break action is a warning.
  Full marks at ≤0.12, zero by 0.60 (calibrated on the live universe: p25 0.25,
  median 0.37, p75 0.55, so the median name lands near half marks).

Both are continuous ramps, per the base's doctrine — a name one day outside the
sweet spot scores slightly lower, it doesn't fall off a cliff.

`BoVol` is a **gate input** (minimum 1.5x), not a score term — it decides whether a
candle counts as a breakout at all. Shown as evidence but not scored: `BoRng`,
`VolHold`, and extension past the level (`Past%`, `PastADR`). Extension is already a
hard gate, and a binary belongs in `disqualify` rather than the score.

**Chop is measured only on the bars AFTER the breakout candle** — the candle is
supposed to be wide, so including it would penalise thrust.

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
closed), writes the universe to `out/breakout_<screen>.json` (stable,
overwritten), and lists the exact `EXCHANGE:SYM` symbols on stderr.

**There is no column pre-filter, by design** — whether a pivot was taken and held
is invisible in any snapshot column, so the whole universe goes to the bar stage.
Expect shortlist == universe; that is correct, not a bug.

### Step 2 — pull bars, then `rank` (the call)
Pull daily bars for **every** shortlist symbol to disk (bars stay out of context).
Requires a **chart tab open** (open one with `tab_new`/`tv_launch` if needed).

⚠️ **`count` must be ≥ 150.** The level needs 60 bars of history behind a candle
that may itself be 10 sessions back, plus 20 more for the pre-break volume and ADR
baselines. Short fetches silently push names into "insufficient bars".

⚠️ **`out_path` MUST be an ABSOLUTE path to *this skill's* `out/` dir** — the MCP
tool resolves relative paths against the MCP server's cwd (the **repo root**), not
the skill dir, so a bare `out/bars.json` writes to `<repo>/out/bars.json` while
`rank` reads the skill's `out/bars.json` — and you'd scan **stale bars from a prior
day with no error**:
```
data_get_ohlcv_batch(symbols=[...all shortlist syms...], timeframe="D", count=150,
    out_path="<skill base dir>/out/bars.json")   # the "Base directory for this skill" path
```
Then run the gate against **that same absolute bars file** (`--scan` picks up the
screen title/date/funnel and the report path):
```
python3 .claude/skills/breakout-scan/breakout.py rank \
    --bars <skill base dir>/out/bars.json --scan out/breakout_<screen>.json --outdir out
```
(The subcommand is called `rank` because that's the shared base's name for the bar
stage; it runs both the gate and the ranking.)

`rank` writes two files and prints a one-line confirmation (never the table):
- `out/refined.json` — `ranked` (the confirmed breakouts) + `dropped` (everything
  that failed the gate, each with its `drop_reason`) + `errors`.
- `out/breakout_<screen>_<date>.md` — the confirmed table **plus the full
  "Dropped / not ranked" ledger**, folded into a
  `<!-- rank-table:start -->…<!-- rank-table:end -->` block (a re-run replaces
  only that block; the model's `## Read` below it is preserved).

## The drop ledger is half the output
Don't skim past it. The buckets that matter day to day:
- **"not yet sustained"** — the volume candle fired today or yesterday. These are
  *tomorrow's* candidates; name them for the user explicitly. The ranking also
  discounts d+2 to half marks, so a fresh name climbs the table as it proves itself.
- **"closed back under the pivot" / "back under the pivot now"** — failed breakouts.
  A day where most names lose the level is a market signal, not a stock signal.
- **"no volume-led break ... (x% under it)"** — the largest bucket, and the
  pre-breakout watchlist. The distance is to the level a candle would have to clear
  today, so sub-1% entries are effectively sitting on the level. The wording avoids
  naming volume, which is usually not the failing condition — the close is.
- **"above the 3M high but no 1.5x-volume break"** — drifted up without a thrust, or
  broke earlier than the 10-session window. Not a confirmed breakout either way.

## Then you (the model) author the read
The table is numbers; the interpretation is the deliverable. After `rank`, read
`out/refined.json` and write a short qualitative summary **below the table block**
(after `<!-- rank-table:end -->`, under a `## Read` heading — a re-run won't touch
it): the top of the rank and why, **⚠️ watch-outs** that the two score terms
deliberately don't catch — extended past the pivot (`PastADR` near 3), no volume
behind the break (`BoVol` ≈ 1), gap/event-driven moves (a 20x+ `BoVol` spike is
news, not a technical breakout) — the names breaking out today to watch tomorrow,
and what the numbers can't see (news, sector clustering). Keep it scannable.

**Screenshots are optional and OFF by default** — capture the top 2–3 via
`capture_screenshot` only if the user asks or a name looks borderline.

## Output for the user
Don't re-transcribe the table. In chat give the same short scannable read — the
cleanest breaks, anything borderline, the "breaking out today" names, and the
funnel counts (universe → confirmed, plus what failed and why) — and point the
user at the dated report for the table.

## Before adding a third score term
**Don't score consolidation.** Tightness, base depth and volume dry-up invert after
a break: a real breakout is a wide-range, high-volume bar, so rewarding
quiet-and-narrow across one selects for breakouts with no thrust behind them. The
pre-break base belongs to the pre-break scan (qmomentum).

If a third term is wanted, thrust is the candidate with the most support and it's
already computed — but validate it across several days of dated reports before
giving it weight.

## File conventions
- **Dated, kept:** `out/breakout_<screen>_<date>.md` — one per day (the history).
  Don't overwrite prior dates.
- **Stable, overwritten every run:** `out/breakout_<screen>.json` (universe),
  `out/bars.json` (bars), `out/refined.json` (results).

## Notes / gotchas
- `scan` needs **no auth** and works with TradingView closed. Only
  `screener_capture` (adding/syncing a screen) and the bar fetch need the running
  MCP + app.
- Run after the close (or near it) — the scanner's snapshot fields update intraday,
  and an intraday close can flip the "latest close above the pivot" test.
- `data_get_ohlcv_batch` drives the open chart and restores it; it reads the
  internal bar model, not the gated `exportData`.
- **qmomentum-scan** is the mirror-image sibling on the same base: it wants the
  high in the PAST (price pulled back under its pivot into a flag), this one wants
  the pivot already taken. Their core signals are opposites — tight and quiet
  before the break, wide and loud after — which is why they can't share judgement.
  To add a new scan, subclass `ScreenerRanking` — see `_lib/screener_ranking.py`.
