#!/usr/bin/env python3
"""
Base-Breakout Scanner — Stage 1 (universe FILTER) and Stage 2 (bar-based
tightness RANKER).

This is the SIBLING setup to the Qullamaggie flag-pullback scanner
(qmomentum-scan), kept deliberately SEPARATE. The flag scanner looks for a
name that ran up hard and then pulled BACK below its high into a coil. This
one looks for the OTHER archetype: a stock coiled ON / just under a 3-month
high, about to break out to new highs on range expansion — covering both the
long flat base AND the high-tight-flag (sharp advance + short tight coil at
the highs). The two scanners do not share scoring code on purpose: the flag
scanner's metrics assume "the high is in the past" (distance-below-high sweet
spot, bars-since-high consolidation), which structurally under-rate a name
whose high IS the current bar.

Horizon: 3-month (swing). Proximity is measured to the 3-month high (High.3M),
not the 52-week high — a year is too long a range for swing breakouts.

Setup target ("coiled at the highs, ready to break to new highs"):
  1. Near the high   -> price sits within a few % of its 3-month high
  2. Tight coil      -> a shallow, contracting range, measured in ADR units
                        (a 4%-ADR name's "tight" looks wide in absolute %)
  3. Power behind it  -> a prior advance into the high (pole) is a plus, not
                        required — this is what makes high-tight-flags rank up
  4. Trend support   -> above a flat-to-rising 50SMA
  5. Supply dry-up   -> volume contracts through the coil
  6. Trigger         -> range-expansion break above the pivot (3M/base high)

Stage 1 pulls the universe from a captured TradingView screen (default: a
"near 3-month high" screen) via the public scanner API and gates on snapshot
fields. Stage 2 takes a bars JSON (produced by the caller via the TradingView
MCP) and computes the precise coil metrics the snapshot can't express.

Usage:
  python3 breakout.py scan --screen screens/3m_high_in.json --outdir out
  python3 breakout.py refine --bars out/bars.json --scan out/breakout_<screen>.json
  python3 breakout.py add-screen --from-capture cap.json --name "<name>"
"""
from __future__ import annotations
import argparse, json, sys, os, glob, datetime, re, urllib.request

# Snapshot columns we ask the scanner for (order matters -> mapped by index).
COLUMNS = [
    "name", "close", "change", "Perf.1M", "Perf.3M", "Perf.6M",
    "SMA10", "SMA20", "SMA50", "SMA200",
    "High.1M", "Low.1M", "High.3M", "Low.3M",
    "Volatility.D", "ADRP", "Value.Traded", "relative_volume_10d_calc",
]

# ---------------------------------------------------------------------------
# Stage 1 — fetch + coarse gate
# ---------------------------------------------------------------------------

def _post_scan(body, market):
    url = f"https://scanner.tradingview.com/{market}/scan?label-product=screener-stock"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_universe(screen, limit=400, market="india"):
    """Replay a captured screen dict (filter/filter2/sort/market), overriding
    only the columns with our analysis set, so the universe matches the user's
    real TradingView screen. A screen is required; there is no fallback."""
    if not screen:
        raise ValueError(
            "fetch_universe requires a screen. Capture one with the "
            "screener_capture MCP tool, then add-screen.")
    market = screen.get("market", market)
    body = {"columns": COLUMNS, "range": [0, limit]}
    flt = list(screen.get("filter") or [])
    if not any(c.get("left") == "is_primary" for c in flt):
        flt.append({"left": "is_primary", "operation": "equal", "right": True})
    body["filter"] = flt
    if screen.get("filter2") is not None:
        body["filter2"] = screen["filter2"]
    body["sort"] = screen.get("sort") or {"sortBy": "market_cap_basic", "sortOrder": "desc"}
    body["markets"] = [market]
    payload = _post_scan(body, market)
    idx = {c: i for i, c in enumerate(COLUMNS)}
    rows = []
    for o in payload.get("data", []):
        d = o["d"]
        g = lambda k: d[idx[k]]
        rows.append({
            "sym": o["s"], "exchange": o["s"].split(":")[0],
            "name": g("name"), "close": g("close"), "chg": g("change"),
            "p1": g("Perf.1M"), "p3": g("Perf.3M"), "p6": g("Perf.6M"),
            "s10": g("SMA10"), "s20": g("SMA20"), "s50": g("SMA50"), "s200": g("SMA200"),
            "h1": g("High.1M"), "l1": g("Low.1M"), "h3": g("High.3M"), "l3": g("Low.3M"),
            "vol": g("Volatility.D"), "adrp": g("ADRP"),
            "value": g("Value.Traded"), "rvol": g("relative_volume_10d_calc"),
        })
    return payload.get("totalCount", len(rows)), rows


