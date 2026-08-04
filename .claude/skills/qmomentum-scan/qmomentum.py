#!/usr/bin/env python3
"""
Qullamaggie Momentum Scanner — Stage 1 (universe FILTER) and
Stage 2 (bar-based tightness RANKER).

The two stages are independent and non-overlapping, each self-contained on its
own data source:
  * Stage 1 is a cheap gate over the whole universe. Using only snapshot fields,
    it eliminates obvious non-setups and buckets survivors (actionable/building/
    early). It does NOT rank — its score exists only to draw the gate.
  * Stage 2 is the sole ranker. For the survivors it pulls daily bars and
    computes the precise tightness metrics the snapshot can't express, then
    ranks on those alone. No Stage-1 number feeds the ranking, so nothing is
    computed-then-merged twice.

Strategy target: continuation/breakout setups in the spirit of Kristjan
Kullamagi ("Qullamaggie"):
  1. Flagpole   -> a prior sharp advance (~30-100%+ over 1-3 months)
  2. Pullback   -> an orderly 2-8 week consolidation making higher lows
  3. Surfing    -> price rides a rising, stacked 10/20/50 SMA
  4. Tight      -> a quiet, contracting range near the 10/20 SMA
  5. Trigger    -> range-expansion breakout above the consolidation high

Stage 1 pulls the momentum universe straight from TradingView's public
scanner API (no auth needed) and scores each name using snapshot fields.
Stage 2 takes a bars JSON (produced by the caller via the TradingView MCP)
and computes the precise tightness metrics the snapshot can't express.

Usage:
  python3 qmomentum.py scan                 # Stage 1 -> writes dated md + json
  python3 qmomentum.py scan --limit 400 --min-price 30 --min-value 1e8
  python3 qmomentum.py refine --bars bars.json --scan out/xxx.json  # Stage 2
"""
from __future__ import annotations
import argparse, json, sys, os, glob, datetime, re, urllib.request

# Snapshot columns we ask the scanner for (order matters -> mapped by index).
COLUMNS = [
    "name", "close", "change", "Perf.1M", "Perf.3M", "Perf.6M",
    "SMA10", "SMA20", "SMA50",
    "High.1M", "High.3M", "High.6M",
    "Volatility.D", "ADRP", "Value.Traded", "relative_volume_10d_calc",
]

# ---------------------------------------------------------------------------
# Stage 1 — fetch + coarse score
# ---------------------------------------------------------------------------

def _post_scan(body, market):
    """POST a scan body to the public scanner for a market. Returns raw payload."""
    url = f"https://scanner.tradingview.com/{market}/scan?label-product=screener-stock"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_universe(screen, limit=400, market="india"):
    """Fetch the momentum universe by replaying a captured screen dict
    (filter/filter2/sort/market), overriding only the columns with our
    analysis set — so the universe matches the user's real TradingView
    screen. A screen is required; there is no built-in fallback filter."""
    if not screen:
        raise ValueError(
            "fetch_universe requires a screen. Capture one with the "
            "screener_capture MCP tool, then add-screen.")
    market = screen.get("market", market)
    body = {"columns": COLUMNS, "range": [0, limit]}
    # Keep only primary listings (e.g. NSE over BSE for the same company).
    # Done server-side so duplicate listings don't eat into `limit`.
    flt = list(screen.get("filter") or [])
    if not any(c.get("left") == "is_primary" for c in flt):
        flt.append({"left": "is_primary", "operation": "equal", "right": True})
    body["filter"] = flt
    if screen.get("filter2") is not None:
        body["filter2"] = screen["filter2"]
    body["sort"] = screen.get("sort") or {"sortBy": "Perf.1M", "sortOrder": "desc"}
    body["markets"] = [market]
    payload = _post_scan(body, market)
    idx = {c: i for i, c in enumerate(COLUMNS)}
    rows = []
    for o in payload.get("data", []):
        d = o["d"]
        rows.append({
            "sym": o["s"], "exchange": o["s"].split(":")[0],
            "name": d[idx["name"]], "close": d[idx["close"]], "chg": d[idx["change"]],
            "p1": d[idx["Perf.1M"]], "p3": d[idx["Perf.3M"]], "p6": d[idx["Perf.6M"]],
            "s10": d[idx["SMA10"]], "s20": d[idx["SMA20"]], "s50": d[idx["SMA50"]],
            "h1": d[idx["High.1M"]], "h3": d[idx["High.3M"]],
            "h6": d[idx["High.6M"]], "vol": d[idx["Volatility.D"]],
            "adrp": d[idx["ADRP"]],
            "value": d[idx["Value.Traded"]], "rvol": d[idx["relative_volume_10d_calc"]],
        })
    return payload.get("totalCount", len(rows)), rows


