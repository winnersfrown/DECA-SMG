"""Turn a target allocation into an executable, rule-checked order list.

Three details of this game that a naive plan gets wrong, and this one does not:

1. **Sells must precede buys.** Cash settles instantly in SMG but buying power
   is finite; ordering the list wrong produces rejected orders.

2. **Whole-share rounding can silently break diversification.** Buying "exactly
   $10,000" of a $173 stock actually fills at $9,974 -- which is *below* the
   $10,000 minimum and, after the deadline, is a disqualifying shortfall. Every
   class-minimum order is therefore sized with a deliberate buffer and rounded
   UP, never down.

3. **The $5 fee is flat.** It costs 0.03% on a $15,000 order and 1.0% on a $500
   one, so the planner merges rather than fragments, and reports the total drag
   in basis points of Percent Return before you commit.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field as _field

from .compliance import Severity, audit, validate_order
from .portfolio import Portfolio, Transaction
from .rules import (
    AssetClass,
    MARGIN_BORROW_RATE,
    MAX_POSITION_PCT,
    MIN_NET_COST_PER_ASSET_CLASS,
    TRANSACTION_FEE,
    pricing_day,
)

SAFETY_MARGIN = 0.995
"""Aim marginally inside every cap.

Fill prices move between quoting a plan and the 4 p.m. close, and equity moves
with them. Targeting 99.5% of the limit absorbs that drift; targeting 100% of it
means a half-percent adverse move puts you in violation.
"""

DIVERSIFICATION_BUFFER = 1.03
"""Target 3% above the $10,000 minimum on class-filling buys.

