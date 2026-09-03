"""Candidate screening, scored against the actual objective.

A conventional screener ranks by quality, value or risk-adjusted return. None of
those is what this competition pays for. This one scores every candidate by the
question that decides your rank:

    "If I built a concentrated portfolio out of names like this one, what would
     my probability of finishing top 25 in the region be?"

That single change reorders the list completely. A steady mega-cap with a high
Sharpe ratio scores poorly. A volatile mid-cap whose moves are largely its own
scores well -- not because it is a better company, but because it can move you
relative to the field, which is the only thing rankings measure.

IMPORTANT -- this is a research tool, not a portfolio.
The guidelines require that "each team must complete their own research and
portfolios must be distinct and reflect the individual team's investment
strategy," and portfolios are reviewed for exactly this. The universes below
are starting points for your own analysis. Every screen output includes the
reasoning behind the score so you can form -- and defend to a judge -- your own
view. Copying a ranked list without that work is both a weak pitch and a rules
problem.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data import PriceHistory, get_many, get_history
from .rules import AssetClass, BENCHMARK_TICKERS
from .tournament import (
    FactorParams,
    FieldModel,
    FieldSample,
    estimate_factors,
    p_qualify,
    sample_field,
)

# --------------------------------------------------------------------------
# Starting universes -- research inputs, not recommendations
# --------------------------------------------------------------------------

UNIVERSE_HIGH_VOL_EQUITY = [
    # Liquid names that historically carry large idiosyncratic moves. Screen
    # them yourself -- volatility regimes change, and last year's mover is
    # often this year's laggard.
    "TSLA", "NVDA", "AMD", "PLTR", "COIN", "MSTR", "RIVN", "LCID", "SOFI",
    "RBLX", "SNAP", "U", "AFRM", "UPST", "CVNA", "ENPH", "FSLR", "MARA",
    "RIOT", "SMCI", "ARM", "IONQ", "RKLB", "ASTS", "HOOD", "DKNG", "ROKU",
    "SHOP", "NET", "CRWD", "DDOG", "SNOW", "MDB", "ZS", "OKTA", "TTD",
]

UNIVERSE_ETF = [
    # ETFs count as STOCKS for diversification -- including bond ETFs.
    # Leveraged/thematic ETFs concentrate volatility without single-name
    # blow-up risk; they are still stocks under the rules.
    "SPY", "QQQ", "IWM", "IVW", "ARKK", "SOXX", "SMH", "XBI", "TAN",
    "URA", "GDX", "KWEB", "IBIT", "TQQQ", "SOXL", "TECL", "UPRO", "SPXL",
]

UNIVERSE_MUTUAL_FUND = [
    # You are REQUIRED to hold $10,000 in a mutual fund. That capital is
    # forced, so the question is not whether to hold one but which. An
    # aggressive growth or sector fund puts the forced capital to work; a
    # money-market or short-bond fund is a guaranteed drag on Percent Return.
    # (Bond mutual funds count as MUTUAL FUNDS, not bonds -- so a bond fund
    # does NOT satisfy the bond requirement, it just lowers your return.)
    "FSELX", "FSPTX", "FBGRX", "FOCPX", "FDGRX", "FCNTX", "PRHSX", "FSCSX",
    "VITAX", "VGT", "RYVYX", "SWPPX", "VFIAX",
]

UNIVERSE_BOND_NOTE = (
    "Bonds must be actual bonds -- SMG's bond list, typically Treasuries and "
    "corporates, inside the trading screen. A bond ETF (BND, TLT, AGG) counts "
    "as a STOCK and a bond mutual fund counts as a MUTUAL FUND; neither "
    "satisfies the bond minimum. This $10,000 is pure drag on Percent Return, "
    "so hold exactly the minimum and no more -- but hold it, because missing "
    "it is disqualifying."
)

DEFAULT_UNIVERSE = UNIVERSE_HIGH_VOL_EQUITY + UNIVERSE_ETF


@dataclass
class ScreenRow:
    """One screened candidate."""

    symbol: str
    asset_class: AssetClass
    price: float
    beta: float
    idio_vol: float
    total_vol: float
    idio_share: float
    drift: float
    mom_3m: float
    mom_6m: float
    max_dd: float
    score: float
    source: str
    n_obs: int

    def why(self) -> str:
        """A one-line, judge-defensible explanation of the score."""
        bits = []
        if self.idio_vol >= 0.55:
            bits.append(f"very high idiosyncratic vol ({self.idio_vol:.0%})")
        elif self.idio_vol >= 0.35:
            bits.append(f"high idiosyncratic vol ({self.idio_vol:.0%})")
        else:
            bits.append(f"modest idiosyncratic vol ({self.idio_vol:.0%})")
        bits.append(
            f"{self.idio_share:.0%} of its variance is its own, not the market's"
        )
        if self.beta > 1.5:
            bits.append(f"but a heavy beta of {self.beta:.1f} that moves the bar too")
        if self.mom_3m > 0.15:
            bits.append(f"3-month momentum {self.mom_3m:+.0%}")
        elif self.mom_3m < -0.15:
            bits.append(f"3-month momentum {self.mom_3m:+.0%} (falling)")
        return "; ".join(bits)


def classify(symbol: str) -> AssetClass:
    """Best-effort asset class from the ticker shape.

    US mutual funds use 5-letter tickers ending in X. Everything else is
    treated as a stock/ETF -- correct under the rules, where all ETFs
    (including bond ETFs) are stocks. Always confirm against the SMG trade
    screen, which is the authority.
    """
    s = symbol.upper().strip()
    if len(s) == 5 and s.endswith("X") and s.isalpha():
        return AssetClass.MUTUAL_FUND
    return AssetClass.STOCK


def screen(
    symbols: list[str] | None = None,
    days_remaining: int = 63,
    lookback: int = 252,
    field: FieldModel | None = None,
    benchmark: str | None = None,
    n_sims: int = 12_000,
    assumed_names: int = 4,
    leverage: float = 1.5,
) -> list[ScreenRow]:
    """Rank candidates by their contribution to P(top 25).

    ``assumed_names`` is the size of the hypothetical portfolio the score is
    computed against: a candidate's idiosyncratic volatility is divided by
    sqrt(n) because independent idiosyncratic risks partially cancel. Screening
    against a 4-name portfolio at ``leverage`` asks the right question --
    "how good is this as one leg of a concentrated, fully-margined book?" --
    rather than the meaningless "how good is this on its own?"
    """
    symbols = symbols or DEFAULT_UNIVERSE
    symbols = [s.upper().strip() for s in symbols]

    bench_sym = benchmark or BENCHMARK_TICKERS[0]
    bench = get_history(bench_sym, days=lookback)
    hist = get_many(symbols, days=lookback)

    # Estimate factors symbol-by-symbol so one bad series cannot poison the
    # alignment for every other candidate.
    factors: dict[str, FactorParams] = {}
    for sym, h in hist.items():
        if len(h) < 30:
            continue
        try:
            factors.update(estimate_factors({sym: h}, bench))
        except ValueError:
            continue

    fs = sample_field(days_remaining, field, n_sims=n_sims)
    scale = 1.0 / math.sqrt(max(1, assumed_names))

    rows: list[ScreenRow] = []
    for sym, fp in factors.items():
        h = hist[sym]
        score = p_qualify(
            fs,
            my_beta=fp.beta,
            my_idio_vol=fp.idio_vol * scale,
            my_leverage=leverage,
        )
        rows.append(
            ScreenRow(
                symbol=sym,
                asset_class=classify(sym),
                price=round(h.last, 2),
                beta=fp.beta,
                idio_vol=fp.idio_vol,
                total_vol=fp.total_vol,
                idio_share=fp.idio_share,
                drift=fp.drift,
                mom_3m=round(h.total_return(63), 4),
                mom_6m=round(h.total_return(126), 4),
                max_dd=round(h.max_drawdown(), 4),
                score=round(score, 4),
                source=h.source,
                n_obs=fp.n_obs,
            )
        )

    rows.sort(key=lambda r: -r.score)
    return rows


def render(rows: list[ScreenRow], top: int = 25, show_why: bool = False) -> str:
    """Format a screen as a table."""
    if not rows:
        return "No candidates produced usable data."
    sources = {r.source for r in rows}
    lines = [
        "=" * 104,
        f"  CANDIDATE SCREEN  -  ranked by P(top 25) as one leg of a "
        f"concentrated, margined portfolio",
        f"  data source(s): {', '.join(sorted(sources))}",
        "=" * 104,
        f"  {'#':>3} {'SYM':<7} {'CLASS':<12} {'PRICE':>9} {'BETA':>6} "
        f"{'IDIO':>7} {'IDIO%':>6} {'3M':>8} {'6M':>8} {'MAXDD':>8} {'SCORE':>8}",
        "-" * 104,
    ]
    for i, r in enumerate(rows[:top], 1):
        lines.append(
            f"  {i:>3} {r.symbol:<7} {r.asset_class.value:<12} {r.price:>9,.2f} "
            f"{r.beta:>6.2f} {r.idio_vol:>7.0%} {r.idio_share:>6.0%} "
            f"{r.mom_3m:>+8.1%} {r.mom_6m:>+8.1%} {r.max_dd:>8.0%} {r.score:>8.1%}"
        )
        if show_why:
            lines.append(f"        {r.why()}")
    lines.append("=" * 104)
    lines.append(
        "  IDIO   = annualized idiosyncratic volatility (the part that moves you"
    )
    lines.append(
        "           relative to the field -- market beta lifts the bar too)."
    )
    lines.append(
        "  IDIO%  = share of total variance that is idiosyncratic. Higher is better."
    )
    lines.append(
        "  SCORE  = simulated P(top 25) for a 4-name, 1.5x portfolio of such names."
    )
    lines.append(
        "  Screen output is a research input. Do your own work on anything you buy --"
    )
    lines.append(
        "  the rules require it, and a judge will ask you to defend every position."
    )
    return "\n".join(lines)