def pct(a, b):
    return (a - b) / b * 100.0 if b else None


def coarse_score(r):
    """0-100 coarse Qullamaggie readiness from snapshot fields."""
    s, why = 0, []
    close, s10, s20, s50 = r["close"], r["s10"], r["s20"], r["s50"]

    # 1) Trend / MA alignment (0-25) — stacked & rising proxy.
    # Price must hold the 20SMA (not the 10SMA): a name coiling just under a
    # rising 10SMA is exactly the pullback we want, so don't penalise close<s10.
    if all(v for v in (s10, s20, s50)):
        if s10 > s20 > s50 and close > s20:
            s += 25; why.append("stacked, holding 20SMA")
        elif close > s50 and s10 > s20:
            s += 15; why.append("above 50SMA, 10>20")
        elif close > s50:
            s += 8; why.append("above 50SMA")
    elif s50 and close > s50:
        s += 6; why.append("above 50SMA (young)")

    # 2) Coiled under recent high (0-25) — pulled back but near the highs.
    # off1 = % the close sits BELOW the 1M high (>=0 below, ~0 at highs).
    off1 = (r["h1"] - close) / r["h1"] * 100 if r["h1"] else None
    if off1 is not None:
        if 0 <= off1 <= 12:
            s += 25
            why.append("at/near 1M high" if off1 < 2
                       else f"coiled {off1:.1f}% under 1M high")
        elif 12 < off1 <= 20:
            s += 15; why.append(f"{off1:.1f}% under 1M high")
        elif 20 < off1 <= 35:
            s += 6

    # 3) Proximity to 10SMA (0-20) — surfing / tight, not extended.
    # Measure distance in ADRs, not raw %: 6% off the 10SMA is <1 day for a wild
    # name but 3 days for a quiet one. Falls back to fixed % when ADRP is null.
    ext = pct(close, s10) if s10 else None
    adrp = r.get("adrp")
    ext_adr = (ext / adrp) if (ext is not None and adrp) else None
    if ext_adr is not None:
        if abs(ext_adr) <= 1.5:
            s += 20; why.append(f"surfing 10SMA ({ext_adr:+.1f} ADR)")
        elif abs(ext_adr) <= 3:
            s += 12; why.append("near 10SMA")
        elif ext_adr > 3:
            s += 5

    # 4) Quiet consolidation day (0-15) — no parabolic blowoff today
    chg = abs(r["chg"]) if r["chg"] is not None else 99
    if chg <= 3:
        s += 15; why.append("quiet day")
    elif chg <= 5:
        s += 8
    elif chg <= 8:
        s += 3

    # 5) Pole quality (0-15) — strong but not insane 3M advance
    p3 = r["p3"] or 0
    if 30 <= p3 <= 150:
        s += 15; why.append(f"pole {p3:.0f}%/3M")
    elif 25 <= p3 < 30 or 150 < p3 <= 250:
        s += 8
    elif p3 > 250:
        s += 2  # parabolic — usually not a clean flag

    # Volume dry-up modifier — quiet volume during consolidation is ideal
    rvol = r["rvol"]
    if rvol is not None and 0.3 <= rvol <= 1.1:
        s += 5; why.append("volume drying up")

    r["off_high_1m"] = round(off1, 1) if off1 is not None else None
    r["ext_s10_adr"] = round(ext_adr, 2) if ext_adr is not None else None
    r["score"] = min(s, 100)
    r["why"] = why
    return r