def coarse_score(r):
    """0-100 coarse base-breakout readiness from snapshot fields. This is a
    GATE, not a ranking (Stage 2 ranks). Unlike the flag scanner, being AT the
    3M high is the BEST state here, not a penalty. Tightness is measured in
    ADR units so a high-volatility name isn't punished for a wide raw %."""
    s, why = 0, []
    close, s10, s20, s50, s200 = r["close"], r["s10"], r["s20"], r["s50"], r["s200"]
    adrp = r.get("adrp")

    # off = % the close sits BELOW its 3-month high (0 = right at it).
    ref_hi = r.get("h3")
    off = (ref_hi - close) / ref_hi * 100 if ref_hi else None

    # 1) Proximity to the 3M high (0-30) — the defining trait. At/just under = best.
    if off is not None:
        if off <= 3:
            s += 30; why.append(f"at 3M high ({off:.1f}% under)")
        elif off <= 6:
            s += 20; why.append(f"{off:.1f}% under 3M high")
        elif off <= 10:
            s += 10; why.append(f"{off:.1f}% under 3M high")
        elif off <= 15:
            s += 4

    # 2) Tight coil (0-25) — the recent 1-month range, in ADR units. A tight
    # coil for a wild name is many raw % but few ADRs; normalise so we don't
    # only ever reward sleepy low-ADR names.
    h1, l1 = r.get("h1"), r.get("l1")
    rng1 = (h1 - l1) / close * 100 if (h1 and l1) else None
    coil = rng1 / adrp if (rng1 is not None and adrp) else None
    if coil is not None:
        if coil <= 5:
            s += 25; why.append(f"tight coil ({coil:.1f} ADR/1M)")
        elif coil <= 8:
            s += 15; why.append(f"coil {coil:.1f} ADR/1M")
        elif coil <= 12:
            s += 6

    # 3) Trend support (0-20) — above a rising 50SMA (and 200 if we have it).
    if all(v for v in (s10, s20, s50)):
        if s10 >= s20 >= s50 and close > s50 and (not s200 or close > s200):
            s += 20; why.append("above rising 50SMA")
        elif close > s50 and s20 >= s50:
            s += 12; why.append("above 50SMA")
        elif close > s50:
            s += 6
    elif s50 and close > s50:
        s += 6

    # 4) Supply dry-up (0-15) — volume contracts through the coil.
    rvol = r["rvol"]
    if rvol is not None:
        if 0.3 <= rvol <= 1.0:
            s += 15; why.append("volume drying up")
        elif rvol <= 1.3:
            s += 8

    # 5) Pre-breakout, not mid-blowoff (0-10) — quiet day = still coiled.
    chg = abs(r["chg"]) if r["chg"] is not None else 99
    if chg <= 3:
        s += 10; why.append("quiet day")
    elif chg <= 5:
        s += 5

    # Power bonus (0-10, funnel saturates) — a prior advance into the high is
    # what separates a high-tight-flag from a random name pinned to a ceiling.
    p3 = r["p3"] or 0
    if 20 <= p3 <= 200:
        s += 10; why.append(f"power {p3:.0f}%/3M")
    elif 10 <= p3 < 20 or 200 < p3 <= 400:
        s += 5

    r["off_high"] = round(off, 1) if off is not None else None
    r["coil_adr"] = round(coil, 1) if coil is not None else None
    r["score"] = min(s, 100)
    r["why"] = why
    return r


def tier(r):
    """actionable = coiled RIGHT at the 3M high, genuinely tight (in ADR),
    trend-supported, quiet. Kept strict on purpose: the default screen already
    pre-selects "near a 3M high", so proximity alone can't separate names —
    the actionable gate demands a tight coil + a quiet tape so the daily
    shortlist stays short. Stage 2 (bars) then ranks within it."""
    off = r["off_high"]; coil = r.get("coil_adr"); chg = r["chg"]
    adrp = r.get("adrp")
    at_lim = max(2.5, 0.8 * adrp) if adrp else 2.5
    at_high = off is not None and off <= at_lim
    tight = coil is not None and coil <= 5          # genuinely tight, in ADR
    quiet = chg is not None and abs(chg) <= 3
    if r["score"] >= 90 and at_high and tight and quiet:
        return "actionable"
    if r["score"] >= 65 and (off is None or off <= 8):
        return "building"
    if r["score"] >= 45:
        return "early"
    return "drop"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "screen").lower()).strip("_") or "screen"


