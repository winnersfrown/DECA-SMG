"""Daily performance tracking, benchmark comparison and pitch-deck charts.

Two jobs:

1. **During the game** -- keep a dated record of Total Equity and Percent
   Return so you can see whether you are tracking toward the qualifying bar
   while there is still time to act on it.

2. **After the game** -- produce Section II of the pitch deck. The rubric
   awards 15 points for analysing performance "using relevant data and
   evidence," and portfolios are deleted on May 10, 2027. A team that never
   logged anything is reconstructing its own history from memory the week
   before ICDC. Log from day one.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .portfolio import Portfolio
from .rules import (
    BENCHMARK_TICKERS,
    COMPETITION_END,
    COMPETITION_START,
    RANKING_BENCHMARK,
    STARTING_CASH,
    trading_days_remaining,
)


@dataclass
class Snapshot:
    """One day's record."""

    date: _dt.date
    total_equity: float
    percent_return: float
    cash: float
    margin_loan: float
    leverage: float
    benchmark: float = 0.0        # cumulative benchmark return since start
    note: str = ""

    @property
    def excess(self) -> float:
        """Return above the ranking benchmark -- what actually decides rank."""
        return round(self.percent_return - self.benchmark, 6)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(
            date=_dt.date.fromisoformat(d["date"]),
            total_equity=float(d["total_equity"]),
            percent_return=float(d["percent_return"]),
            cash=float(d["cash"]),
            margin_loan=float(d["margin_loan"]),
            leverage=float(d["leverage"]),
            benchmark=float(d.get("benchmark", 0.0)),
            note=d.get("note", ""),
        )