def tier(r):
    # "actionable" = tight, quiet, coiled right under the highs on a rising
    # 10SMA. Kept deliberately strict so the daily list stays short; Stage 2
    # (bar tightness) then ranks within it. The looser tiers are the funnel.
    off = r["off_high_1m"]; ext_adr = r.get("ext_s10_adr"); chg = r["chg"]
    coiled = off is not None and off <= 8
    tight =  ext_adr is not None and (abs(ext_adr) <= 1.5)
    quiet = chg is not None and abs(chg) <= 4
    if r["score"] >= 95 and coiled and tight and quiet:
        return "actionable"   # tight, near 10SMA, quiet -> ready to break
    if r["score"] >= 70 and (off is None or off <= 20):
        return "building"     # tightening but not yet coiled/quiet enough
    if r["score"] >= 45:
        return "early"        # pole formed, still digesting / more extended
    return "drop"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "screen").lower()).strip("_") or "screen"


def load_screen(path):
    with open(path) as f:
        return json.load(f)


def scan_and_score(screen, limit=400, market="india"):
    """Fetch a universe from a captured screen, score + tier."""
    total, rows = fetch_universe(screen, limit=limit, market=market)
    for r in rows:
        coarse_score(r)
        r["tier"] = tier(r)
    rows = [r for r in rows if r["tier"] != "drop"]
    rows.sort(key=lambda r: (-r["score"], r.get("off_high_1m") or 99))
    return total, rows


def write_output(outdir, slug, title, total, rows):
    today = datetime.date.today().isoformat()
    os.makedirs(outdir, exist_ok=True)
    # Intermediate JSON -> one stable file per screen, overwritten every run, so
    # Stage 2 always reads the latest without date-guessing. The markdown is the
    # human-readable final result, so it is dated to preserve the daily history.
    json_path = os.path.join(outdir, f"qmomentum_{slug}.json")
    md_path = os.path.join(outdir, f"qmomentum_{slug}_{today}.md")
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
    """Write a screen config from a screener_capture output JSON."""
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


