#!/usr/bin/env python3
"""
Qullamaggie Momentum Scanner — a `ScreenerRanking` scan.

Finds continuation/breakout setups à la Kristjan Kullamägi: a stock that ran up
hard (flagpole), pulled BACK below its high into an orderly, tightening 2-8 week
consolidation (higher lows), is surfing a rising, stacked 10/20/50 SMA, and is now
coiled in a quiet, tight range — about to break out on range expansion.

The whole pipeline (fetch -> eliminate -> score -> report, and the CLI) lives in
`screener_ranking.ScreenerRanking`; this file only supplies the Qullamaggie
judgement:
  * eliminate_losses (columns): drop below-50SMA / stretched >1 ADR from the 10SMA.
  * score (bars): the flag whose high is in the PAST — pole from the swing high,
    consolidation length since it, pullback depth, and a breakout trigger at the
    top of the flag off the pullback low. Scored on setup quality via continuous
    ramps, led by volume dry-up and flag position: a coil is defined by supply
    drying up and price holding the top of the flag, NOT by narrowness alone (a
    quiet bleed is narrow too). Trigger distance is a hard gate, not a score input
    (rewarding proximity ranked premature-trigger names highest).

  python3 qmomentum.py scan --screen screens/<s>.json --outdir out
  <model> data_get_ohlcv_batch(symbols=shortlist) -> out/bars.json
  python3 qmomentum.py rank --bars out/bars.json --scan out/qmomentum_<s>.json
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from screener_ranking import ScreenerRanking, pct


class QMomentum(ScreenerRanking):
    NAME = "Qullamaggie Momentum"
    FILE_PREFIX = "qmomentum"
    COLUMNS = ["name", "close", "change", "Perf.1M", "Perf.3M",
               "SMA10", "SMA20", "SMA50", "High.1M", "ADRP"]
    DEFAULT_SORT = {"sortBy": "Perf.1M", "sortOrder": "desc"}
    SCORE_BLURB = ("Ranked by `score` — bar-derived setup quality on continuous ramps: "
                   "`VolDry` (26) > `FlagPos` (22) > tightness (`5d/ADR` 20) "
                   "> `Pull%` (12) > `Consol` length (12) > `Pole%` (8). Trigger distance "
                   "and 10/20/50 SMA stacking are gates, not score inputs — a hard binary "
                   "belongs in `disqualify`. `VolDry` = recent volume ÷ its 20-day average "
                   "(lower = drier); `FlagPos` = where the close sits in the flag's own "
                   "range (1 = at the flag high). Every metric column is a score input — "
                   "headers are the metric keys in `refined.json`. This is the sole ranking.")

    MAX_S10_DIST_ADR = 1.0   # a coil must hug the 10-day SMA; farther = extended/broken
    MAX_TRIG_DIST_ADR = 1.0  # the breakout trigger must be reachable in one session
    MIN_TRIG_DIST_ADR = 0.5  # ...but no closer than this, or the buy-stop fires on open
                             # noise (premature trigger) instead of a real expansion move
    MAX_PULLBACK_PCT = 35.0  # deeper than this = the base has failed, not a flag
    MAX_LOWER_CLOSES = 3     # drop names still falling: >= this many consecutive lower
    MIN_CONSOL_DAYS = 3      # a flag needs its high in the PAST. At 0-2 bars since the
                             # swing high there is no coil to break out of: the name is
                             # printing new highs (a breakout-scan setup, not this one)
                             # and its "trigger" would be the synthetic MIN_TRIG floor.

    # -- column gate ---------------------------------------------------------

    def annotate(self, r):
        h1, close, s10, adrp = r.get("h1"), r.get("close"), r.get("s10"), r.get("adrp")
        r["off_high_1m"] = round((h1 - close) / h1 * 100, 1) if (h1 and close) else None
        ext = pct(close, s10) if (close and s10) else None
        r["ext_s10_adr"] = round(ext / adrp, 2) if (ext is not None and adrp) else None

    def is_loser(self, r):
        close, s50 = r.get("close"), r.get("s50")
        if not close or not s50:
            return None  # can't judge on columns -> defer to the bar stage
        if close < s50:
            return "below 50SMA"
        e = r.get("ext_s10_adr")
        if e is not None and abs(e) > self.MAX_S10_DIST_ADR:
            return (f"extended {e:+.1f} ADR from 10SMA" if e > 0
                    else f"broken down {e:+.1f} ADR below 10SMA")
        return None

    # -- bar metrics ---------------------------------------------------------
    def bar_metrics(self, sym, bars):
        """Flag geometry off the daily bars."""
        if not bars or len(bars) < self.MIN_BARS:
            return {"sym": sym, "error": "insufficient bars"}
        h, l, c, v = self.ohlcv(bars)
        n = len(bars)

        adr = self.adr20(h, l, c)
        hi_i = max(range(max(0, n - 60), n), key=lambda i: c[i])   # swing high on closes
        lo_before = min(c[max(0, hi_i - 40):hi_i + 1]) if hi_i > 0 else c[hi_i]
        pole = (c[hi_i] - lo_before) / lo_before * 100 if lo_before else 0
        consol_len = n - 1 - hi_i                                  # bars since the highest close
        since_low = min(c[hi_i:]) if hi_i < n else c[-1]
        pullback = (c[hi_i] - since_low) / c[hi_i] * 100           # deepest closing drawdown

        lo_i = min(range(hi_i, n), key=lambda i: l[i])             # pullback low after the high
        trigger = max(h[lo_i:])                                    # top of the flag off that low
        if adr:                                                    # floored MIN_TRIG_DIST_ADR up
            trigger = max(trigger, c[-1] * (1 + self.MIN_TRIG_DIST_ADR * adr / 100))

        lower_closes = 0                                 # consecutive lower closes from the end
        for i in range(n - 1, 0, -1):
            if c[i] < c[i - 1]:
                lower_closes += 1
            else:
                break
        trig_dist_adr = (trigger - c[-1]) / c[-1] / (adr / 100) if adr else None

        rng5 = (max(h[-5:]) - min(l[-5:])) / c[-1] * 100
        tight = rng5 / adr if adr else None                        # 5-day envelope as x ADR
        vavg = sum(v[-20:]) / 20
        vd5 = (sum(v[-5:]) / 5) / vavg if vavg else None
        vd3 = (sum(v[-3:]) / 3) / vavg if vavg else None
        vdry = min(vd5, vd3) if (vd5 is not None and vd3 is not None) else vd5

        seg_h, seg_l = max(h[hi_i:]), min(l[hi_i:])                # the flag's own range
        flag_pos = (c[-1] - seg_l) / (seg_h - seg_l) if seg_h > seg_l else 0.5
        stacked = self.sma(c, 10) > self.sma(c, 20) > self.sma(c, 50)

        return {
            "sym": sym, "close": round(c[-1], 2), "adr_pct": round(adr, 2),
            "pole_pct": round(pole, 1), "consol_days": consol_len,
            "pullback_pct": round(pullback, 1),
            "range5_over_adr": round(tight, 2) if tight else None,
            "vol_dryup": round(vdry, 2) if vdry else None,
            "vol_dryup_5d": round(vd5, 2) if vd5 else None,
            "flag_pos": round(flag_pos, 2),
            "mas_stacked": stacked, "trigger": round(trigger, 2),
            "trig_dist_adr": round(trig_dist_adr, 2) if trig_dist_adr is not None else None,
            "lower_closes": lower_closes,
        }

    def disqualify(self, m):
        if m["consol_days"] < self.MIN_CONSOL_DAYS:
            return (f"no flag yet ({m['consol_days']}d since the high) — "
                    "at/near highs, see breakout-scan")
        if not m["mas_stacked"]:
            return "10/20/50 SMA not stacked"
        if m["pullback_pct"] > self.MAX_PULLBACK_PCT:
            return f"pullback {m['pullback_pct']:.0f}% (base failed)"
        if m["lower_closes"] >= self.MAX_LOWER_CLOSES:
            return f"{m['lower_closes']} straight lower closes (still falling)"
        if m["trig_dist_adr"] is not None and m["trig_dist_adr"] > self.MAX_TRIG_DIST_ADR:
            return f"trigger {m['trig_dist_adr']:.1f} ADR overhead"
        return None

    # -- score ---------------------------------------------------------------

    def readiness(self, m):
        s = 0.0
        # supply exhaustion — the drier of the 5/3-bar windows
        s += self.ramp(m["vol_dryup"], 0.20, 1.00, 26)
        # coil tightness: the 5-day envelope as a multiple of ADR. Deliberately the
        # only tightness term — mean bar range is bounded above by the envelope
        # (envelope >= largest bar >= mean bar), so scoring it too charges twice
        # for the same looseness and moved nothing in the top of the ranking.
        s += self.ramp(m["range5_over_adr"], 1.3, 3.2, 20)
        # closing strength: where the close sits in the flag's own range
        s += self.ramp(m["flag_pos"], 0.80, 0.20, 22)
        # pullback depth — SHALLOWER IS BETTER (ramp takes best first):
        # full credit at <=5%, zero by 28%. Independent of consol_days, which
        # says the high is in the past but nothing about whether the flag held.
        s += self.ramp(m["pullback_pct"], 5.0, 28.0, 12)
        # consolidation length: 5-20 bars is the sweet spot
        s += self.plateau(m["consol_days"], 1, 5, 20, 45, 12)
        # pole: a real thrust to continue, not a moonshot to fade
        s += self.plateau(m["pole_pct"], 12, 30, 150, 260, 8)
        return round(min(s, 100))

    # -- report table --------------------------------------------------------

    def render_table(self, ranked):
        # Column headers are the metric keys, so a number in the report can be traced
        # straight to the field in refined.json and the term in readiness().
        out = ["| # | Symbol | Score | Close | Trigger | Δtrig | Stop~ | ADR% | Pole% | "
               "Consol | Pull% | 5d/ADR | VolDry | FlagPos |",
               "|--:|--------|------:|------:|--------:|------:|------:|-----:|------:|"
               "-------:|------:|-------:|-------:|--------:|"]
        for i, r in enumerate(ranked, 1):
            adr = r.get("adr_pct"); trig = r.get("trigger"); dist = r.get("trig_dist_adr")
            stop = round(trig * (1 - adr / 100), 2) if (trig and adr) else None  # ~1 ADR below entry
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                i, r["sym"].split(":")[-1], r["score"], r.get("close"), trig,
                "—" if dist is None else f"{dist:.1f}", stop if stop is not None else "—",
                adr, r.get("pole_pct"), r.get("consol_days"), r.get("pullback_pct"),
                r.get("range5_over_adr"), r.get("vol_dryup"), r.get("flag_pos")))
        return "\n".join(out)


if __name__ == "__main__":
    QMomentum.main()