class PerformanceLog:
    """An append-only daily log, persisted as JSON."""

    def __init__(self, path: str | Path = "performance_log.json"):
        self.path = Path(path)
        self.snapshots: list[Snapshot] = []
        if self.path.exists():
            try:
                self.snapshots = [
                    Snapshot.from_dict(d) for d in json.loads(self.path.read_text())
                ]
            except (json.JSONDecodeError, KeyError, ValueError):
                self.snapshots = []

    def record(
        self, pf: Portfolio, benchmark_return: float = 0.0,
        when: _dt.date | None = None, note: str = "",
    ) -> Snapshot:
        """Record today's state, replacing any existing entry for that date."""
        when = when or pf.as_of or _dt.date.today()
        snap = Snapshot(
            date=when,
            total_equity=pf.total_equity,
            percent_return=pf.percent_return,
            cash=round(pf.cash, 2),
            margin_loan=pf.margin_loan,
            leverage=pf.leverage,
            benchmark=round(benchmark_return, 6),
            note=note,
        )
        self.snapshots = [s for s in self.snapshots if s.date != when]
        self.snapshots.append(snap)
        self.snapshots.sort(key=lambda s: s.date)
        self.save()
        return snap

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([s.to_dict() for s in self.snapshots], indent=2)
        )

    def export_csv(self, path: str | Path) -> Path:
        """CSV export -- for a spreadsheet, or for the appendix."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["date", "total_equity", "percent_return", "benchmark_return",
                 "excess_return", "cash", "margin_loan", "leverage", "note"]
            )
            for s in self.snapshots:
                w.writerow(
                    [s.date, f"{s.total_equity:.2f}", f"{s.percent_return:.6f}",
                     f"{s.benchmark:.6f}", f"{s.excess:.6f}", f"{s.cash:.2f}",
                     f"{s.margin_loan:.2f}", f"{s.leverage:.4f}", s.note]
                )
        return p

    # ---------------- statistics ----------------

    def stats(self) -> dict:
        """Summary statistics for the deck and for the judge's questions."""
        if not self.snapshots:
            return {}
        rets = [s.percent_return for s in self.snapshots]
        eq = [s.total_equity for s in self.snapshots]

        daily = [
            (eq[i] / eq[i - 1]) - 1.0
            for i in range(1, len(eq))
            if eq[i - 1] > 0
        ]
        peak, mdd = -float("inf"), 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1.0)

        best = max(self.snapshots, key=lambda s: s.percent_return)
        worst = min(self.snapshots, key=lambda s: s.percent_return)
        latest = self.snapshots[-1]

        mean = sum(daily) / len(daily) if daily else 0.0
        var = (
            sum((d - mean) ** 2 for d in daily) / (len(daily) - 1)
            if len(daily) > 1 else 0.0
        )
        vol_ann = (var ** 0.5) * (252 ** 0.5)

        return {
            "days_logged": len(self.snapshots),
            "current_return": round(latest.percent_return, 4),
            "current_equity": latest.total_equity,
            "benchmark_return": round(latest.benchmark, 4),
            "excess_return": round(latest.excess, 4),
            "best_day": (best.date.isoformat(), round(best.percent_return, 4)),
            "worst_day": (worst.date.isoformat(), round(worst.percent_return, 4)),
            "max_drawdown": round(mdd, 4),
            "realized_vol_annualized": round(vol_ann, 4),
            "best_daily_move": round(max(daily), 4) if daily else 0.0,
            "worst_daily_move": round(min(daily), 4) if daily else 0.0,
            "trading_days_remaining": trading_days_remaining(),
        }

    def render(self) -> str:
        s = self.stats()
        if not s:
            return "No snapshots logged yet. Run `smg track` daily."
        lines = [
            "=" * 72,
            "  PERFORMANCE SUMMARY",
            "=" * 72,
            f"  Total Equity          ${s['current_equity']:,.2f}",
            f"  Percent Return        {s['current_return']:+.2%}   <- the ranking metric",
            f"  {RANKING_BENCHMARK:<21} {s['benchmark_return']:+.2%}",
            f"  Excess vs benchmark   {s['excess_return']:+.2%}",
            "",
            f"  Max drawdown          {s['max_drawdown']:+.2%}",
            f"  Realized volatility   {s['realized_vol_annualized']:.1%} annualized",
            f"  Best / worst day      {s['best_daily_move']:+.2%} / "
            f"{s['worst_daily_move']:+.2%}",
            f"  Days logged           {s['days_logged']}",
            f"  Trading days left     {s['trading_days_remaining']}",
            "=" * 72,
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Charts -- Section II of the pitch deck
# --------------------------------------------------------------------------


def make_charts(
    log: PerformanceLog,
    pf: Portfolio | None = None,
    outdir: str | Path = "charts",
    prefix: str = "",
) -> list[Path]:
    """Render the performance charts the rubric asks for.

    Section II requires "charts and diagrams of performance," and Presentation
    Design is another 5 points. Produces PNGs at presentation resolution.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        raise ImportError("charts require matplotlib -- `pip install matplotlib`")

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if not log.snapshots:
        return written

    pct = FuncFormatter(lambda v, _: f"{v:.0%}")
    dates = [s.date for s in log.snapshots]
    rets = [s.percent_return for s in log.snapshots]
    bench = [s.benchmark for s in log.snapshots]
    eq = [s.total_equity for s in log.snapshots]

    plt.rcParams.update({
        "figure.dpi": 130, "savefig.dpi": 160, "font.size": 11,
        "axes.grid": True, "grid.alpha": 0.25, "axes.spines.top": False,
        "axes.spines.right": False,
    })

    # 1. Percent Return vs the ranking benchmark -- the headline chart.
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(dates, rets, lw=2.4, color="#1f6feb", label="Our portfolio")
    ax.plot(dates, bench, lw=1.8, color="#8b949e", ls="--", label=RANKING_BENCHMARK)
    ax.axhline(0, color="#57606a", lw=1)
    ax.fill_between(dates, rets, bench,
                    where=[r >= b for r, b in zip(rets, bench)],
                    color="#2da44e", alpha=0.16, interpolate=True)
    ax.fill_between(dates, rets, bench,
                    where=[r < b for r, b in zip(rets, bench)],
                    color="#cf222e", alpha=0.16, interpolate=True)
    ax.set_title("Percent Return vs Ranking Benchmark", fontweight="bold", pad=14)
    ax.set_ylabel("Cumulative return")
    ax.yaxis.set_major_formatter(pct)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    p = out / f"{prefix}01_return_vs_benchmark.png"
    fig.savefig(p); plt.close(fig); written.append(p)

    # 2. Equity curve.
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(dates, eq, lw=2.4, color="#1f6feb")
    ax.axhline(STARTING_CASH, color="#57606a", ls=":", lw=1.4,
               label=f"${STARTING_CASH:,.0f} start")
    ax.set_title("Total Equity Over the Competition", fontweight="bold", pad=14)
    ax.set_ylabel("Total Equity")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    p = out / f"{prefix}02_equity_curve.png"
    fig.savefig(p); plt.close(fig); written.append(p)

    # 3. Drawdown -- shows the judge you understand your own risk.
    peak, dd = -float("inf"), []
    for v in eq:
        peak = max(peak, v)
        dd.append(v / peak - 1.0 if peak > 0 else 0.0)
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.fill_between(dates, dd, 0, color="#cf222e", alpha=0.3)
    ax.plot(dates, dd, lw=1.6, color="#cf222e")
    ax.set_title("Drawdown from Peak", fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(pct)
    fig.autofmt_xdate()
    fig.tight_layout()
    p = out / f"{prefix}03_drawdown.png"
    fig.savefig(p); plt.close(fig); written.append(p)

    if pf is not None and pf.holdings:
        # 4. Allocation by security, coloured by asset class.
        conc = pf.concentration()
        syms = sorted(conc, key=lambda s: -conc[s])
        vals = [conc[s] for s in syms]
        cmap = {"stock": "#1f6feb", "mutual_fund": "#8250df", "bond": "#bf8700"}
        cls = {}
        for h in pf.holdings:
            cls[h.symbol] = h.asset_class.value
        colors = [cmap.get(cls.get(s, "stock"), "#1f6feb") for s in syms]

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(syms, vals, color=colors)
        ax.axhline(0.30, color="#cf222e", ls="--", lw=1.5,
                   label="30% single-security cap")
        ax.set_title("Allocation as a Share of Total Equity",
                     fontweight="bold", pad=14)
        ax.yaxis.set_major_formatter(pct)
        ax.legend(frameon=False)
        handles = [
            plt.Rectangle((0, 0), 1, 1, color=c)
            for c in ("#1f6feb", "#8250df", "#bf8700")
        ]
        ax.legend(
            handles + [plt.Line2D([0], [0], color="#cf222e", ls="--")],
            ["Stock / ETF", "Mutual fund", "Bond", "30% cap"],
            frameon=False, ncol=4, fontsize=9,
        )
        fig.tight_layout()
        p = out / f"{prefix}04_allocation.png"
        fig.savefig(p); plt.close(fig); written.append(p)

        # 5. Contribution to return by position -- answers "what actually worked?"
        contrib = [(h.symbol, h.unrealized) for h in pf.holdings]
        contrib.sort(key=lambda kv: kv[1])
        names = [c[0] for c in contrib]
        vals2 = [c[1] for c in contrib]
        fig, ax = plt.subplots(figsize=(10, max(3.5, 0.45 * len(names) + 1.6)))
        ax.barh(names, vals2,
                color=["#2da44e" if v >= 0 else "#cf222e" for v in vals2])
        ax.axvline(0, color="#57606a", lw=1)
        ax.set_title("Profit and Loss by Position", fontweight="bold", pad=12)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
        fig.tight_layout()
        p = out / f"{prefix}05_pnl_by_position.png"
        fig.savefig(p); plt.close(fig); written.append(p)

    return written


def benchmark_return_since_start(
    benchmark: str | None = None, start: _dt.date | None = None
) -> float:
    """Cumulative benchmark return from the competition start to the last close."""
    from .data import get_history

    sym = benchmark or BENCHMARK_TICKERS[0]
    start = start or COMPETITION_START.date()
    h = get_history(sym, days=400)
    pairs = [(d, c) for d, c in zip(h.dates, h.closes)
             if start <= d <= COMPETITION_END.date()]
    if len(pairs) < 2:
        return 0.0
    return round(pairs[-1][1] / pairs[0][1] - 1.0, 6)
