"""Portfolio state, valuation and the Percent Return that decides rankings.

Pure stdlib. The valuation here mirrors how SMG scores a team:

    Total Equity   = cash + long market value - short liability
    Percent Return = (Total Equity - 100,000) / 100,000

Because a margin loan shows up as negative cash, Total Equity is already *net
of borrowed funds* -- which is precisely how the guidelines say final rankings
are struck, "regardless of whether portfolios are liquidated at the end."
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .rules import (
    AssetClass,
    MARGIN_BORROW_RATE,
    MAX_POSITION_PCT,
    STARTING_CASH,
    TRANSACTION_FEE,
)


@dataclass
class Holding:
    """One position in the portfolio.

    ``net_cost`` is tracked explicitly because the diversification rule is
    measured in *net cost* (purchase price excluding the $5 fee), not in
    current market value. Those diverge the moment a position moves.
    """

    symbol: str
    asset_class: AssetClass
    quantity: float
    avg_cost: float
    price: float = 0.0
    is_short: bool = False
    name: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.asset_class = AssetClass(self.asset_class)
        if self.price <= 0:
            self.price = self.avg_cost

    @property
    def net_cost(self) -> float:
        """Cost basis excluding transaction fees -- the diversification metric."""
        return round(self.quantity * self.avg_cost, 2)

    @property
    def market_value(self) -> float:
        """Signed market value: negative for a short, which is a liability."""
        mv = self.quantity * self.price
        return round(-mv if self.is_short else mv, 2)

    @property
    def exposure(self) -> float:
        """Absolute size of the position, used for the concentration cap."""
        return round(abs(self.quantity * self.price), 2)

    @property
    def unrealized(self) -> float:
        """Profit/loss in dollars, sign-correct for shorts."""
        if self.is_short:
            return round(self.quantity * (self.avg_cost - self.price), 2)
        return round(self.quantity * (self.price - self.avg_cost), 2)

    @property
    def return_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        move = (self.price - self.avg_cost) / self.avg_cost
        return round(-move if self.is_short else move, 6)

    def counts_toward_diversification(self) -> bool:
        """Whether this holding satisfies its class's $10,000 minimum.

        Only LONG stock positions count toward the stock requirement; a short
        contributes nothing regardless of size.
        """
        return not self.is_short

    def to_dict(self) -> dict:
        d = asdict(self)
        d["asset_class"] = self.asset_class.value
        return d


@dataclass
class Transaction:
    """A single executed order, for the transaction log and the pitch deck."""

    date: _dt.date
    symbol: str
    side: str
    quantity: float
    price: float
    asset_class: AssetClass
    fee: float = TRANSACTION_FEE
    note: str = ""

    @property
    def gross(self) -> float:
        return round(self.quantity * self.price, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["asset_class"] = self.asset_class.value
        return d


@dataclass
class Portfolio:
    """A team's SMG account.

    ``cash`` may go negative -- that negative balance *is* the margin loan.
    """

    team: str = "My Team"
    region: str = ""
    cash: float = STARTING_CASH
    holdings: list[Holding] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    as_of: _dt.date = field(default_factory=_dt.date.today)

    # ---------------- valuation ----------------

    @property
    def long_market_value(self) -> float:
        return round(sum(h.market_value for h in self.holdings if not h.is_short), 2)

    @property
    def short_liability(self) -> float:
        """Positive number: what it would cost to cover every short."""
        return round(sum(h.exposure for h in self.holdings if h.is_short), 2)

    @property
    def total_equity(self) -> float:
        """The number rankings are computed from, net of borrowed funds."""
        return round(self.cash + self.long_market_value - self.short_liability, 2)

    @property
    def margin_loan(self) -> float:
        """Borrowed funds outstanding. Zero unless cash has gone negative."""
        return round(max(0.0, -self.cash), 2)

    @property
    def margin_capacity(self) -> float:
        """Additional borrowing allowed: 50% of Total Equity, less what is used."""
        return round(max(0.0, MARGIN_BORROW_RATE * self.total_equity - self.margin_loan), 2)

    @property
    def buying_power(self) -> float:
        """Cash on hand plus remaining margin capacity."""
        return round(max(0.0, self.cash) + self.margin_capacity, 2)

    @property
    def gross_exposure(self) -> float:
        """Total capital at work, long plus short."""
        return round(sum(h.exposure for h in self.holdings), 2)

    @property
    def leverage(self) -> float:
        """Gross exposure divided by equity. 1.0 = unlevered, 1.5 = fully levered."""
        eq = self.total_equity
        return round(self.gross_exposure / eq, 4) if eq > 0 else 0.0

    @property
    def percent_return(self) -> float:
        """Percent Return against the $100,000 start. The ranking metric."""
        return round((self.total_equity - STARTING_CASH) / STARTING_CASH, 6)

    # ---------------- diversification ----------------

    def net_cost_by_class(self) -> dict[AssetClass, float]:
        """Net cost per asset class, counting only diversification-eligible lots."""
        totals = {ac: 0.0 for ac in AssetClass}
        for h in self.holdings:
            if h.counts_toward_diversification():
                totals[h.asset_class] += h.net_cost
        return {k: round(v, 2) for k, v in totals.items()}

    def exposure_by_class(self) -> dict[AssetClass, float]:
        totals = {ac: 0.0 for ac in AssetClass}
        for h in self.holdings:
            totals[h.asset_class] += h.exposure
        return {k: round(v, 2) for k, v in totals.items()}

    # ---------------- position limits ----------------

    def position_cap_dollars(self) -> float:
        """Maximum dollars allowed in any one security (30% of Total Equity)."""
        return round(MAX_POSITION_PCT * self.total_equity, 2)

    def concentration(self) -> dict[str, float]:
        """Each symbol's exposure as a fraction of Total Equity."""
        eq = self.total_equity
        if eq <= 0:
            return {h.symbol: 0.0 for h in self.holdings}
        agg: dict[str, float] = {}
        for h in self.holdings:
            agg[h.symbol] = agg.get(h.symbol, 0.0) + h.exposure
        return {sym: round(v / eq, 6) for sym, v in agg.items()}

    def headroom(self, symbol: str) -> float:
        """Additional dollars that may still be bought of ``symbol``.

        Returns 0.0 when the position is already at or through the cap -- the
        shares are kept, but no more may be purchased.
        """
        symbol = symbol.upper().strip()
        held = sum(h.exposure for h in self.holdings if h.symbol == symbol)
        return round(max(0.0, self.position_cap_dollars() - held), 2)

    # ---------------- mutation ----------------

    def get(self, symbol: str, is_short: bool = False) -> Holding | None:
        symbol = symbol.upper().strip()
        for h in self.holdings:
            if h.symbol == symbol and h.is_short == is_short:
                return h
        return None

    def apply(self, txn: Transaction) -> None:
        """Apply an executed transaction, updating cash and holdings.

        Handles the four permitted order types: buy, sell, short, cover.
        """
        sym = txn.symbol.upper().strip()
        qty, px = txn.quantity, txn.price
        side = txn.side.lower()

        if side == "buy":
            h = self.get(sym, is_short=False)
            if h is None:
                self.holdings.append(
                    Holding(sym, txn.asset_class, qty, px, px, is_short=False)
                )
            else:
                total = h.quantity + qty
                h.avg_cost = (h.quantity * h.avg_cost + qty * px) / total
                h.quantity = total
                h.price = px
            self.cash -= qty * px + txn.fee

        elif side == "sell":
            h = self.get(sym, is_short=False)
            if h is None:
                raise ValueError(f"cannot sell {sym}: no long position held")
            if qty > h.quantity + 1e-9:
                raise ValueError(f"cannot sell {qty} of {sym}: only {h.quantity} held")
            h.quantity -= qty
            h.price = px
            if h.quantity <= 1e-9:
                self.holdings.remove(h)
            self.cash += qty * px - txn.fee

        elif side == "short":
            h = self.get(sym, is_short=True)
            if h is None:
                self.holdings.append(
                    Holding(sym, txn.asset_class, qty, px, px, is_short=True)
                )
            else:
                total = h.quantity + qty
                h.avg_cost = (h.quantity * h.avg_cost + qty * px) / total
                h.quantity = total
                h.price = px
            self.cash += qty * px - txn.fee

        elif side == "cover":
            h = self.get(sym, is_short=True)
            if h is None:
                raise ValueError(f"cannot cover {sym}: no short position held")
            if qty > h.quantity + 1e-9:
                raise ValueError(f"cannot cover {qty} of {sym}: only {h.quantity} short")
            h.quantity -= qty
            h.price = px
            if h.quantity <= 1e-9:
                self.holdings.remove(h)
            self.cash -= qty * px + txn.fee

        else:
            raise ValueError(f"unknown order side: {txn.side!r}")

        self.cash = round(self.cash, 2)
        self.transactions.append(txn)

    def mark(self, prices: dict[str, float]) -> None:
        """Mark holdings to current prices. Unknown symbols keep their last price."""
        for h in self.holdings:
            px = prices.get(h.symbol)
            if px is not None and px > 0:
                h.price = float(px)

    # ---------------- persistence ----------------

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "region": self.region,
            "cash": round(self.cash, 2),
            "as_of": self.as_of.isoformat(),
            "holdings": [h.to_dict() for h in self.holdings],
            "transactions": [t.to_dict() for t in self.transactions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        holdings = [
            Holding(
                symbol=h["symbol"],
                asset_class=AssetClass(h["asset_class"]),
                quantity=float(h["quantity"]),
                avg_cost=float(h["avg_cost"]),
                price=float(h.get("price") or h["avg_cost"]),
                is_short=bool(h.get("is_short", False)),
                name=h.get("name", ""),
            )
            for h in d.get("holdings", [])
        ]
        txns = [
            Transaction(
                date=_dt.date.fromisoformat(t["date"]),
                symbol=t["symbol"],
                side=t["side"],
                quantity=float(t["quantity"]),
                price=float(t["price"]),
                asset_class=AssetClass(t["asset_class"]),
                fee=float(t.get("fee", TRANSACTION_FEE)),
                note=t.get("note", ""),
            )
            for t in d.get("transactions", [])
        ]
        as_of = d.get("as_of")
        return cls(
            team=d.get("team", "My Team"),
            region=d.get("region", ""),
            cash=float(d.get("cash", STARTING_CASH)),
            holdings=holdings,
            transactions=txns,
            as_of=_dt.date.fromisoformat(as_of) if as_of else _dt.date.today(),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Portfolio":
        return cls.from_dict(json.loads(Path(path).read_text()))