def render_markdown(today, total, rows, title="Built-in Qullamaggie filter"):
    tiers = {"actionable": [], "building": [], "early": []}
    for r in rows:
        tiers[r["tier"]].append(r)
    out = [f"# Qullamaggie Momentum Scan — {title} — {today}",
           f"\nUniverse: **{total}** names from the screen -> **{len(rows)}** setups "
           f"(actionable {len(tiers['actionable'])} · building "
           f"{len(tiers['building'])} · early {len(tiers['early'])})\n"]
    labels = {"actionable": "🟢 Actionable — tight & ready",
              "building": "🟡 Building — tightening",
              "early": "🟠 Early — pole formed, still digesting"}
    for t in ("actionable", "building", "early"):
        if not tiers[t]:
            continue
        out.append(f"\n## {labels[t]}\n")
        out.append("| # | Symbol | Gate | Close | Day% | 1M% | 3M% | %below 1M-high | 10SMA (ADR) | Notes |")
        out.append("|--:|--------|-----:|------:|-----:|----:|----:|---------------:|------------:|-------|")
        for i, r in enumerate(tiers[t], 1):
            ext_adr = r.get("ext_s10_adr")
            out.append("| {} | {} | {} | {} | {:+.1f} | {:.0f} | {:.0f} | {} | {} | {} |".format(
                i, r["sym"].split(":")[1], r["score"], r["close"], r["chg"] or 0,
                r["p1"] or 0, r["p3"] or 0,
                f"{r['off_high_1m']:.1f}%" if r["off_high_1m"] is not None else "—",
                f"{ext_adr:+.1f}" if ext_adr is not None else "—",
                "; ".join(r["why"][:3])))
    out.append("\n> Stage-1 snapshot scan. Run Stage-2 (bars + screenshots) on the "
               "🟢 Actionable tier before trading — see SKILL.md.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Stage 2 — precise tightness from daily bars
# ---------------------------------------------------------------------------

def sma(vals, n):
    w = vals[-n:]
    return sum(w) / len(w)


def refine_one(sym, bars):
    """bars: list of [time, open, high, low, close, volume] oldest->newest."""
    if len(bars) < 30:
        return {"sym": sym, "error": "insufficient bars"}
    h = [b[2] for b in bars]; l = [b[3] for b in bars]
    c = [b[4] for b in bars]; v = [b[5] for b in bars]
    n = len(bars)

    adr = sum((h[i] - l[i]) / c[i] for i in range(n - 20, n)) / 20 * 100  # ADR%(20)
    hi_i = max(range(max(0, n - 60), n), key=lambda i: c[i])  # swing high on closes
    lo_before = min(c[max(0, hi_i - 40):hi_i + 1]) if hi_i > 0 else c[hi_i]
    pole = (c[hi_i] - lo_before) / lo_before * 100 if lo_before else 0
    consol_len = n - 1 - hi_i                       # bars since the highest close
    since_low = min(c[hi_i:]) if hi_i < n else c[-1]
    pullback = (c[hi_i] - since_low) / c[hi_i] * 100  # deepest CLOSING drawdown in the base

    rng5 = (max(h[-5:]) - min(l[-5:])) / c[-1] * 100  # recent range — high-low, to match ADR
    tight = rng5 / adr if adr else None             # 5-day range as x ADR (lower=tighter)
    rolling_over = c[-1] < c[-2] < c[-3]
    hl = (min(c[-5:]) > min(c[-10:-5]) and not rolling_over) if n >= 10 else None
    vdry = (sum(v[-5:]) / 5) / (sum(v[-20:]) / 20) if sum(v[-20:]) else None
    stacked = sma(c, 10) > sma(c, 20) > sma(c, 50)

    # Precise tightness score (0-100)
    s = 0
    if stacked: s += 20
    if 30 <= pole <= 150: s += 15
    elif pole > 150: s += 6
    if 5 <= consol_len <= 40: s += 15
    if pullback <= 25: s += 15
    elif pullback <= 35: s += 6
    if tight is not None:
        if tight <= 2.0: s += 20
        elif tight <= 3.0: s += 12
        elif tight <= 4.0: s += 4
    if hl: s += 8
    if vdry is not None and vdry < 1.0: s += 7

    return {"sym": sym, "precise_score": min(s, 100), "close": round(c[-1], 2),
            "adr_pct": round(adr, 2),
            "pole_pct": round(pole, 1), "consol_days": consol_len,
            "pullback_pct": round(pullback, 1), "range5_over_adr": round(tight, 2) if tight else None,
            "higher_lows": hl, "vol_dryup": round(vdry, 2) if vdry else None,
            "mas_stacked": stacked,
            "trigger": round(max(h[-max(consol_len,1):]) if consol_len else h[-1], 2)}


# The refiner owns ONLY this table block in the dated report (exact, rules-based
# numbers). The qualitative read above it — cleanest coils / watch-outs — is
# authored by the model, so it lives OUTSIDE these markers and is never touched
# when a re-run replaces the table.
S2_TABLE_START = "<!-- stage2-table:start -->"
S2_TABLE_END = "<!-- stage2-table:end -->"


def render_refined_table(date, good, errors, title):
    """The full ranked table as markdown — deterministic, so the numbers are
    exact and no model transcription is involved. No interpretive digest here:
    that's the model's job (see SKILL.md)."""
    out = [f"## Stage 2 — Refined Ranking ({title}) — {date}", ""]
    out.append(f"Ranked by `precise_score` (bar-derived tightness — the sole ranking). "
               f"**{len(good)}** names refined"
               + (f", {len(errors)} skipped (insufficient bars)." if errors else "."))
    out.append("")
    out.append("| # | Symbol | Score | Gate | Close | Trigger | Stop~ | ADR% | Pole% | "
               "Consol | Pull% | 5d/ADR | HL | VolDry | Stacked |")
    out.append("|--:|--------|------:|------|------:|--------:|------:|-----:|------:|"
               "-------:|------:|-------:|:--:|-------:|:------:|")
    for i, r in enumerate(good, 1):
        adr = r.get("adr_pct"); trig = r.get("trigger")
        # Qullamaggie stop ≈ 1× ADR below the breakout entry.
        stop = round(trig * (1 - adr / 100), 2) if (trig and adr) else None
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            i, r["sym"].split(":")[-1], r["precise_score"], r.get("gate", "—"),
            r.get("close"), trig, stop if stop is not None else "—",
            adr, r.get("pole_pct"), r.get("consol_days"), r.get("pullback_pct"),
            r.get("range5_over_adr"), "✓" if r.get("higher_lows") else "·",
            r.get("vol_dryup"), "✓" if r.get("mas_stacked") else "·"))
    return "\n".join(out)