def load_screen(path):
    with open(path) as f:
        return json.load(f)


def scan_and_score(screen, limit=400, market="india"):
    total, rows = fetch_universe(screen, limit=limit, market=market)
    for r in rows:
        coarse_score(r)
        r["tier"] = tier(r)
    rows = [r for r in rows if r["tier"] != "drop"]
    rows.sort(key=lambda r: (-r["score"], r.get("off_high") or 99))
    return total, rows


def write_output(outdir, slug, title, total, rows):
    today = datetime.date.today().isoformat()
    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, f"breakout_{slug}.json")
    md_path = os.path.join(outdir, f"breakout_{slug}_{today}.md")
    with open(json_path, "w") as f:
        json.dump({"date": today, "screen": title, "universe": total,
                   "kept": len(rows), "rows": rows}, f, indent=2)
    md = render_markdown(today, total, rows, title)
    with open(md_path, "w") as f:
        f.write(md)
    return json_path, md_path, md


def run_scan(args):
    if not args.screen:
        print("A screen is required. Capture one with the screener_capture "
              "MCP tool, then add-screen.", file=sys.stderr)
        return
    screen = load_screen(args.screen)
    title = screen.get("name") or os.path.basename(args.screen)
    slug = slugify(title)
    total, rows = scan_and_score(screen=screen, limit=args.limit, market=args.market)
    json_path, md_path, md = write_output(args.outdir, slug, title, total, rows)
    print(md)
    print(f"\nSaved: {md_path} (dated history) / {json_path} (latest, overwritten)",
          file=sys.stderr)


def run_scan_all(args):
    files = sorted(glob.glob(os.path.join(args.screens, "*.json")))
    if not files:
        print(f"No screen files in {args.screens}/ — capture one with the "
              "screener_capture MCP tool, then add-screen.", file=sys.stderr)
        return
    for path in files:
        screen = load_screen(path)
        title = screen.get("name") or os.path.basename(path)
        total, rows = scan_and_score(screen=screen, limit=args.limit)
        _, md_path, _ = write_output(args.outdir, slugify(title), title, total, rows)
        act = sum(1 for r in rows if r["tier"] == "actionable")
        print(f"{title}: {total} universe -> {len(rows)} setups "
              f"({act} actionable)  [{md_path}]")


def run_add_screen(args):
    with open(args.from_capture) as f:
        cap = json.load(f)
    screen = {
        "name": args.name or cap.get("name") or "Untitled screen",
        "url": args.url or cap.get("sourceUrl") or cap.get("url"),
        "market": cap["market"],
        "filter": cap.get("filter"),
        "filter2": cap.get("filter2"),
        "sort": cap.get("sort"),
        "captured_at": cap.get("capturedAt", datetime.date.today().isoformat()),
    }
    os.makedirs(args.screens, exist_ok=True)
    path = os.path.join(args.screens, slugify(screen["name"]) + ".json")
    with open(path, "w") as f:
        json.dump(screen, f, indent=2)
    print(f"Wrote {path}  (market={screen['market']})")