Protects against whole-share rounding *and* against the platform recording a
slightly different fill price than your quote. Costs a few hundred dollars of
flexibility; prevents a disqualification.
"""


@dataclass
class Order:
    """One planned trade, ready to type into the SMG trade screen."""

    symbol: str
    side: str
    quantity: float
    price: float
    asset_class: AssetClass
    reason: str = ""
    priority: int = 50

    @property
    def notional(self) -> float:
        return round(self.quantity * self.price, 2)

    @property
    def cash_impact(self) -> float:
        """Signed effect on cash, fee included."""
        n = self.notional
        if self.side in ("buy", "cover"):
            return round(-(n + TRANSACTION_FEE), 2)
        return round(n - TRANSACTION_FEE, 2)

    def __str__(self) -> str:
        return (
            f"{self.side.upper():<6} {self.quantity:>10,.4g} {self.symbol:<8} "
            f"@ ~${self.price:>10,.2f}  = ${self.notional:>12,.2f}"
        )


@dataclass
class TradePlan:
    """An ordered set of trades plus the compliance outcome of executing them."""

    orders: list[Order] = _field(default_factory=list)
    warnings: list[str] = _field(default_factory=list)
    fees: float = 0.0
    prices_as_of: str = ""
    fills_on: _dt.date | None = None
    projected: Portfolio | None = None
    post_trade_ok: bool = True
    post_trade_findings: list[str] = _field(default_factory=list)

    def render(self) -> str:
        if not self.orders:
            return "No trades required -- the portfolio already matches the target."
        lines = [
            "=" * 78,
            "  TRADE PLAN",
            "=" * 78,
        ]
        if self.fills_on:
            lines.append(
                f"  Orders entered now fill at the {self.fills_on:%a %b %d} closing "
                f"price. Intraday timing is irrelevant in this game."
            )
        if self.prices_as_of:
            lines.append(f"  Quotes: {self.prices_as_of}")
        lines.append("-" * 78)
        lines.append("  Execute IN THIS ORDER (sells first, to free buying power):")
        lines.append("")
        for i, o in enumerate(sorted(self.orders, key=lambda x: x.priority), 1):
            lines.append(f"  {i:>2}. {o}")
            if o.reason:
                lines.append(f"      {o.reason}")
        lines.append("-" * 78)
        drag_bps = self.fees / 100_000.0 * 10_000
        lines.append(
            f"  {len(self.orders)} trades | ${self.fees:,.2f} in fees "
            f"({drag_bps:.1f} bps of Percent Return)"
        )
        if self.projected is not None:
            p = self.projected
            lines.append(
                f"  Projected after fills: equity ${p.total_equity:,.2f} | "
                f"leverage {p.leverage:.2f}x | cash ${p.cash:,.2f}"
            )
            nc = p.net_cost_by_class()
            lines.append(
                "  Net cost by class: "
                + " | ".join(
                    f"{k.value.replace('_', ' ')} ${v:,.0f}" for k, v in nc.items()
                )
            )
        for w in self.warnings:
            lines.append(f"  [!] {w}")
        if self.post_trade_findings:
            lines.append("-" * 78)
            lines.append("  Post-trade compliance:")
            for f in self.post_trade_findings:
                lines.append(f"    {f}")
        lines.append("=" * 78)
        if not self.post_trade_ok:
            lines.append(
                "  WARNING: this plan does NOT leave you compliant. Fix before trading."
            )
        return "\n".join(lines)


def _round_shares(dollars: float, price: float, asset_class: AssetClass,
                  round_up: bool = False) -> float:
    """Shares for a dollar target.

    Mutual funds and bonds transact in dollars, so fractional units are fine.
    Stocks and ETFs need whole shares.
    """
    if price <= 0:
        return 0.0
    if asset_class in (AssetClass.MUTUAL_FUND, AssetClass.BOND):
        return round(dollars / price, 4)
    raw = dollars / price
    return float(math.ceil(raw)) if round_up else float(int(raw))


def plan_to_target(
    pf: Portfolio,
    target_weights: dict[str, float],
    classes: dict[str, AssetClass],
    prices: dict[str, float],
    now: _dt.datetime | None = None,
    tolerance: float = 0.015,
) -> TradePlan:
    """Build the order list that moves ``pf`` to ``target_weights``.

    ``target_weights`` are fractions of Total Equity. Positions not mentioned
    are liquidated. Differences smaller than ``tolerance`` are ignored -- a 1%
    drift is not worth a $5 fee plus a day of exposure.
    """
    now = now or _dt.datetime.now()
    plan = TradePlan(fills_on=pricing_day(now))

    px = {k.upper(): v for k, v in prices.items()}

    # Value the book at the SAME prices the plan will trade at. Sizing against
    # equity computed from stale marks and then filling at fresh quotes makes
    # every weight wrong by however far the two disagree -- which is how a plan
    # aimed at the 30% cap quietly lands above it.
    pf = Portfolio.from_dict(pf.to_dict())
    pf.mark(px)

    equity = pf.total_equity
    if equity <= 0:
        plan.warnings.append("Total Equity is zero or negative; cannot plan trades.")
        plan.post_trade_ok = False
        return plan

    current_dollars: dict[str, float] = {}
    for h in pf.holdings:
        current_dollars[h.symbol] = current_dollars.get(h.symbol, 0.0) + h.market_value

    # Size against PROJECTED post-trade equity, not current equity. Every $5 fee
    # comes straight out of equity, so weights sized against today's balance
    # land a fraction high once the fees clear -- which is exactly how a plan
    # aimed at the 30% cap ends up at 31% and in violation. Estimating the fee
    # drag first, and clamping to the caps afterwards, keeps the plan legal.
    est_orders = len(set(target_weights) | set(current_dollars))
    equity_after = max(1.0, equity - est_orders * TRANSACTION_FEE)

    weights = {s.upper(): float(w) for s, w in target_weights.items()}
    weights = {s: max(-MAX_POSITION_PCT, min(MAX_POSITION_PCT, w))
               for s, w in weights.items()}
    gross = sum(abs(w) for w in weights.values())
    max_gross = 1.0 + MARGIN_BORROW_RATE
    if gross > max_gross:
        # Scale the whole book down rather than dropping a position, so the
        # allocation's shape survives.
        weights = {s: w * max_gross / gross for s, w in weights.items()}
        plan.warnings.append(
            f"Target gross exposure {gross:.0%} exceeds the {max_gross:.0%} margin "
            f"limit; scaled every position down proportionally."
        )

    targets = {s: w * equity_after * SAFETY_MARGIN for s, w in weights.items()}
    universe = set(current_dollars) | set(targets)

    # Which asset classes land close enough to the $10,000 minimum that
    # whole-share rounding could push them under? Only those get the buffer --
    # judged per CLASS, since a class funded well above the minimum needs no
    # protection even when one individual order happens to be near $10,000.
    class_target: dict[AssetClass, float] = {}
    for s, dollars in targets.items():
        ac = classes.get(s) or next(
            (h.asset_class for h in pf.holdings if h.symbol == s), AssetClass.STOCK
        )
        if dollars > 0:
            class_target[ac] = class_target.get(ac, 0.0) + dollars
    tight_classes = {
        ac for ac, total in class_target.items()
        if total < MIN_NET_COST_PER_ASSET_CLASS * 1.25
    }

    for sym in sorted(universe):
        want = targets.get(sym, 0.0)
        have = current_dollars.get(sym, 0.0)
        delta = want - have
        if abs(delta) < tolerance * equity:
            continue

        price = px.get(sym) or next(
            (h.price for h in pf.holdings if h.symbol == sym), 0.0
        )
        if price <= 0:
            plan.warnings.append(f"No price for {sym}; skipped.")
            continue

        ac = classes.get(sym) or next(
            (h.asset_class for h in pf.holdings if h.symbol == sym), AssetClass.STOCK
        )

        # Class-minimum buys get a buffer and round UP, so whole-share rounding
        # can never leave the class below $10,000.
        is_class_fill = delta > 0 and want > 0 and ac in tight_classes
        amount = delta * DIVERSIFICATION_BUFFER if is_class_fill else abs(delta)

        if delta > 0:
            qty = _round_shares(amount, price, ac, round_up=is_class_fill)
            if qty <= 0:
                continue
            reason = ""
            if is_class_fill:
                reason = (
                    f"Sized {DIVERSIFICATION_BUFFER - 1:.0%} above the "
                    f"${MIN_NET_COST_PER_ASSET_CLASS:,.0f} minimum and rounded up, "
                    f"so share rounding cannot leave the "
                    f"{ac.value.replace('_', ' ')} class short."
                )
            plan.orders.append(
                Order(sym, "buy", qty, price, ac, reason, priority=80)
            )
        else:
            held = pf.get(sym, is_short=False)
            if held is None:
                continue
            qty = min(held.quantity, _round_shares(amount, price, ac))
            if qty <= 0:
                continue
            plan.orders.append(
                Order(
                    sym, "sell", qty, price, ac,
                    "Liquidated -- not in the target allocation."
                    if want == 0 else "Trimmed to target weight.",
                    priority=10,
                )
            )

    plan.fees = round(len(plan.orders) * TRANSACTION_FEE, 2)

    # Dry-run the plan and audit the result, so a bad plan never reaches the
    # trade screen.
    proj = Portfolio.from_dict(pf.to_dict())
    fill_date = plan.fills_on or now.date()
    for o in sorted(plan.orders, key=lambda x: x.priority):
        try:
            proj.apply(
                Transaction(fill_date, o.symbol, o.side, o.quantity, o.price, o.asset_class)
            )
        except ValueError as exc:
            plan.warnings.append(f"{o.symbol}: {exc}")
            plan.post_trade_ok = False
    plan.projected = proj

    report = audit(proj, now=now)
    plan.post_trade_findings = [str(f) for f in report.sorted_findings()
                                if f.severity in (Severity.VIOLATION, Severity.WARNING)]
    if not report.is_compliant:
        plan.post_trade_ok = False

    return plan


def plan_diversification_fix(
    pf: Portfolio,
    prices: dict[str, float],
    stock_symbol: str | None = None,
    fund_symbol: str | None = None,
    bond_symbol: str = "BOND",
    bond_price: float = 100.0,
    now: _dt.datetime | None = None,
) -> TradePlan:
    """Emit the minimum trades that satisfy every $10,000 class requirement.

    Run this the moment your strategy is set, and again the day before the
    diversification deadline. It is the cheapest insurance in the competition:
    three orders, $15 in fees, and the single most common disqualification
    becomes impossible.
    """
    now = now or _dt.datetime.now()
    plan = TradePlan(fills_on=pricing_day(now))
    by_class = pf.net_cost_by_class()
    px = {k.upper(): v for k, v in prices.items()}

    wanted = {
        AssetClass.STOCK: (stock_symbol or "").upper(),
        AssetClass.MUTUAL_FUND: (fund_symbol or "").upper(),
        AssetClass.BOND: bond_symbol.upper(),
    }

    for ac, sym in wanted.items():
        have = by_class.get(ac, 0.0)
        if have >= MIN_NET_COST_PER_ASSET_CLASS:
            continue
        if not sym:
            plan.warnings.append(
                f"{ac.value.replace('_', ' ')} is ${MIN_NET_COST_PER_ASSET_CLASS - have:,.0f} "
                f"short but no symbol was supplied for it."
            )
            plan.post_trade_ok = False
            continue

        price = px.get(sym, bond_price if ac is AssetClass.BOND else 0.0)
        if price <= 0:
            plan.warnings.append(f"No price for {sym}; cannot size the {ac.value} fill.")
            plan.post_trade_ok = False
            continue

        need = (MIN_NET_COST_PER_ASSET_CLASS - have) * DIVERSIFICATION_BUFFER
        qty = _round_shares(need, price, ac, round_up=True)
        if qty <= 0:
            continue
        plan.orders.append(
            Order(
                sym, "buy", qty, price, ac,
                f"Fills the {ac.value.replace('_', ' ')} minimum: "
                f"${qty * price:,.0f} net cost vs the "
                f"${MIN_NET_COST_PER_ASSET_CLASS:,.0f} requirement.",
                priority=90,
            )
        )

    plan.fees = round(len(plan.orders) * TRANSACTION_FEE, 2)

    proj = Portfolio.from_dict(pf.to_dict())
    for o in plan.orders:
        try:
            proj.apply(
                Transaction(plan.fills_on or now.date(), o.symbol, o.side,
                            o.quantity, o.price, o.asset_class)
            )
        except ValueError as exc:
            plan.warnings.append(f"{o.symbol}: {exc}")
            plan.post_trade_ok = False
    plan.projected = proj

    report = audit(proj, now=now)
    plan.post_trade_findings = [
        str(f) for f in report.sorted_findings() if f.severity is Severity.VIOLATION
    ]
    if not report.is_compliant:
        plan.post_trade_ok = False
    return plan
