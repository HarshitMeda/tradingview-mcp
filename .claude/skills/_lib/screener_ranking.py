#!/usr/bin/env python3
"""
ScreenerRanking — a reusable base for TradingView screen-driven scan/rank skills.

A "scan" takes the user's own captured TradingView screen (the screen IS the
filter), throws out the obvious non-setups on cheap snapshot columns, then ranks
the survivors on precise metrics computed from daily OHLCV bars. That pipeline is
identical across strategies; only the strategy-specific judgement differs. This
module owns the pipeline; each concrete scan is a small subclass.

    from screener_ranking import ScreenerRanking

    class MyScan(ScreenerRanking):
        NAME        = "My Setup"
        FILE_PREFIX = "myscan"
        COLUMNS     = ["name", "close", "change", "SMA10", "SMA50", "High.1M", "ADRP"]
        SCORE_BLURB = "Ranked by `score` — what my setup rewards."

        def annotate(self, r):    ...   # add column-derived context to a row (optional)
        def is_loser(self, r):    ...   # column gate -> drop reason | None
        def bar_metrics(self, sym, bars): ...   # -> metric dict (or {'error': ...})
        def disqualify(self, m):  ...   # bar gate -> drop reason | None
        def readiness(self, m):   ...   # -> score 0..100
        def render_table(self):   ...   # -> markdown table of self.ranked

    if __name__ == "__main__":
        MyScan.main()

The base provides: universe fetch (public scanner API, no auth), eliminate_losses,
score, the dated-report renderer with a marker-preserved read section, the
shortlist/refined JSON I/O, bar helpers (sma/adr20/ohlcv), and the full CLI
(scan / scan-all / add-screen / rank). Because Python can't call the TradingView
MCP, `scan` (columns -> shortlist) and `rank` (bars -> ranking) are separate
invocations bridged by a model-driven `data_get_ohlcv_batch` bar fetch.
"""
from __future__ import annotations
import abc, argparse, datetime, glob, json, os, re, sys, urllib.request


def pct(a, b):
    return (a - b) / b * 100.0 if b else None