def render_markdown(today, total, rows, title="Base-Breakout filter"):
    tiers = {"actionable": [], "building": [], "early": []}
    for r in rows:
        tiers[r["tier"]].append(r)
    out = [f"# Base-Breakout Scan — {title} — {today}",
           f"\nUniverse: **{total}** names from the screen -> **{len(rows)}** setups "
           f"(actionable {len(tiers['actionable'])} · building "
           f"{len(tiers['building'])} · early {len(tiers['early'])})\n"]
    labels = {"actionable": "🟢 Actionable — coiled at 3M high, ready",
              "building": "🟡 Building — near 3M high, coil still loosening",
              "early": "🟠 Early — in range, coil too wide / just reclaimed"}
    for t in ("actionable", "building", "early"):
        if not tiers[t]:
            continue
        out.append(f"\n## {labels[t]}\n")
        out.append("| # | Symbol | Gate | Close | Day% | 1M% | 3M% | %below 3M-high | coil (ADR/1M) | Notes |")
        out.append("|--:|--------|-----:|------:|-----:|----:|----:|---------------:|--------------:|-------|")
        for i, r in enumerate(tiers[t], 1):
            out.append("| {} | {} | {} | {} | {:+.1f} | {:.0f} | {:.0f} | {} | {} | {} |".format(
                i, r["sym"].split(":")[1], r["score"], r["close"], r["chg"] or 0,
                r["p1"] or 0, r["p3"] or 0,
                f"{r['off_high']:.1f}%" if r["off_high"] is not None else "—",
                f"{r['coil_adr']:.1f}" if r.get("coil_adr") is not None else "—",
                "; ".join(r["why"][:3])))
    out.append("\n> Stage-1 snapshot gate. Run Stage-2 (bars) on the 🟢 Actionable "
               "tier before trading — see SKILL.md.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Stage 2 — precise coil metrics from daily bars
# ---------------------------------------------------------------------------

def sma(vals, n):
    w = vals[-n:]
    return sum(w) / len(w)


def refine_one(sym, bars):
    """bars: list of [time, open, high, low, close, volume] oldest->newest.
    Measures the coil AT the high directly — distance to the pivot, recent
    tightness (in ADR), depth, the pole behind it, and volume behaviour — none
    of which require the high to be in the past. Length-agnostic on purpose so
    a short high-tight-flag ranks as well as a long flat base."""
    if len(bars) < 30:
        return {"sym": sym, "error": "insufficient bars"}
    h = [b[2] for b in bars]; l = [b[3] for b in bars]
    c = [b[4] for b in bars]; v = [b[5] for b in bars]
    n = len(bars)

    adr = sum((h[i] - l[i]) / c[i] for i in range(n - 20, n)) / 20 * 100  # ADR%(20)
    look = min(n, 60)                            # ~3 months of daily bars
    pivot = max(h[-look:])                        # 3M high = breakout trigger
    lo3 = min(l[-look:])
    pole = (pivot - lo3) / lo3 * 100 if lo3 else 0  # advance into the high

    # Coil length: trailing bars held within a band of the pivot (context only,
    # not scored — a short high-tight-flag is as valid as a long flat base).
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
    stacked = sma(c, 10) >= sma(c, 20) >= sma(c, 50)
    above_200 = (c[-1] > sma(c, 200)) if n >= 200 else None

    # Precise coil-at-highs score (0-100) — length-agnostic.
    s = 0
    if dist_adr is not None:                       # near / at the trigger
        if dist_adr <= 0.5: s += 20
        elif dist_adr <= 1.0: s += 15
        elif dist_adr <= 2.0: s += 8
    if tight is not None:                          # tight last week (in ADR)
        if tight <= 1.5: s += 20
        elif tight <= 2.5: s += 13
        elif tight <= 3.5: s += 5
    if depth <= 15: s += 15                        # shallow coil
    elif depth <= 25: s += 9
    elif depth <= 35: s += 3
    if 15 <= pole <= 200: s += 15                  # power behind the coil
    elif 200 < pole <= 400: s += 7
    elif 8 <= pole < 15: s += 6
    if vdry is not None:                           # supply dry-up (gradient)
        if vdry <= 0.85: s += 15
        elif vdry <= 1.05: s += 9
        elif vdry <= 1.3: s += 3
    if stacked: s += 10
    if contraction is not None and contraction < 1.0: s += 5
    elif contraction is None: s += 2               # too short to measure

    return {"sym": sym, "precise_score": min(s, 100), "close": round(c[-1], 2),
            "adr_pct": round(adr, 2), "pivot": round(pivot, 2),
            "pole_pct": round(pole, 1), "base_len": base_len,
            "base_depth_pct": round(depth, 1),
            "range5_over_adr": round(tight, 2) if tight else None,
            "contraction": round(contraction, 2) if contraction else None,
            "dist_to_pivot_pct": round(dist_pivot, 2),
            "dist_to_pivot_adr": round(dist_adr, 2) if dist_adr is not None else None,
            "triggered": triggered,
            "vol_dryup": round(vdry, 2) if vdry else None,
            "mas_stacked": stacked, "above_200sma": above_200,
            "trigger": round(pivot, 2)}


S2_TABLE_START = "<!-- stage2-table:start -->"
S2_TABLE_END = "<!-- stage2-table:end -->"


def render_refined_table(date, good, errors, title):
    out = [f"## Stage 2 — Refined Ranking ({title}) — {date}", ""]
    out.append(f"Ranked by `precise_score` (bar-derived coil tightness — the sole ranking). "
               f"**{len(good)}** names refined"
               + (f", {len(errors)} skipped (insufficient bars)." if errors else "."))
    out.append("")
    out.append("| # | Symbol | Score | Gate | Close | Trigger | Stop~ | ADR% | Pole% | Coil | "
               "Depth% | 5d/ADR | Contr | ToPivot | VolDry | Stkd |")
    out.append("|--:|--------|------:|------|------:|--------:|------:|-----:|------:|-----:|"
               "-------:|-------:|------:|--------:|-------:|:----:|")
    for i, r in enumerate(good, 1):
        adr = r.get("adr_pct"); trig = r.get("trigger"); depth = r.get("base_depth_pct")
        # Stop ~ coil low (depth below the trigger).
        stop = round(trig * (1 - depth / 100), 2) if (trig and depth is not None) else None
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            i, r["sym"].split(":")[-1], r["precise_score"], r.get("gate", "—"),
            r.get("close"), trig, stop if stop is not None else "—",
            adr, r.get("pole_pct"), r.get("base_len"), depth,
            r.get("range5_over_adr"), r.get("contraction") or "—",
            f"{r.get('dist_to_pivot_pct')}%", r.get("vol_dryup"),
            "✓" if r.get("mas_stacked") else "·"))
    return "\n".join(out)


