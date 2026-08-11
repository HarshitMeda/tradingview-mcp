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
    top of the flag off the pullback low. Scored on setup quality — coil tightness
    dominates; pole strength is a minor input. Trigger distance is a hard gate, not
    a score input (rewarding proximity ranked premature-trigger names highest).

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
    SCORE_BLURB = ("Ranked by `score` — bar-derived setup quality (coil tightness "
                   "dominates; trigger distance is a gate, not scored). This is the sole ranking.")

    MAX_S10_DIST_ADR = 1.0   # a coil must hug the 10-day SMA; farther = extended/broken
    MAX_TRIG_DIST_ADR = 1.0  # the breakout trigger must be reachable in one session
    MIN_TRIG_DIST_ADR = 0.5  # ...but no closer than this, or the buy-stop fires on open
                             # noise (premature trigger) instead of a real expansion move
    MAX_PULLBACK_PCT = 35.0  # deeper than this = the base has failed, not a flag
    MAX_LOWER_CLOSES = 3     # drop names still falling: >= this many consecutive lower

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

        # Trigger = top of the flag that formed off the pullback low: the high since
        # the deepest low AFTER the swing-high close. Emulates buying the break of the
        # recent contraction (à la Qullamaggie). No special geometry guard is needed:
        # the only names whose trigger would "collapse" onto the last bar are ones still
        # pressing lower, and disqualify() drops those on the lower-close streak before
        # they can score.
        lo_i = min(range(hi_i, n), key=lambda i: l[i])
        trigger = max(h[lo_i:])
        # Floor the trigger a minimum distance above the close: a flag high sitting only
        # a fraction of an ADR overhead gets tagged by open-auction noise, filling the
        # buy-stop before any real breakout. Require at least MIN_TRIG_DIST_ADR of room.
        if adr:
            trigger = max(trigger, c[-1] * (1 + self.MIN_TRIG_DIST_ADR * adr / 100))

        lower_closes = 0                                 # consecutive lower closes from the end
        for i in range(n - 1, 0, -1):
            if c[i] < c[i - 1]:
                lower_closes += 1
            else:
                break
        trig_dist_adr = (trigger - c[-1]) / c[-1] / (adr / 100) if adr else None

        rng5 = (max(h[-5:]) - min(l[-5:])) / c[-1] * 100
        tight = rng5 / adr if adr else None                       # 5-day range as x ADR (lower=tighter)
        rolling_over = c[-1] < c[-2] < c[-3]
        hl = (min(c[-5:]) > min(c[-10:-5]) and not rolling_over) if n >= 10 else None
        vdry = (sum(v[-5:]) / 5) / (sum(v[-20:]) / 20) if sum(v[-20:]) else None
        stacked = self.sma(c, 10) > self.sma(c, 20) > self.sma(c, 50)

        return {
            "sym": sym, "close": round(c[-1], 2), "adr_pct": round(adr, 2),
            "pole_pct": round(pole, 1), "consol_days": consol_len,
            "pullback_pct": round(pullback, 1),
            "range5_over_adr": round(tight, 2) if tight else None,
            "higher_lows": hl, "vol_dryup": round(vdry, 2) if vdry else None,
            "mas_stacked": stacked, "trigger": round(trigger, 2),
            "trig_dist_adr": round(trig_dist_adr, 2) if trig_dist_adr is not None else None,
            "lower_closes": lower_closes,
        }

    def disqualify(self, m):
        if m["pullback_pct"] > self.MAX_PULLBACK_PCT:
            return f"pullback {m['pullback_pct']:.0f}% (base failed)"
        if m["lower_closes"] >= self.MAX_LOWER_CLOSES:
            return f"{m['lower_closes']} straight lower closes (still falling)"
        if m["trig_dist_adr"] is not None and m["trig_dist_adr"] > self.MAX_TRIG_DIST_ADR:
            return f"trigger {m['trig_dist_adr']:.1f} ADR overhead"
        return None

    # -- score ---------------------------------------------------------------

    def readiness(self, m):
        """Setup QUALITY only, scaled to 100. Coil TIGHTNESS (31) dominates; pole
        strength is a minor input (13). Trigger distance is NOT scored — it's a hard
        gate (MIN/MAX_TRIG_DIST_ADR) and a display column, not a quality signal.
        Rewarding proximity ranked premature-trigger names highest, so it's gone."""
        s = 0
        t = m["range5_over_adr"]
        if t is not None:
            if t <= 1.5: s += 31
            elif t <= 2.5: s += 20
            elif t <= 3.5: s += 8
        cl = m["consol_days"]
        if 5 <= cl <= 15: s += 19
        elif 16 <= cl <= 40: s += 13
        elif 3 <= cl <= 4: s += 8
        pb = m["pullback_pct"]
        if pb <= 15 and m["higher_lows"]: s += 19
        elif pb <= 25: s += 13
        elif pb <= 35: s += 5
        vd = m["vol_dryup"]
        if vd is not None:
            if vd < 0.7: s += 13
            elif vd < 1.0: s += 8
        p = m["pole_pct"]
        if 30 <= p <= 150: s += 13
        elif p > 150: s += 5
        elif 20 <= p < 30: s += 8
        if m["mas_stacked"]: s += 5
        return min(s, 100)

    # -- report table --------------------------------------------------------

    def render_table(self, ranked):
        out = ["| # | Symbol | Score | Close | Trigger | Δtrig | Stop~ | ADR% | Pole% | "
               "Consol | Pull% | 5d/ADR | HL | VolDry | Stacked |",
               "|--:|--------|------:|------:|--------:|------:|------:|-----:|------:|"
               "-------:|------:|-------:|:--:|-------:|:------:|"]
        for i, r in enumerate(ranked, 1):
            adr = r.get("adr_pct"); trig = r.get("trigger"); dist = r.get("trig_dist_adr")
            stop = round(trig * (1 - adr / 100), 2) if (trig and adr) else None  # ~1 ADR below entry
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                i, r["sym"].split(":")[-1], r["score"], r.get("close"), trig,
                "—" if dist is None else f"{dist:.1f}", stop if stop is not None else "—",
                adr, r.get("pole_pct"), r.get("consol_days"), r.get("pullback_pct"),
                r.get("range5_over_adr"), "✓" if r.get("higher_lows") else "·",
                r.get("vol_dryup"), "✓" if r.get("mas_stacked") else "·"))
        return "\n".join(out)


if __name__ == "__main__":
    QMomentum.main()