class ScreenerRanking(abc.ABC):
    # ---- subclass contract: class attributes ----------------------------------
    NAME = "Screen Ranking"           # human title (fallback when a screen has no name)
    FILE_PREFIX = "scan"              # output file prefix: <prefix>_<slug>[_<date>].{json,md}
    COLUMNS = ["name", "close", "change", "SMA10", "SMA20", "SMA50", "High.1M", "ADRP"]
    DEFAULT_SORT = {"sortBy": "Perf.1M", "sortOrder": "desc"}
    SCORE_BLURB = "Ranked by `score`. This is the sole ranking."
    MIN_BARS = 30

    # Scanner column name -> the key it lands under in each row dict. A superset;
    # a subclass just lists the COLUMNS it needs and gets those keys populated.
    COL_MAP = {
        "name": "name", "close": "close", "change": "chg",
        "Perf.1M": "p1", "Perf.3M": "p3", "Perf.6M": "p6",
        "SMA10": "s10", "SMA20": "s20", "SMA50": "s50", "SMA200": "s200",
        "High.1M": "h1", "Low.1M": "l1", "High.3M": "h3", "Low.3M": "l3", "High.6M": "h6",
        "Volatility.D": "vol", "ADRP": "adrp", "Value.Traded": "value",
        "relative_volume_10d_calc": "rvol",
    }

    TABLE_START = "<!-- rank-table:start -->"
    TABLE_END = "<!-- rank-table:end -->"

    def __init__(self, rows=None):
        self.rows = rows or []
        self.survivors = []      # eliminate_losses output (shortlist)
        self.ranked = []         # score output, best-first
        self.dropped = []        # bar-stage drops, each with drop_reason
        self.errors = []         # insufficient-bars etc.

    # ==== abstract hooks — each scan implements these =========================

    def annotate(self, r):
        """Add column-derived context fields to a row (used by is_loser and the
        shortlist). Default: nothing."""

    @abc.abstractmethod
    def is_loser(self, r):
        """Column gate. Return a drop-reason string, or None to keep the row."""

    @abc.abstractmethod
    def bar_metrics(self, sym, bars):
        """Compute the strategy's metric dict from daily bars (oldest->newest).
        Return {'sym':..., 'error': '...'} when there aren't enough bars."""

    @abc.abstractmethod
    def disqualify(self, m):
        """Bar gate. Return a drop-reason string, or None to keep + score."""

    @abc.abstractmethod
    def readiness(self, m):
        """Score a metric dict 0..100 (higher = readier). This drives the rank."""

    @abc.abstractmethod
    def render_table(self, ranked):
        """Return the markdown table (header + rows) for the ranked list."""

    # ==== pipeline — shared ===================================================

    def eliminate_losses(self):
        """Cheap column gate over the whole universe -> shortlist worth pulling
        bars for. No bars, no app needed."""
        keep = []
        for r in self.rows:
            self.annotate(r)
            reason = self.is_loser(r)
            if reason:
                r["drop_reason"] = reason
            else:
                keep.append(r)
        self.survivors = keep
        return keep

    def score(self, bars_map):
        """The sole ranking, from bars alone. Drops disqualified names, scores the
        rest, returns them best-first. bars_map: {sym: [[t,o,h,l,c,v], ...]}."""
        ranked = []
        for sym, bars in bars_map.items():
            m = self.bar_metrics(sym, bars)
            if m.get("error"):
                self.errors.append(m); continue
            reason = self.disqualify(m)
            if reason:
                m["drop_reason"] = reason; self.dropped.append(m); continue
            m["score"] = self.readiness(m)
            ranked.append(m)
        ranked.sort(key=lambda r: -r["score"])
        self.ranked = ranked
        return ranked

    # ==== bar helpers — shared ================================================

    @staticmethod
    def ohlcv(bars):
        """Split bars into (highs, lows, closes, volumes)."""
        return ([b[2] for b in bars], [b[3] for b in bars],
                [b[4] for b in bars], [b[5] for b in bars])

    @staticmethod
    def sma(vals, n):
        w = vals[-n:]
        return sum(w) / len(w)

    @staticmethod
    def adr20(h, l, c):
        """Average Daily Range % over the last 20 bars."""
        n = len(c)
        return sum((h[i] - l[i]) / c[i] for i in range(n - 20, n)) / 20 * 100

    # ==== fetch — shared ======================================================

    @staticmethod
    def _post_scan(body, market):
        url = f"https://scanner.tradingview.com/{market}/scan?label-product=screener-stock"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "text/plain;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    @classmethod
    def fetch_universe(cls, screen, limit=400, market="india"):
        """Replay a captured screen (filter/filter2/sort/market), overriding only
        the columns with this scan's COLUMNS, so the universe matches the user's
        real TradingView screen. A screen is required — no fallback filter."""
        if not screen:
            raise ValueError("fetch_universe requires a screen. Capture one with "
                             "the screener_capture MCP tool, then add-screen.")
        market = screen.get("market", market)
        cols = cls.COLUMNS if "name" in cls.COLUMNS else ["name", *cls.COLUMNS]
        body = {"columns": cols, "range": [0, limit]}
        flt = list(screen.get("filter") or [])
        if not any(c.get("left") == "is_primary" for c in flt):   # primary listing only (NSE>BSE)
            flt.append({"left": "is_primary", "operation": "equal", "right": True})
        body["filter"] = flt
        if screen.get("filter2") is not None:
            body["filter2"] = screen["filter2"]
        body["sort"] = screen.get("sort") or cls.DEFAULT_SORT
        body["markets"] = [market]
        payload = cls._post_scan(body, market)
        idx = {c: i for i, c in enumerate(cols)}
        rows = []
        for o in payload.get("data", []):
            d = o["d"]
            row = {"sym": o["s"], "exchange": o["s"].split(":")[0]}
            for col in cols:
                row[cls.COL_MAP.get(col, col)] = d[idx[col]]
            rows.append(row)
        return payload.get("totalCount", len(rows)), rows

    # ==== rendering — shared ==================================================

    def to_markdown(self, date, title, funnel=None):
        """The final ranked report the user consumes. Deterministic numbers only;
        the qualitative read is authored by the model below this block."""
        funnel = funnel or {}
        parts = []
        if funnel.get("universe") is not None:
            parts.append(f"**{funnel['universe']} universe**")
        if funnel.get("shortlist") is not None:
            parts.append(f"{funnel['shortlist']} shortlist")
        parts.append(f"{len(self.ranked)} ranked")
        drops = []
        if self.dropped:
            names = ", ".join(f"{r['sym'].split(':')[-1]} ({r['drop_reason']})"
                              for r in self.dropped[:8])
            drops.append(f"{len(self.dropped)} dropped: {names}"
                         + (" …" if len(self.dropped) > 8 else ""))
        if self.errors:
            drops.append(f"{len(self.errors)} insufficient bars")
        out = [f"## Ranking ({title}) — {date}", "",
               "Funnel: " + " → ".join(parts) + (" (" + "; ".join(drops) + ")." if drops else "."),
               self.SCORE_BLURB, "", self.render_table(self.ranked)]
        return "\n".join(out)

    def render_shortlist(self, date, total, title):
        """Phase-1 output: the eliminate_losses survivors (not a ranking)."""
        out = [f"# {title} — {date}",
               f"\nUniverse: **{total}** from the screen -> **{len(self.survivors)}** "
               f"survive elimination (worth pulling bars for).\n",
               "> Column pre-filter only. Pull daily bars for these and run `rank` "
               "for the actual ranking — see SKILL.md.\n",
               "| # | Symbol | Close | Day% | 1M% | 3M% |",
               "|--:|--------|------:|-----:|----:|----:|"]
        for i, r in enumerate(self.survivors, 1):
            out.append("| {} | {} | {} | {:+.1f} | {:.0f} | {:.0f} |".format(
                i, r["sym"].split(":")[-1], r.get("close"), r.get("chg") or 0,
                r.get("p1") or 0, r.get("p3") or 0))
        return "\n".join(out)

    # ==== I/O helpers — shared ================================================

    @staticmethod
    def slugify(name):
        return re.sub(r"[^a-z0-9]+", "_", (name or "screen").lower()).strip("_") or "screen"

    def upsert_section(self, path, block):
        """Write `block` into the marker-delimited table section of the dated
        report so a re-run replaces only the table and leaves the model's read
        (written below the end marker) untouched."""
        start, end = self.TABLE_START, self.TABLE_END
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

    def _shortlist_path(self, outdir, slug):
        return os.path.join(outdir, f"{self.FILE_PREFIX}_{slug}.json")

    def _report_path(self, outdir, slug, date):
        return os.path.join(outdir, f"{self.FILE_PREFIX}_{slug}_{date}.md")

    # ==== CLI — shared ========================================================

    @classmethod
    def _load_screen(cls, path):
        with open(path) as f:
            return json.load(f)

    @classmethod
    def run_scan(cls, args):
        if not args.screen:
            print("A screen is required. Capture one with the screener_capture "
                  "MCP tool, then add-screen.", file=sys.stderr)
            return
        screen = cls._load_screen(args.screen)
        title = screen.get("name") or cls.NAME
        slug = cls.slugify(title)
        today = datetime.date.today().isoformat()
        total, rows = cls.fetch_universe(screen, limit=args.limit, market=args.market)
        eng = cls(rows)
        eng.eliminate_losses()

        os.makedirs(args.outdir, exist_ok=True)
        with open(eng._shortlist_path(args.outdir, slug), "w") as f:
            json.dump({"date": today, "screen": title, "universe": total,
                       "shortlist": len(eng.survivors), "rows": eng.survivors}, f, indent=2)
        print(eng.render_shortlist(today, total, title))
        syms = " ".join(r["sym"] for r in eng.survivors)
        print(f"\n{len(eng.survivors)} survive -> pull bars for these, then `rank`:\n{syms}",
              file=sys.stderr)

    @classmethod
    def run_scan_all(cls, args):
        files = sorted(glob.glob(os.path.join(args.screens, "*.json")))
        if not files:
            print(f"No screen files in {args.screens}/ — capture one with the "
                  "screener_capture MCP tool, then add-screen.", file=sys.stderr)
            return
        today = datetime.date.today().isoformat()
        os.makedirs(args.outdir, exist_ok=True)
        for path in files:
            screen = cls._load_screen(path)
            title = screen.get("name") or os.path.basename(path)
            slug = cls.slugify(title)
            total, rows = cls.fetch_universe(screen, limit=args.limit)
            eng = cls(rows)
            eng.eliminate_losses()
            with open(eng._shortlist_path(args.outdir, slug), "w") as f:
                json.dump({"date": today, "screen": title, "universe": total,
                           "shortlist": len(eng.survivors), "rows": eng.survivors}, f, indent=2)
            print(f"{title}: {total} universe -> {len(eng.survivors)} shortlist "
                  f"[{eng._shortlist_path(args.outdir, slug)}]")

    @classmethod
    def run_rank(cls, args):
        with open(args.bars) as f:
            bars_map = json.load(f)
        title, date = cls.NAME, datetime.date.today().isoformat()
        universe = shortlist = None
        if args.scan:
            with open(args.scan) as f:
                sl = json.load(f)
            title = sl.get("screen", title)
            date = sl.get("date") or date
            universe, shortlist = sl.get("universe"), sl.get("shortlist")
        slug = cls.slugify(title)

        eng = cls()
        eng.score(bars_map)
        funnel = {"universe": universe, "shortlist": shortlist or len(bars_map)}

        os.makedirs(args.outdir, exist_ok=True)
        with open(os.path.join(args.outdir, "refined.json"), "w") as f:
            json.dump({"date": date, "screen": title, "ranked": eng.ranked,
                       "dropped": eng.dropped, "errors": eng.errors}, f, indent=2)
        report = eng._report_path(args.outdir, slug, date)
        eng.upsert_section(report, eng.to_markdown(date, title, funnel))
        print(f"Ranked {len(eng.ranked)} ({len(eng.dropped)} dropped, "
              f"{len(eng.errors)} skipped) -> {os.path.join(args.outdir, 'refined.json')}; "
              f"table -> {report} (now author the read below the table)")

    @staticmethod
    def run_add_screen(args):
        with open(args.from_capture) as f:
            cap = json.load(f)
        screen = {
            "name": args.name or cap.get("name") or "Untitled screen",
            "url": args.url or cap.get("sourceUrl") or cap.get("url"),
            "market": cap["market"], "filter": cap.get("filter"),
            "filter2": cap.get("filter2"), "sort": cap.get("sort"),
            "captured_at": cap.get("capturedAt", datetime.date.today().isoformat()),
        }
        os.makedirs(args.screens, exist_ok=True)
        path = os.path.join(args.screens, ScreenerRanking.slugify(screen["name"]) + ".json")
        with open(path, "w") as f:
            json.dump(screen, f, indent=2)
        print(f"Wrote {path}  (market={screen['market']})")

    @classmethod
    def main(cls, argv=None):
        p = argparse.ArgumentParser(description=f"{cls.NAME} scanner")
        sub = p.add_subparsers(dest="cmd", required=True)

        s = sub.add_parser("scan", help="Phase 1: eliminate_losses over the universe -> shortlist")
        s.add_argument("--screen", help="screen config JSON (captured filter)")
        s.add_argument("--limit", type=int, default=400)
        s.add_argument("--market", default="india")
        s.add_argument("--outdir", default="out")
        s.set_defaults(func=cls.run_scan)

        sa = sub.add_parser("scan-all", help="Phase 1 across every screen in screens/")
        sa.add_argument("--screens", default="screens")
        sa.add_argument("--limit", type=int, default=400)
        sa.add_argument("--outdir", default="out")
        sa.set_defaults(func=cls.run_scan_all)

        a = sub.add_parser("add-screen", help="save a screen from a screener_capture output")
        a.add_argument("--from-capture", required=True)
        a.add_argument("--name")
        a.add_argument("--url")
        a.add_argument("--screens", default="screens")
        a.set_defaults(func=cls.run_add_screen)

        r = sub.add_parser("rank", help="Phase 2: score the shortlist from bars (the ranking)")
        r.add_argument("--bars", required=True, help="JSON: {sym: [[t,o,h,l,c,v],...]}")
        r.add_argument("--scan", help="Phase-1 shortlist json (title/date/funnel + report path)")
        r.add_argument("--outdir", default="out")
        r.set_defaults(func=cls.run_rank)

        args = p.parse_args(argv)
        args.func(args)