def upsert_section(path, block, start, end):
    wrapped = f"{start}\n{block}\n{end}"
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    if start in existing and end in existing:
        pre = existing.split(start, 1)[0].rstrip()
        post = existing.split(end, 1)[1]
        new = f"{pre}\n\n{wrapped}{post}"
    elif existing:
        new = f"{existing.rstrip()}\n\n{wrapped}\n"
    else:
        new = wrapped + "\n"
    with open(path, "w") as f:
        f.write(new)


def run_refine(args):
    with open(args.bars) as f:
        bars_map = json.load(f)
    gate = {}
    title = "refined"
    sdate = datetime.date.today().isoformat()
    report_path = None
    if args.scan:
        with open(args.scan) as f:
            scan_data = json.load(f)
        for row in scan_data.get("rows", []):
            gate[row["sym"]] = row.get("tier")
        title = scan_data.get("screen", title)
        sdate = scan_data.get("date") or sdate
        report_path = os.path.join(args.outdir, f"breakout_{slugify(title)}_{sdate}.md")

    results = []
    for sym, bars in bars_map.items():
        res = refine_one(sym, bars)
        if "error" not in res and sym in gate:
            res["gate"] = gate[sym]
        results.append(res)
    good = [r for r in results if "error" not in r]
    good.sort(key=lambda r: -r["precise_score"])
    errors = [r for r in results if "error" in r]

    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, "refined.json")
    with open(out_path, "w") as f:
        json.dump({"date": sdate, "screen": title, "refined": good, "errors": errors},
                  f, indent=2)

    if report_path:
        upsert_section(report_path, render_refined_table(sdate, good, errors, title),
                       S2_TABLE_START, S2_TABLE_END)

    print(f"Stage 2 refined: {len(good)} ranked, {len(errors)} skipped -> {out_path}"
          + (f"; table -> {report_path} (now author the read above the table)"
             if report_path else ""))


def main():
    p = argparse.ArgumentParser(description="Base-breakout scanner")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Stage 1: fetch universe + coarse gate")
    s.add_argument("--screen", help="screen config JSON (captured filter)")
    s.add_argument("--limit", type=int, default=400)
    s.add_argument("--market", default="india")
    s.add_argument("--outdir", default="out")
    s.set_defaults(func=run_scan)

    sa = sub.add_parser("scan-all", help="Stage 1 across every screen in screens/")
    sa.add_argument("--screens", default="screens")
    sa.add_argument("--limit", type=int, default=400)
    sa.add_argument("--outdir", default="out")
    sa.set_defaults(func=run_scan_all)

    a = sub.add_parser("add-screen", help="save a screen from a screener_capture output")
    a.add_argument("--from-capture", required=True)
    a.add_argument("--name")
    a.add_argument("--url")
    a.add_argument("--screens", default="screens")
    a.set_defaults(func=run_add_screen)

    r = sub.add_parser("refine", help="Stage 2: precise coil metrics from bars JSON")
    r.add_argument("--bars", required=True, help="JSON: {sym: [[t,o,h,l,c,v],...]}")
    r.add_argument("--scan", help="optional Stage-1 json to attach gate labels")
    r.add_argument("--outdir", default="out")
    r.set_defaults(func=run_refine)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