def upsert_section(path, block, start, end):
    """Write `block` into a marker-delimited section of the dated report so a
    re-run replaces that section instead of appending a duplicate. Only ever
    touches the region between `start`/`end`, leaving the model's read intact.
    Creates the file if the Stage-1 report isn't there yet."""
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
    """Stage 2 is the sole ranker: it ranks purely on bar-derived tightness
    (`precise_score`). Stage 1 already did the filtering — it decided which
    names were worth pulling bars for — so no Stage-1 number feeds the ranking.
    Pass --scan to attach each name's gate bucket as a label AND to locate the
    dated report whose table block gets refreshed."""
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
        report_path = os.path.join(args.outdir, f"qmomentum_{slugify(title)}_{sdate}.md")

    results = []
    for sym, bars in bars_map.items():
        res = refine_one(sym, bars)
        if "error" not in res and sym in gate:
            res["gate"] = gate[sym]   # Stage-1 filter bucket (label only, not scored)
        results.append(res)
    good = [r for r in results if "error" not in r]
    good.sort(key=lambda r: -r["precise_score"])
    errors = [r for r in results if "error" in r]

    # Full ranking -> one stable JSON (overwritten each run), out of context.
    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, "refined.json")
    with open(out_path, "w") as f:
        json.dump({"date": sdate, "screen": title, "refined": good, "errors": errors},
                  f, indent=2)

    # Exact ranked table -> the dated report, in the refiner's own marked block.
    # The model then writes its interpretive read ABOVE this block (see SKILL.md).
    if report_path:
        upsert_section(report_path, render_refined_table(sdate, good, errors, title),
                       S2_TABLE_START, S2_TABLE_END)

    print(f"Stage 2 refined: {len(good)} ranked, {len(errors)} skipped -> {out_path}"
          + (f"; table -> {report_path} (now author the read above the table)"
             if report_path else ""))


def main():
    p = argparse.ArgumentParser(description="Qullamaggie momentum scanner")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Stage 1: fetch universe + coarse score")
    s.add_argument("--screen", help="screen config JSON (captured filter). "
                   "Omit to use the built-in Qullamaggie filter.")
    s.add_argument("--limit", type=int, default=400)
    s.add_argument("--min-perf-1m", type=float, default=15.0)
    s.add_argument("--min-price", type=float, default=30.0)
    s.add_argument("--min-value", type=float, default=1e8, help="min daily traded value")
    s.add_argument("--market", default="india")
    s.add_argument("--outdir", default="out")
    s.set_defaults(func=run_scan)

    sa = sub.add_parser("scan-all", help="Stage 1 across every screen in screens/")
    sa.add_argument("--screens", default="screens", help="dir of screen JSON files")
    sa.add_argument("--limit", type=int, default=400)
    sa.add_argument("--outdir", default="out")
    sa.set_defaults(func=run_scan_all)

    a = sub.add_parser("add-screen", help="save a screen from a screener_capture output")
    a.add_argument("--from-capture", required=True, help="JSON output of the screener_capture MCP tool")
    a.add_argument("--name", help="screen display name")
    a.add_argument("--url", help="screener share URL")
    a.add_argument("--screens", default="screens", help="dir to write the screen JSON")
    a.set_defaults(func=run_add_screen)

    r = sub.add_parser("refine", help="Stage 2: precise tightness from bars JSON")
    r.add_argument("--bars", required=True, help="JSON: {sym: [[t,o,h,l,c,v],...]}")
    r.add_argument("--scan", help="optional Stage-1 json to merge scores")
    r.add_argument("--outdir", default="out", help="dir for refined.json (overwritten each run)")
    r.set_defaults(func=run_refine)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
