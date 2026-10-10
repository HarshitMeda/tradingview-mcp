#!/usr/bin/env python3
"""
3M-High Breakout Scanner — a `ScreenerRanking` scan.

A binary gate plus a deliberately narrow rank.

The gate (`disqualify`): did a volume-led candle close above the 3-month
resistance, and has every close since held above it?

The resistance (`_resistance`) is the highest close in the 60-bar frame EXCLUDING
the recent 10 sessions. Removing those stops the breaking move being part of the
level it has to clear, and makes every level at least 10 sessions old without a
separate rule. Wicks are ignored: closes, matching the sustain test.

The breakout candle (`_find_break`) is the highest-volume bar in those recent 10
sessions that closed above the resistance.

The rank (`readiness`) orders survivors on two terms, 50/50: sessions sustained
(3-8 is the sweet spot) and how calmly the level has held (`daily_chop_adr`).

Consolidation is not measured. Tightness, base depth and volume dry-up invert
after a break — a real breakout is a wide-range, high-volume bar, so scoring
quiet-and-narrow across one rewards breakouts with no thrust behind them. The
pre-break base belongs to the pre-break scan (qmomentum).

THE SIBLING of qmomentum-scan, and the two are mirror images on purpose:
qmomentum wants the high in the PAST (price pulled back into a flag, still under
its pivot); this one wants the level ALREADY TAKEN and holding.

  python3 breakout.py scan --screen screens/<s>.json --outdir out
  <model> data_get_ohlcv_batch(symbols=shortlist, count=150) -> out/bars.json
  python3 breakout.py rank --bars out/bars.json --scan out/breakout_<s>.json
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from screener_ranking import ScreenerRanking


class Breakout(ScreenerRanking):
    NAME = "3M-High Breakout"
    FILE_PREFIX = "breakout"
    REPORT_HEADING = "Confirmed breakouts"
    RESULT_NOUN = "ranked"
    COLUMNS = ["name", "close", "change", "Perf.1M", "Perf.3M",
               "SMA50", "High.3M", "ADRP"]
    DEFAULT_SORT = {"sortBy": "market_cap_basic", "sortOrder": "desc"}
    SCORE_BLURB = (
        "Every name listed had a **volume-led candle close above its 3-month "
        "resistance**, and has held above that level on every close since (the gate). "
        "`Pivot` is that resistance — the highest CLOSE in the 60-bar frame **excluding the "
        "recent 10 sessions**, so the breaking move can never be part of the level it "
        "has to clear, and every level is at least 10 sessions old. Wicks are ignored throughout, matching the close-based sustain "
        "test. `LvlAge` is how many sessions ago the level was set; `BoVol` is the "
        "breaking candle's volume vs the 20 days before it (a gate input, minimum 1.5x; "
        "where several candles qualify, the highest-volume one is the break). "
        "`score` then ranks them on exactly two things, 50/50 — **`d+`** sessions "
        "sustained (3-8 is the sweet spot: below is premature, above is going stale) "
        "and **`Chop`**, the mean absolute daily move since the break in ADR units "
        "(lower = holding calmly; erratic action is a warning). Both are continuous "
        "ramps, so a name just outside the sweet spot scores slightly lower rather "
        "than falling off a cliff. "
        "Everything else is evidence, shown but NOT scored: `Past%`/`PastADR` how far "
        "above the level price now sits (capped at 3 ADR by the gate — the guard "
        "against a name deep into a long advance looking fresh off its newest level), "
        "`BoRng` the breaking candle's range, `VolHold` average volume since the break "
        "vs the 20 days before it, `BoLow` that candle's low (the tighter alternative "
        "stop; `Pivot` is the natural one). "
        "Headers are the metric keys in `refined.json`. Everything that did NOT "
        "confirm is in the **Dropped** ledger with its reason — names 'not yet "
        "sustained' are tomorrow's candidates.")

    MIN_BARS = 61         # the 60-bar frame + 1

    # -- the resistance, and the candle that broke it -------------------------
    # Two complementary windows over the same 60-bar frame, both anchored to today:
    # the recent BO_SEARCH_WINDOW sessions are searched for the candle, the rest of
    # the frame supplies the resistance. Splitting it this way stops the breaking move
    # being part of the level it has to clear, and makes every level at least
    # BO_SEARCH_WINDOW sessions old for free.
    PIVOT_LOOKBACK = 60    # the 3-month frame. Resistance = highest CLOSE in it,
                           # outside the recent window; closes not highs, matching
                           # the close-based sustain test.
    BO_SEARCH_WINDOW = 10  # recent sessions searched for the candle and excluded from
                           # the resistance. Also the freshness limit, and it bounds
                           # days_since.
    MIN_BO_VOL_X = 1.5     # candle volume vs the 20 bars before it; highest wins

    MIN_SUSTAIN = 2       # closes above the level after the candle; 0-1 = tomorrow's
    HOLD_TOL_ADR = 0.3    # a close may dip this far under and still count as holding
    MAX_EXT_ADR = 3.0     # farther past the level than this = chased, not entry-able

    # -- ranking: two components, 50/50 (no evidence to prefer either) --------
    DAYS_PTS, CHOP_PTS = 50, 50
    DAYS_ZERO_LO, DAYS_FULL_LO = 1, 3     # d+2 scores half; d+1 would score zero
    DAYS_FULL_HI, DAYS_ZERO_HI = 8, 11    # the 3-8 sweet spot, stale by 11
    CHOP_BEST, CHOP_WORST = 0.12, 0.60    # ADR/day; universe p25 .25 / med .37 / p75 .55

    # -- column gate ---------------------------------------------------------

    def is_loser(self, r):
        """No column gate, on purpose: the screen already does what a snapshot can,
        and whether a level was taken and held needs bars. The gate is `disqualify`."""
        return None

    # -- bar metrics ---------------------------------------------------------

    def _resistance(self, c, n):
        """The level: highest close of the PIVOT_LOOKBACK frame anchored to TODAY, with
        the recent BO_SEARCH_WINDOW sessions removed — so the move that is breaking out
        cannot be part of the level it has to clear. Returns (level, age_in_sessions)."""
        lo, hi = n - self.PIVOT_LOOKBACK, n - self.BO_SEARCH_WINDOW
        w = c[lo:hi]
        level = max(w)
        return level, n - 1 - (lo + len(w) - 1 - w[::-1].index(level))

    def _find_break(self, c, v, n, level):
        """The breakout candle: the highest-volume bar in the search window that closed
        above `level` on at least MIN_BO_VOL_X its prior 20-day average volume.
        Returns (index, vol_x) or (None, None)."""
        best = None
        for i in range(n - 1, n - self.BO_SEARCH_WINDOW - 1, -1):
            vbase = sum(v[i - 20:i]) / 20
            if not vbase:
                continue
            vol_x = v[i] / vbase
            if vol_x >= self.MIN_BO_VOL_X and c[i] > level \
                    and (best is None or vol_x > best[1]):
                best = (i, vol_x)
        return best if best else (None, None)

    def bar_metrics(self, sym, bars):
        """Did a volume-led candle clear the resistance, and has it held?"""
        if not bars or len(bars) < self.MIN_BARS:
            return {"sym": sym, "error": "insufficient bars"}
        h, l, c, v = self.ohlcv(bars)
        n = len(bars)
        adr = self.adr20(h, l, c)
        m = {"sym": sym, "close": round(c[-1], 2), "adr_pct": round(adr, 2)}

        pivot, lvl_age = self._resistance(c, n)
        bo_i, bo_vol_x = self._find_break(c, v, n, pivot)
        if bo_i is None:
            gap = (pivot - c[-1]) / pivot * 100
            m.update({"broke_out": False, "pivot": round(pivot, 2),
                      "level_age": lvl_age, "under_pivot_pct": round(gap, 2),
                      # Above the level with no qualifying candle: cleared it without
                      # volume, or before the search window. Needs its own reason,
                      # since "never cleared, -24.9% under" is self-contradictory.
                      "above_level_no_candle": gap < 0})
            return m

        days = n - 1 - bo_i                       # sessions since the breakout bar
        tol = pivot * (1 - self.HOLD_TOL_ADR * adr / 100)
        post = c[bo_i:]                           # breakout close onwards
        lost = min(post) < tol
        vbase = sum(v[bo_i - 20:bo_i]) / 20       # the 20 days BEFORE the break
        # ADR as it stood going INTO the break — the post-break bars are exactly the
        # expansion we're measuring, so including them would deflate the thrust.
        adr_pre = sum((h[i] - l[i]) / c[i]
                      for i in range(bo_i - 20, bo_i)) / 20 * 100

        m.update({
            "broke_out": True, "pivot": round(pivot, 2), "days_since": days,
            # "held" tolerates a noise-level dip on any close since the break;
            # "now_above" is the stricter end-state test — sustaining a level you
            # are currently trading under isn't sustaining it.
            "held": not lost, "now_above": c[-1] >= pivot,
            # how far the WORST close since the break sat relative to the pivot
            "worst_close_vs_pivot_pct": round((min(post) - pivot) / pivot * 100, 2),
            "past_pivot_pct": round((c[-1] - pivot) / pivot * 100, 2),
            "past_pivot_adr": round((c[-1] - pivot) / pivot * 100 / adr, 2) if adr else None,
            # thrust of the breakout bar itself — displayed as evidence, NOT scored
            "bo_range_adr": round((h[bo_i] - l[bo_i]) / c[bo_i] * 100 / adr_pre, 2)
                            if adr_pre else None,
            "bo_vol_x": round(bo_vol_x, 2),        # a gate input, not just evidence
            "level_age": lvl_age,                  # sessions the level stood before it broke
            "bo_low": round(l[bo_i], 2),
            "vol_hold_x": round((sum(v[bo_i:]) / len(post)) / vbase, 2) if vbase else None,
            "daily_chop_adr": self._chop(c, bo_i, adr),
        })
        return m

    @staticmethod
    def _chop(c, bo_i, adr):
        """Mean absolute daily close-to-close move since the break, in ADR units —
        the rank's 'erratic' term. Lower = holding calmly. Bars AFTER the candle
        only: the candle is supposed to be wide, so including it penalises thrust.
        Mean-absolute rather than stdev, which at two post-break bars measures
        sample size more than dispersion."""
        rets = [abs((c[i] - c[i - 1]) / c[i - 1]) * 100 for i in range(bo_i + 1, len(c))]
        if not rets or not adr:
            return None
        return round(sum(rets) / len(rets) / adr, 2)

    def disqualify(self, m):
        """The whole scan. Ordered so the ledger reads usefully: the most
        informative reason wins when several apply."""
        if not m.get("broke_out"):
            w, vx = self.BO_SEARCH_WINDOW, self.MIN_BO_VOL_X
            if m.get("above_level_no_candle"):
                return (f"above the 3M high ({-m['under_pivot_pct']:.1f}%) but no "
                        f"{vx:g}x-volume break in {w} sessions — drifted up, or broke "
                        f"earlier than the window")
            # Not "no {vx}x-volume break": volume is usually not the failing
            # condition — the close is. The distance carries the information.
            return (f"no volume-led break of the 3M high in {w} sessions "
                    f"({m['under_pivot_pct']:.1f}% under it)")
        # No staleness branch — BO_SEARCH_WINDOW already caps days_since.
        if not m["held"]:
            return (f"closed back under the pivot "
                    f"({m['worst_close_vs_pivot_pct']:+.1f}%)")
        if not m["now_above"]:
            return f"back under the pivot now ({m['past_pivot_pct']:+.2f}%)"
        if m["days_since"] < self.MIN_SUSTAIN:
            return (f"broke out {m['days_since']}d ago — not yet sustained "
                    f"(needs {self.MIN_SUSTAIN})")
        if (m["past_pivot_adr"] is not None
                and m["past_pivot_adr"] > self.MAX_EXT_ADR):
            return f"{m['past_pivot_adr']:.1f} ADR past the pivot (extended)"
        return None

    # -- score ---------------------------------------------------------------

    def readiness(self, m):
        """Two components, 50/50: how long the break has been sustained (3-8
        sessions is the sweet spot — below is premature, above is going stale) and
        how calmly it has held it (erratic post-break action is a warning, not a
        feature). Both are continuous, per the base's ramp doctrine: a name one day
        outside the sweet spot should score slightly lower, not fall off a cliff.

        Thrust (`bo_vol_x`, `bo_range_adr`) and extension are computed and shown but
        not scored: volume is already a gate input, extension is already a hard gate,
        and a binary belongs in `disqualify` rather than the score."""
        s = self.plateau(m["days_since"], self.DAYS_ZERO_LO, self.DAYS_FULL_LO,
                         self.DAYS_FULL_HI, self.DAYS_ZERO_HI, self.DAYS_PTS)
        s += self.ramp(m["daily_chop_adr"], self.CHOP_BEST, self.CHOP_WORST,
                       self.CHOP_PTS)
        return round(min(s, 100))

    def sort_key(self, m):
        """Best score first; exact ties (both components flat) broken by the fresher
        break, so the order never depends on the bars file's key order."""
        return (-m["score"], m["days_since"])

    # -- report table --------------------------------------------------------

    def render_table(self, ranked):
        # Score inputs first (d+, Chop), then the evidence that is shown but not
        # scored — so a row can be traced to the two terms in readiness().
        out = ["| # | Symbol | Score | d+ | Chop | Close | Pivot | LvlAge | Past% | "
               "PastADR | BoRng | BoVol | VolHold | ADR% | BoLow |",
               "|--:|--------|------:|---:|-----:|------:|------:|-------:|------:|"
               "--------:|------:|------:|--------:|-----:|------:|"]
        for i, r in enumerate(ranked, 1):
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | "
                       "{} | {} | {} |".format(
                i, r["sym"].split(":")[-1], r["score"], f"+{r.get('days_since')}",
                r.get("daily_chop_adr"), r.get("close"), r.get("pivot"),
                r.get("level_age"),
                f"{r.get('past_pivot_pct')}%", r.get("past_pivot_adr"),
                r.get("bo_range_adr"), r.get("bo_vol_x"), r.get("vol_hold_x"),
                r.get("adr_pct"), r.get("bo_low")))
        return "\n".join(out)


if __name__ == "__main__":
    Breakout.main()
