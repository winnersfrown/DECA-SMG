"""Allocation search: maximize P(top 25) subject to every competition rule.

This is a constrained optimizer whose objective is a probability, not a return.
It searches feasible portfolios and keeps the one with the highest simulated
chance of finishing top 25 in the region.

Constraints enforced, all from ``rules.py``:

  * no security above 30% of Total Equity (20% x 1.5)
  * gross exposure at most 150% of equity (50% margin limit)
  * at least $10,000 net cost in each of stock (LONG), mutual fund and bond
  * the bond slug held at exactly the minimum -- it is required drag, so the
    optimizer never allocates a dollar more to it than the rules demand

One consequence worth noticing, because it falls out of the constraints rather
than anyone's opinion: a fully-margined book cannot hold fewer than five
positions. Gross exposure of 150% against a 30% per-name cap requires at least
ceil(1.5 / 0.3) = 5 securities. The rules impose a floor on diversification no
matter how concentrated you want to be.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as _field

import numpy as np

from .rules import (
    AssetClass,
    MARGIN_BORROW_RATE,
    MAX_POSITION_PCT,
    MIN_NET_COST_PER_ASSET_CLASS,
    STARTING_CASH,
    TRANSACTION_FEE,
)
from .tournament import (
    FactorParams,
    FieldModel,
    FieldSample,
    p_qualify,
    portfolio_factors,
    sample_field,
)

MAX_GROSS = 1.0 + MARGIN_BORROW_RATE  # 150% of equity

# A bond, for modelling purposes: nearly no volatility and nearly no return
# over a 3-month horizon. Held only because the rules require it.
BOND_PROXY = FactorParams("BOND", beta=0.05, idio_vol=0.04, total_vol=0.05, drift=0.03)


@dataclass
class Allocation:
    """A proposed portfolio, in weights and in dollars."""

    weights: dict[str, float]              # fraction of equity, signed
    dollars: dict[str, float]
    classes: dict[str, AssetClass]
    beta: float
    idio_vol: float
    gross: float
    p_qualify: float
    equity: float
    days: int
    median_threshold: float
    n_positions: int
    feasible: bool = True
    notes: list[str] = _field(default_factory=list)

    def render(self) -> str:
        lines = [
            "=" * 78,
            f"  PROPOSED ALLOCATION  -  P(top 25) = {self.p_qualify:.1%}",
            "=" * 78,
            f"  Equity ${self.equity:,.0f} | {self.days} trading days left | "
            f"qualifying bar ~{self.median_threshold:+.1%}",
            f"  Portfolio beta {self.beta:.2f} | idiosyncratic vol "
            f"{self.idio_vol:.0%} | gross exposure {self.gross:.0%} of equity",
            f"  {self.n_positions} positions | margin borrowed "
            f"${max(0.0, (self.gross - 1.0)) * self.equity:,.0f}",
            "-" * 78,
            f"  {'SYMBOL':<10} {'CLASS':<13} {'WEIGHT':>9} {'DOLLARS':>14} {'OF CAP':>9}",
            "-" * 78,
        ]
        cap = MAX_POSITION_PCT
        for sym, w in sorted(self.weights.items(), key=lambda kv: -abs(kv[1])):
            lines.append(
                f"  {sym:<10} {self.classes[sym].value:<13} {w:>9.1%} "
                f"${self.dollars[sym]:>13,.0f} {abs(w) / cap:>8.0%}"
            )
        lines.append("-" * 78)
        for n in self.notes:
            lines.append(f"  * {n}")
        lines.append("=" * 78)
        return "\n".join(lines)


def _class_of(sym: str, classes: dict[str, AssetClass]) -> AssetClass:
    return classes.get(sym, AssetClass.STOCK)


def optimize(
    factors: dict[str, FactorParams],
    classes: dict[str, AssetClass],
    days: int,
    equity: float = STARTING_CASH,
    current_return: float = 0.0,
    field: FieldModel | None = None,
    bond_symbol: str = "BOND",
    max_names: int = 8,
    min_names: int = 5,
    n_candidates: int = 4_000,
    n_sims: int = 12_000,
    seed: int = 4242,
    allow_margin: bool = True,
    field_sample: FieldSample | None = None,
) -> Allocation:
    """Search feasible allocations and return the one maximizing P(top 25).

    The search is random-restart over (which names, how many, what weights),
    every candidate scored against one shared field draw. Common random numbers
    mean two candidates differing by half a percent of probability are compared
    fairly rather than on simulation noise.

    Set ``allow_margin=False`` to cap gross exposure at 100% -- useful for
    seeing exactly what leverage is buying you before you decide to use it.
    """
    rng = np.random.default_rng(seed)
    fs = field_sample or sample_field(days, field, n_sims=n_sims, seed=seed)
    max_gross = MAX_GROSS if allow_margin else 1.0

    # Required minimum weights for the two funded classes.
    min_w = MIN_NET_COST_PER_ASSET_CLASS / equity
    if min_w > MAX_POSITION_PCT:
        return Allocation(
            {}, {}, {}, 0, 0, 0, 0.0, equity, days, fs.median_threshold, 0,
            feasible=False,
            notes=[
                f"Equity of ${equity:,.0f} is too small: the required "
                f"${MIN_NET_COST_PER_ASSET_CLASS:,.0f} per asset class exceeds "
                f"the {MAX_POSITION_PCT:.0%} single-security cap. Satisfy each "
                f"class with two or more securities.",
            ],
        )

    stocks = [s for s in factors if _class_of(s, classes) is AssetClass.STOCK]
    funds = [s for s in factors if _class_of(s, classes) is AssetClass.MUTUAL_FUND]

    notes: list[str] = []
    if not stocks:
        notes.append("No stock candidates supplied -- the stock class cannot be filled.")
    if not funds:
        notes.append(
            "No mutual fund candidates supplied. The $10,000 mutual fund "
            "minimum is mandatory; add fund tickers (5 letters ending in X)."
        )

    # Rank stocks by idiosyncratic volatility -- the quantity that actually
    # moves you relative to the field.
    stocks.sort(key=lambda s: -factors[s].idio_vol)
    funds.sort(key=lambda s: -factors[s].idio_vol)
    pool = stocks[: max(max_names * 3, 12)]

    best: tuple[float, dict[str, float]] | None = None

    for _ in range(n_candidates):
        if not pool or not funds:
            break
        k = int(rng.integers(min_names, max_names + 1))
        k = min(k, len(pool))
        if k < 1:
            continue
        picks = list(rng.choice(pool, size=k, replace=False))

        w: dict[str, float] = {}

        # 1. Bond: exactly the minimum. It is required drag, never an investment.
        w[bond_symbol] = min_w

        # 2. Mutual fund: at least the minimum. The optimizer may size it up if
        #    the fund carries real volatility, since that capital is forced
        #    anyway -- a required holding might as well be a working one.
        fund = str(rng.choice(funds))
        fund_w = min_w + float(rng.random()) * max(0.0, MAX_POSITION_PCT - min_w) * 0.6
        w[fund] = min(fund_w, MAX_POSITION_PCT)

        # 3. Stocks: split the remaining gross budget.
        used = sum(abs(v) for v in w.values())
        target_gross = float(rng.uniform(max(used + 0.05, 0.6), max_gross))
        budget = target_gross - used
        if budget <= 0:
            continue

        raw = rng.random(k) + 0.15
        raw = raw / raw.sum() * budget
        # Respect the per-name cap, redistributing whatever it clips.
        for _pass in range(4):
            over = raw > MAX_POSITION_PCT
            if not over.any():
                break
            spill = float((raw[over] - MAX_POSITION_PCT).sum())
            raw[over] = MAX_POSITION_PCT
            room = MAX_POSITION_PCT - raw
            room[room < 0] = 0.0
            if room.sum() <= 1e-9:
                break
            raw = raw + room / room.sum() * spill
        if (raw > MAX_POSITION_PCT + 1e-9).any():
            continue

        for sym, wt in zip(picks, raw):
            w[sym] = w.get(sym, 0.0) + float(wt)

        if sum(abs(v) for v in w.values()) > max_gross + 1e-6:
            continue
        if any(abs(v) > MAX_POSITION_PCT + 1e-9 for v in w.values()):
            continue

        fac = dict(factors)
        fac.setdefault(bond_symbol, BOND_PROXY)
        beta, idio = portfolio_factors(w, fac)
        gross = sum(abs(v) for v in w.values())

        # Weights are already expressed as fractions of equity, so gross
        # exposure IS the leverage -- passing leverage=1.0 avoids double-counting.
        p = p_qualify(
            fs, my_beta=beta, my_idio_vol=idio, my_leverage=1.0,
            current_return=current_return,
        )
        if best is None or p > best[0]:
            best = (p, dict(w))

    if best is None:
        return Allocation(
            {}, {}, {}, 0, 0, 0, 0.0, equity, days, fs.median_threshold, 0,
            feasible=False,
            notes=notes + ["No feasible allocation found from the supplied candidates."],
        )

    p, w = best
    fac = dict(factors)
    fac.setdefault(bond_symbol, BOND_PROXY)
    beta, idio = portfolio_factors(w, fac)
    gross = sum(abs(v) for v in w.values())
    out_classes = {s: _class_of(s, classes) for s in w}
    out_classes[bond_symbol] = AssetClass.BOND

    notes.append(
        f"Bond held at exactly ${w[bond_symbol] * equity:,.0f} -- the rule "
        f"minimum. Every extra bond dollar is a direct drag on Percent Return."
    )
    if gross > 1.01:
        notes.append(
            f"Uses ${(gross - 1.0) * equity:,.0f} of margin "
            f"({(gross - 1.0) / MARGIN_BORROW_RATE:.0%} of your borrowing limit). "
            f"Losses are magnified identically -- and final rankings are struck "
            f"net of borrowed funds."
        )
    n_pos = len(w)
    notes.append(
        f"{n_pos} positions. At {MAX_GROSS:.0%} gross and a {MAX_POSITION_PCT:.0%} "
        f"per-name cap, the rules make fewer than "
        f"{math.ceil(MAX_GROSS / MAX_POSITION_PCT)} positions impossible."
    )
    notes.append(
        f"Estimated trading cost to build: ${n_pos * TRANSACTION_FEE:,.0f} "
        f"({n_pos} trades x ${TRANSACTION_FEE:.0f})."
    )

    return Allocation(
        weights={k: round(v, 4) for k, v in w.items()},
        dollars={k: round(v * equity, 2) for k, v in w.items()},
        classes=out_classes,
        beta=round(beta, 3),
        idio_vol=round(idio, 3),
        gross=round(gross, 4),
        p_qualify=round(p, 4),
        equity=equity,
        days=days,
        median_threshold=round(fs.median_threshold, 4),
        n_positions=n_pos,
        feasible=True,
        notes=notes,
    )


def compare_leverage(
    factors: dict[str, FactorParams],
    classes: dict[str, AssetClass],
    days: int,
    equity: float = STARTING_CASH,
    current_return: float = 0.0,
    field: FieldModel | None = None,
    n_sims: int = 12_000,
) -> str:
    """Show what margin actually buys, rather than assuming it is free."""
    fs = sample_field(days, field, n_sims=n_sims)
    out = ["Margin, priced honestly:", ""]
    out.append(f"  {'setting':<22} {'gross':>8} {'idio':>8} {'P(top 25)':>12}")
    for label, allow in (("unlevered (100%)", False), ("full margin (150%)", True)):
        a = optimize(
            factors, classes, days, equity, current_return, field,
            allow_margin=allow, n_sims=n_sims, field_sample=fs,
        )
        if a.feasible:
            out.append(
                f"  {label:<22} {a.gross:>8.0%} {a.idio_vol:>8.0%} "
                f"{a.p_qualify:>12.1%}"
            )
    out += [
        "",
        "Margin multiplies both tails. It is worth using here only because the",
        "payoff is a step function -- finishing 400th and 900th are the same",
        "outcome, so the downside it adds costs you nothing that you had.",
    ]
    return "\n".join(out)
