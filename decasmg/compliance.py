"""Rule compliance engine -- the disqualification firewall.

Placing badly is survivable. Being disqualified is not, and the guidelines are
explicit that "final determination of disqualification rests solely with the
SIFMA Foundation." Every rule that can end a run is checked here, and every
finding carries the concrete trade that fixes it.

Pure stdlib so this always runs, even where numpy is unavailable.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum

from .portfolio import Portfolio
from .rules import (
    AssetClass,
    COMPETITION_END,
    COMPETITION_START,
    DEADLINES,
    DIVERSIFICATION_DEADLINE,
    DIVERSIFICATION_RESTORE_BUSINESS_DAYS,
    MARGIN_BORROW_RATE,
    MAX_POSITION_PCT,
    MAX_TEAM_SIZE,
    MIN_NET_COST_PER_ASSET_CLASS,
    MIN_TEAM_SIZE,
    PROHIBITED_INSTRUMENTS,
    STUDENT_NAME_DEADLINE,
    TRANSACTION_FEE,
    add_business_days,
    trading_days_remaining,
)


class Severity(str, Enum):
    """How badly a finding hurts.

    VIOLATION -- a rule is being broken right now; fix today.
    WARNING   -- not yet broken, but on track to break, or a deadline nears.
    INFO      -- worth knowing; no action strictly required.
    OK        -- the check passed.
    """

    VIOLATION = "VIOLATION"
    WARNING = "WARNING"
    INFO = "INFO"
    OK = "OK"


_ORDER = {Severity.VIOLATION: 0, Severity.WARNING: 1, Severity.INFO: 2, Severity.OK: 3}


@dataclass
class Finding:
    """One rule check outcome."""

    rule: str
    severity: Severity
    message: str
    remedy: str = ""

    def __str__(self) -> str:
        icon = {
            Severity.VIOLATION: "[X]",
            Severity.WARNING: "[!]",
            Severity.INFO: "[i]",
            Severity.OK: "[OK]",
        }[self.severity]
        out = f"{icon} {self.rule}: {self.message}"
        if self.remedy:
            out += f"\n      -> {self.remedy}"
        return out


@dataclass
class ComplianceReport:
    """The full set of findings for a portfolio at a moment in time."""

    findings: list[Finding] = field(default_factory=list)
    checked_at: _dt.datetime = field(default_factory=_dt.datetime.now)

    def add(self, *findings: Finding) -> None:
        self.findings.extend(findings)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.VIOLATION]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def is_compliant(self) -> bool:
        return not self.violations

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: _ORDER[f.severity])

    def render(self, show_ok: bool = True) -> str:
        lines = [
            "=" * 72,
            f"  DECA SMG COMPLIANCE REPORT  -  {self.checked_at:%Y-%m-%d %H:%M}",
            "=" * 72,
        ]
        shown = [
            f for f in self.sorted_findings()
            if show_ok or f.severity is not Severity.OK
        ]
        for f in shown:
            lines.append(str(f))
        lines.append("-" * 72)
        if self.violations:
            lines.append(
                f"  {len(self.violations)} VIOLATION(S) -- fix before the next "
                f"4 p.m. ET close."
            )
        elif self.warnings:
            lines.append(f"  Compliant. {len(self.warnings)} warning(s) to watch.")
        else:
            lines.append("  Fully compliant.")
        lines.append("=" * 72)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_diversification(
    pf: Portfolio, now: _dt.datetime | None = None
) -> list[Finding]:
    """The $10,000-per-asset-class requirement -- the most common way to lose.

    Before the deadline this is a countdown; after it, any shortfall is a live
    violation with a one-business-day cure period.
    """
    now = now or _dt.datetime.now()
    past_deadline = now >= DIVERSIFICATION_DEADLINE
    by_class = pf.net_cost_by_class()
    out: list[Finding] = []

    for ac in (AssetClass.STOCK, AssetClass.MUTUAL_FUND, AssetClass.BOND):
        have = by_class.get(ac, 0.0)
        label = ac.value.replace("_", " ")
        short_by = round(MIN_NET_COST_PER_ASSET_CLASS - have, 2)

        if have >= MIN_NET_COST_PER_ASSET_CLASS:
            out.append(
                Finding(
                    f"Diversification/{label}",
                    Severity.OK,
                    f"${have:,.2f} net cost (minimum ${MIN_NET_COST_PER_ASSET_CLASS:,.0f}).",
                )
            )
            continue

        # Shortfall. Severity depends on whether the deadline has passed.
        buy_amt = round(MIN_NET_COST_PER_ASSET_CLASS + 5.0, 2)
        if ac is AssetClass.STOCK:
            how = (
                f"Buy ~${buy_amt:,.0f} net cost of a LONG stock or ETF "
                f"(short positions do NOT count)."
            )
        elif ac is AssetClass.MUTUAL_FUND:
            how = (
                f"Buy ~${buy_amt:,.0f} net cost of a mutual fund. A bond mutual "
                f"fund counts here, not as a bond."
            )
        else:
            how = (
                f"Buy ~${buy_amt:,.0f} net cost of an actual BOND. A bond ETF "
                f"counts as a stock and a bond mutual fund counts as a mutual "
                f"fund -- neither satisfies this class."
            )

        if past_deadline:
            cure = add_business_days(now.date(), DIVERSIFICATION_RESTORE_BUSINESS_DAYS)
            out.append(
                Finding(
                    f"Diversification/{label}",
                    Severity.VIOLATION,
                    f"${have:,.2f} net cost -- ${short_by:,.2f} SHORT of the "
                    f"${MIN_NET_COST_PER_ASSET_CLASS:,.0f} minimum, and the "
                    f"deadline passed on {DIVERSIFICATION_DEADLINE:%b %d}.",
                    f"{how} Restore within one business day (by "
                    f"{cure:%a %b %d}) or risk disqualification.",
                )
            )
        else:
            days = (DIVERSIFICATION_DEADLINE - now).days
            sev = Severity.WARNING if days <= 14 else Severity.INFO
            out.append(
                Finding(
                    f"Diversification/{label}",
                    sev,
                    f"${have:,.2f} net cost -- ${short_by:,.2f} short. "
                    f"{days} day(s) until the {DIVERSIFICATION_DEADLINE:%b %d} deadline.",
                    how,
                )
            )
    return out


def check_concentration(pf: Portfolio) -> list[Finding]:
    """No more than 20% x 1.5 = 30% of Total Equity in any one security.

    The cap blocks *purchases*, not ownership: a position that appreciates
    through 30% is retained, it simply cannot be added to.
    """
    out: list[Finding] = []
    conc = pf.concentration()
    if not conc:
        return [Finding("Concentration", Severity.OK, "No positions held.")]

    cap_dollars = pf.position_cap_dollars()
    worst = max(conc.items(), key=lambda kv: kv[1]) if conc else None

    for sym, pct in sorted(conc.items(), key=lambda kv: -kv[1]):
        if pct > MAX_POSITION_PCT + 1e-9:
            # Determine whether it was bought over-cap or drifted over.
            cost = sum(
                h.net_cost for h in pf.holdings if h.symbol == sym
            )
            drifted = cost <= cap_dollars + 1e-6
            if drifted:
                out.append(
                    Finding(
                        f"Concentration/{sym}",
                        Severity.INFO,
                        f"{pct:.1%} of Total Equity -- above the "
                        f"{MAX_POSITION_PCT:.0%} cap because it appreciated.",
                        "Allowed: you keep the shares. You may NOT buy more of "
                        f"{sym} until it falls back under ${cap_dollars:,.0f}.",
                    )
                )
            else:
                out.append(
                    Finding(
                        f"Concentration/{sym}",
                        Severity.VIOLATION,
                        f"{pct:.1%} of Total Equity was PURCHASED above the "
                        f"{MAX_POSITION_PCT:.0%} cap (${cap_dollars:,.0f}).",
                        f"Sell down to at most ${cap_dollars:,.0f} of {sym}.",
                    )
                )
        elif pct > MAX_POSITION_PCT * 0.9:
            out.append(
                Finding(
                    f"Concentration/{sym}",
                    Severity.WARNING,
                    f"{pct:.1%} of Total Equity -- near the "
                    f"{MAX_POSITION_PCT:.0%} cap.",
                    f"Only ${pf.headroom(sym):,.0f} more of {sym} may be bought.",
                )
            )
    if not out and worst:
        out.append(
            Finding(
                "Concentration",
                Severity.OK,
                f"Largest position {worst[0]} at {worst[1]:.1%} of Total Equity "
                f"(cap {MAX_POSITION_PCT:.0%}).",
            )
        )
    return out


def check_margin(pf: Portfolio) -> list[Finding]:
    """Borrowing is capped at 50% of Total Equity."""
    eq = pf.total_equity
    if eq <= 0:
        return [
            Finding(
                "Margin",
                Severity.VIOLATION,
                f"Total Equity is ${eq:,.2f} -- the account is wiped out.",
                "Close leveraged positions immediately.",
            )
        ]
    loan = pf.margin_loan
    limit = round(MARGIN_BORROW_RATE * eq, 2)
    if loan > limit + 1e-6:
        return [
            Finding(
                "Margin",
                Severity.VIOLATION,
                f"Margin loan ${loan:,.2f} exceeds the 50%-of-equity limit of "
                f"${limit:,.2f}.",
                f"Sell at least ${loan - limit:,.2f} of holdings to pay the loan down.",
            )
        ]
    if loan > limit * 0.9:
        return [
            Finding(
                "Margin",
                Severity.WARNING,
                f"Margin loan ${loan:,.2f} is {loan / limit:.0%} of the "
                f"${limit:,.2f} limit. A drawdown shrinks equity and the limit "
                f"with it.",
                "Keep a buffer -- a falling market can force a sale at the worst time.",
            )
        ]
    return [
        Finding(
            "Margin",
            Severity.OK,
            f"Loan ${loan:,.2f} of ${limit:,.2f} allowed. "
            f"${pf.margin_capacity:,.2f} of borrowing capacity unused "
            f"(leverage {pf.leverage:.2f}x).",
        )
    ]


def check_instruments(pf: Portfolio) -> list[Finding]:
    """Futures, options, commodities, currencies and bitcoin are not permitted."""
    bad: list[str] = []
    for h in pf.holdings:
        blob = f"{h.symbol} {h.name}".lower()
        if any(word in blob.split() or word in blob for word in PROHIBITED_INSTRUMENTS):
            bad.append(h.symbol)
    if bad:
        return [
            Finding(
                "Prohibited instruments",
                Severity.VIOLATION,
                f"Possible prohibited holding(s): {', '.join(sorted(set(bad)))}. "
                f"Futures, options, commodities, currencies and bitcoin are banned.",
                "Verify each and liquidate anything that is not a stock, ETF, "
                "mutual fund or bond.",
            )
        ]
    return [
        Finding(
            "Prohibited instruments",
            Severity.OK,
            "No futures, options, commodities, currencies or crypto detected.",
        )
    ]


def check_team(members: list[str] | None, now: _dt.datetime | None = None) -> list[Finding]:
    """Team size and the hard student-name eligibility gate."""
    now = now or _dt.datetime.now()
    out: list[Finding] = []
    if members is not None:
        n = len(members)
        if not (MIN_TEAM_SIZE <= n <= MAX_TEAM_SIZE):
            out.append(
                Finding(
                    "Team size",
                    Severity.VIOLATION,
                    f"{n} member(s); teams must have "
                    f"{MIN_TEAM_SIZE}-{MAX_TEAM_SIZE}.",
                    "Adjust the roster before the name submission deadline.",
                )
            )
        else:
            out.append(
                Finding("Team size", Severity.OK, f"{n} member(s) -- within 1-3.")
            )

    if now < STUDENT_NAME_DEADLINE:
        days = (STUDENT_NAME_DEADLINE - now).days
        sev = Severity.WARNING if days <= 21 else Severity.INFO
        out.append(
            Finding(
                "Student name submission",
                sev,
                f"{days} day(s) until {STUDENT_NAME_DEADLINE:%b %d, %Y} 4 p.m. ET. "
                f"Only students submitted before then are ELIGIBLE TO RANK IN "
                f"THE TOP 25.",
                "Have your advisor confirm every name in the Teacher Support "
                "Center -> View Team Portfolios -> Update Student Names. A "
                "misspelled name is an ineligible name.",
            )
        )
    else:
        out.append(
            Finding(
                "Student name submission",
                Severity.INFO,
                "Deadline passed. The roster is now frozen -- no substitutions "
                "or additions are permitted.",
            )
        )
    return out


def check_activity(pf: Portfolio, now: _dt.datetime | None = None) -> list[Finding]:
    """A team receives no ranking at all until its first transaction lands."""
    now = now or _dt.datetime.now()
    if not pf.transactions and not pf.holdings:
        sev = Severity.VIOLATION if now > COMPETITION_START else Severity.WARNING
        return [
            Finding(
                "Initial transaction",
                sev,
                "No transactions recorded. A team is not ranked and does not "
                "appear in regional rankings until its initial transaction is "
                "entered successfully.",
                "Place your first trade -- an unranked portfolio cannot place "
                "top 25 no matter how it performs.",
            )
        ]
    return [
        Finding(
            "Initial transaction",
            Severity.OK,
            f"{len(pf.transactions)} transaction(s) recorded -- the portfolio is ranked.",
        )
    ]


def check_timeline(now: _dt.datetime | None = None) -> list[Finding]:
    """Countdown across every dated obligation."""
    now = now or _dt.datetime.now()
    out: list[Finding] = []
    remaining = trading_days_remaining(now)
    if now < COMPETITION_START:
        out.append(
            Finding(
                "Timeline",
                Severity.INFO,
                f"Competition opens {COMPETITION_START:%b %d, %Y} 9:30 a.m. ET.",
            )
        )
    elif now >= COMPETITION_END:
        out.append(
            Finding(
                "Timeline",
                Severity.INFO,
                f"Competition closed {COMPETITION_END:%b %d, %Y}. Final Percent "
                f"Return is struck net of borrowed funds.",
            )
        )
    else:
        out.append(
            Finding(
                "Timeline",
                Severity.INFO,
                f"{remaining} trading day(s) remain of the 63-day competition.",
            )
        )
    for d in DEADLINES:
        if d.disqualifying and not d.is_past(now):
            days = d.days_remaining(now)
            if days <= 7:
                out.append(
                    Finding(
                        f"Deadline/{d.name}",
                        Severity.WARNING,
                        f"{days:.1f} day(s) away ({d.when:%a %b %d, %I:%M %p} ET). {d.detail}",
                    )
                )
    return out


def check_fee_drag(pf: Portfolio) -> list[Finding]:
    """Flat $5 per trade is negligible on size and brutal on churn."""
    n = len(pf.transactions)
    if n == 0:
        return []
    paid = round(sum(t.fee for t in pf.transactions), 2)
    drag = paid / 100_000.0
    if drag > 0.005:
        return [
            Finding(
                "Fee drag",
                Severity.WARNING,
                f"{n} trades have cost ${paid:,.2f} in fees -- {drag:.2%} of the "
                f"starting balance, deducted straight from Percent Return.",
                "The fee is flat, so it punishes small frequent trades. Trade in "
                "fewer, larger tickets; a $5 fee on a $15,000 order is 0.03%, on "
                "a $500 order it is 1.0%.",
            )
        ]
    return [
        Finding(
            "Fee drag",
            Severity.OK,
            f"{n} trades, ${paid:,.2f} in fees ({drag:.2%} of starting balance).",
        )
    ]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def audit(
    pf: Portfolio,
    members: list[str] | None = None,
    now: _dt.datetime | None = None,
) -> ComplianceReport:
    """Run every check and return a single report."""
    now = now or _dt.datetime.now()
    report = ComplianceReport(checked_at=now)
    report.add(*check_activity(pf, now))
    report.add(*check_diversification(pf, now))
    report.add(*check_concentration(pf))
    report.add(*check_margin(pf))
    report.add(*check_instruments(pf))
    report.add(*check_team(members, now))
    report.add(*check_fee_drag(pf))
    report.add(*check_timeline(now))
    return report


def validate_order(
    pf: Portfolio,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    asset_class: AssetClass,
) -> list[Finding]:
    """Pre-trade check: would this order break a rule *before* you place it?

    This is the cheap version of compliance -- catching a bad order costs
    nothing, unwinding a filled one costs $5 and a day of slippage.
    """
    out: list[Finding] = []
    side = side.lower()
    notional = quantity * price

    if side in ("buy", "short"):
        cap = pf.position_cap_dollars()
        held = sum(h.exposure for h in pf.holdings if h.symbol == symbol.upper())
        if held + notional > cap + 1e-6:
            allowed = max(0.0, cap - held)
            max_shares = int(allowed // price) if price > 0 else 0
            out.append(
                Finding(
                    "Pre-trade/concentration",
                    Severity.VIOLATION,
                    f"{side} {quantity:,.0f} {symbol} = ${notional:,.2f} would put "
                    f"the position at ${held + notional:,.2f}, past the "
                    f"{MAX_POSITION_PCT:.0%} cap of ${cap:,.2f}.",
                    f"Reduce to at most {max_shares:,} shares (${allowed:,.2f}).",
                )
            )

    if side == "buy":
        cost = notional + TRANSACTION_FEE
        if cost > pf.buying_power + 1e-6:
            out.append(
                Finding(
                    "Pre-trade/buying power",
                    Severity.VIOLATION,
                    f"Order costs ${cost:,.2f} including the ${TRANSACTION_FEE:.0f} "
                    f"fee but buying power is ${pf.buying_power:,.2f}.",
                    f"Reduce size or sell something first.",
                )
            )

    if side == "sell":
        h = pf.get(symbol, is_short=False)
        if h is None or quantity > h.quantity + 1e-9:
            have = h.quantity if h else 0
            out.append(
                Finding(
                    "Pre-trade/position",
                    Severity.VIOLATION,
                    f"Cannot sell {quantity:,.0f} {symbol}: {have:,.0f} held.",
                    "Reduce the order or short instead of selling.",
                )
            )
        else:
            # Would this sale drop a class below its minimum?
            after = pf.net_cost_by_class()[asset_class] - quantity * h.avg_cost
            if after < MIN_NET_COST_PER_ASSET_CLASS:
                out.append(
                    Finding(
                        "Pre-trade/diversification",
                        Severity.WARNING,
                        f"This sale leaves ${after:,.2f} net cost in "
                        f"{asset_class.value.replace('_', ' ')} -- below the "
                        f"${MIN_NET_COST_PER_ASSET_CLASS:,.0f} minimum.",
                        "Permitted only if you restore the class to $10,000 "
                        "within one business day. Plan the replacement buy now.",
                    )
                )

    if side == "cover":
        h = pf.get(symbol, is_short=True)
        if h is None or quantity > h.quantity + 1e-9:
            have = h.quantity if h else 0
            out.append(
                Finding(
                    "Pre-trade/position",
                    Severity.VIOLATION,
                    f"Cannot cover {quantity:,.0f} {symbol}: {have:,.0f} short.",
                    "Reduce the order size.",
                )
            )

    if not out:
        out.append(
            Finding(
                "Pre-trade",
                Severity.OK,
                f"{side} {quantity:,.0f} {symbol} @ ${price:,.2f} "
                f"(${notional:,.2f}) passes every rule check.",
            )
        )
    return out
