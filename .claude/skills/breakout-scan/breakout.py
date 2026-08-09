#!/usr/bin/env python3
"""
Base-Breakout Scanner — a `ScreenerRanking` scan.

The SIBLING of the Qullamaggie flag scanner (qmomentum-scan). The flag scanner
wants a name that ran up and pulled BACK below its high into a coil; this one
wants the OTHER archetype: a stock coiled ON / just under its 3-month high, about
to break out to NEW highs — covering both the long flat base and the high-tight-
flag. The metrics differ on purpose: the flag scanner assumes "the high is in the
past" (distance-below-high, bars-since-high), which structurally under-rates a
name whose high IS the current bar. Here proximity is measured to the 3-month high
(a year is too long a range for swing breakouts).

The pipeline + CLI live in `screener_ranking.ScreenerRanking`; this file supplies
the breakout judgement:
  * eliminate_losses (columns): drop below-50SMA, stretched >1 ADR from the 10SMA,
    or sitting more than ~15% under the 3M high (not near the high = not a setup).
  * score (bars): the coil AT the high — pivot = the 3M high (the breakout
    trigger), pole = the advance into it, base depth/tightness/contraction, volume
    dry-up. Length-agnostic so a short high-tight-flag ranks with a long flat base.

  python3 breakout.py scan --screen screens/<s>.json --outdir out
  <model> data_get_ohlcv_batch(symbols=shortlist) -> out/bars.json
  python3 breakout.py rank --bars out/bars.json --scan out/breakout_<s>.json
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from screener_ranking import ScreenerRanking


class Breakout(ScreenerRanking):
    NAME = "Base-Breakout"
    FILE_PREFIX = "breakout"
    COLUMNS = ["name", "close", "change", "Perf.1M", "Perf.3M",
               "SMA10", "SMA20", "SMA50", "SMA200",
               "High.1M", "Low.1M", "High.3M", "ADRP"]
    DEFAULT_SORT = {"sortBy": "market_cap_basic", "sortOrder": "desc"}
    SCORE_BLURB = ("Ranked by `score` — bar-derived coil-at-the-high readiness "
                   "(trigger proximity + coil tightness dominate). The sole ranking.")

    MAX_S10_DIST_ADR = 1.0     # a coil must hug the 10-day SMA
    MAX_OFF_HIGH_PCT = 15.0    # farther than this under the 3M high = not near the high
    MAX_DEPTH_PCT = 35.0       # a coil deeper than this is a failed base, not a flag

    # -- column gate ---------------------------------------------------------

    def annotate(self, r):
        close, s10, adrp = r.get("close"), r.get("s10"), r.get("adrp")
        h3, h1, l1 = r.get("h3"), r.get("h1"), r.get("l1")
        r["off_high"] = round((h3 - close) / h3 * 100, 1) if (h3 and close) else None
        rng1 = (h1 - l1) / close * 100 if (h1 and l1 and close) else None
        r["coil_adr"] = round(rng1 / adrp, 1) if (rng1 is not None and adrp) else None
        ext10 = (close - s10) / s10 * 100 if s10 else None
        r["dist_s10_adr"] = round(abs(ext10) / adrp, 2) if (ext10 is not None and adrp) else None

    def is_loser(self, r):
        close, s50 = r.get("close"), r.get("s50")
        if not close or not s50:
            return None
        if close < s50:
            return "below 50SMA"
        d = r.get("dist_s10_adr")
        if d is not None and d > self.MAX_S10_DIST_ADR:
            return f"{d:.1f} ADR from 10SMA (extended/broken)"
        off = r.get("off_high")
        if off is not None and off > self.MAX_OFF_HIGH_PCT:
            return f"{off:.0f}% under 3M high (not near the high)"
        return None

    # -- bar metrics ---------------------------------------------------------

    def bar_metrics(self, sym, bars):
        if not bars or len(bars) < self.MIN_BARS:
            return {"sym": sym, "error": "insufficient bars"}
        h, l, c, v = self.ohlcv(bars)
        n = len(bars)

        adr = self.adr20(h, l, c)
        look = min(n, 60)                             # ~3 months of daily bars
        pivot = max(h[-look:])                        # 3M high = breakout trigger
        lo3 = min(l[-look:])
        pole = (pivot - lo3) / lo3 * 100 if lo3 else 0  # advance into the high

        # Coil length: trailing bars held within a band of the pivot (context only —
        # a short high-tight-flag is as valid as a long flat base).
        band = max(0.08, adr / 100 * 2.5)
        base_len = 0
        for i in range(n - 1, -1, -1):
            if c[i] >= pivot * (1 - band) and h[i] <= pivot * (1 + 0.03):
                base_len += 1
            else:
                break
        base_len = max(base_len, 1)
        base = bars[-base_len:]
        depth = (pivot - min(b[3] for b in base)) / pivot * 100

        rng5 = (max(h[-5:]) - min(l[-5:])) / c[-1] * 100
        tight = rng5 / adr if adr else None           # recent 5d range in ADR
        if base_len >= 8:
            half = base_len // 2
            early = bars[-base_len:-half] if half else bars[-base_len:]
            late = bars[-half:]
            er = (max(b[2] for b in early) - min(b[3] for b in early)) / c[-1] * 100
            lr = (max(b[2] for b in late) - min(b[3] for b in late)) / c[-1] * 100
            contraction = lr / er if er else None     # <1 = range tightening
        else:
            contraction = None

        dist_pivot = (pivot - c[-1]) / c[-1] * 100    # % to the trigger
        dist_adr = (dist_pivot / adr) if adr else None
        triggered = c[-1] > pivot * 1.001

        vbase = sum(v[-base_len:]) / base_len
        vdry = (sum(v[-5:]) / 5) / vbase if vbase else None
        stacked = self.sma(c, 10) >= self.sma(c, 20) >= self.sma(c, 50)
        above_200 = (c[-1] > self.sma(c, 200)) if n >= 200 else None

        return {"sym": sym, "close": round(c[-1], 2), "adr_pct": round(adr, 2),
                "pivot": round(pivot, 2), "pole_pct": round(pole, 1), "base_len": base_len,
                "base_depth_pct": round(depth, 1),
                "range5_over_adr": round(tight, 2) if tight else None,
                "contraction": round(contraction, 2) if contraction else None,
                "dist_to_pivot_pct": round(dist_pivot, 2),
                "dist_to_pivot_adr": round(dist_adr, 2) if dist_adr is not None else None,
                "triggered": triggered, "vol_dryup": round(vdry, 2) if vdry else None,
                "mas_stacked": stacked, "above_200sma": above_200,
                "trigger": round(pivot, 2)}

    def disqualify(self, m):
        if m["base_depth_pct"] > self.MAX_DEPTH_PCT:
            return f"coil {m['base_depth_pct']:.0f}% deep (base failed)"
        return None

    # -- score ---------------------------------------------------------------

    def readiness(self, m):
        """Length-agnostic coil-at-the-high score. Trigger proximity (20) + recent
        tightness (20) dominate; pole is power behind the coil, not required."""
        s = 0
        d = m["dist_to_pivot_adr"]
        if d is not None:
            if d <= 0.5: s += 20
            elif d <= 1.0: s += 15
            elif d <= 2.0: s += 8
        t = m["range5_over_adr"]
        if t is not None:
            if t <= 1.5: s += 20
            elif t <= 2.5: s += 13
            elif t <= 3.5: s += 5
        depth = m["base_depth_pct"]
        if depth <= 15: s += 15
        elif depth <= 25: s += 9
        elif depth <= 35: s += 3
        p = m["pole_pct"]
        if 15 <= p <= 200: s += 15
        elif 200 < p <= 400: s += 7
        elif 8 <= p < 15: s += 6
        vd = m["vol_dryup"]
        if vd is not None:
            if vd <= 0.85: s += 15
            elif vd <= 1.05: s += 9
            elif vd <= 1.3: s += 3
        if m["mas_stacked"]: s += 10
        contr = m["contraction"]
        if contr is not None and contr < 1.0: s += 5
        elif contr is None: s += 2                    # too short to measure
        return min(s, 100)

    # -- report table --------------------------------------------------------

    def render_table(self, ranked):
        out = ["| # | Symbol | Score | Close | Trigger | Stop~ | ADR% | Pole% | Coil | "
               "Depth% | 5d/ADR | Contr | ToPivot | VolDry | Stkd |",
               "|--:|--------|------:|------:|--------:|------:|-----:|------:|-----:|"
               "-------:|-------:|------:|--------:|-------:|:----:|"]
        for i, r in enumerate(ranked, 1):
            adr = r.get("adr_pct"); trig = r.get("trigger"); depth = r.get("base_depth_pct")
            stop = round(trig * (1 - depth / 100), 2) if (trig and depth is not None) else None  # ~coil low
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                i, r["sym"].split(":")[-1], r["score"], r.get("close"), trig,
                stop if stop is not None else "—", adr, r.get("pole_pct"), r.get("base_len"),
                depth, r.get("range5_over_adr"), r.get("contraction") or "—",
                f"{r.get('dist_to_pivot_pct')}%", r.get("vol_dryup"),
                "✓" if r.get("mas_stacked") else "·"))
        return "\n".join(out)


if __name__ == "__main__":
    Breakout.main()
