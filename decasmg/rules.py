"""Machine-readable encoding of the DECA Stock Market Game ruleset (2026-27).

Every constant here traces to the official HS_SMG_Guidelines. This module is the
single source of truth: no other module hard-codes a limit, fee or date.

Pure stdlib on purpose -- the compliance path must run anywhere, even on a
locked-down school laptop with no scientific Python stack.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import Enum

# --------------------------------------------------------------------------
# Key dates (all times US/Eastern, per the guidelines)
# --------------------------------------------------------------------------

COMPETITION_START = _dt.datetime(2026, 9, 8, 9, 30)
"""Competition begins: Tuesday, September 8, 2026, 9:30 a.m. ET."""

STUDENT_NAME_DEADLINE = _dt.datetime(2026, 10, 16, 16, 0)
"""Student Name Submission: Friday, October 16, 2026, 4 p.m. ET.

Hard eligibility gate: only students submitted before this instant are eligible
to rank in the top 25. No substitutions or additions afterwards.
"""

TEAM_ADDITION_DEADLINE = _dt.datetime(2026, 10, 16, 16, 0)
"""Final team additions must reach decasmg@sifma.org by this instant.

SIFMA asks for two business days of processing, so the practical internal
deadline is earlier -- see ``ADVISOR_PROCESSING_DAYS``.
"""

ADVISOR_PROCESSING_DAYS = 2
"""Business days SIFMA asks for to process an advisor request."""

DIVERSIFICATION_DEADLINE = _dt.datetime(2026, 10, 23, 16, 0)
"""Asset Diversification: Friday, October 23, 2026, 4 p.m. ET.

By this instant every asset class must hold >= $10,000 net cost, and the
holding must be maintained through COMPETITION_END.
"""

COMPETITION_END = _dt.datetime(2026, 12, 4, 16, 0)
"""Competition ends: Friday, December 4, 2026, 4 p.m. ET."""

PORTFOLIO_DELETION = _dt.date(2027, 5, 10)
"""Portfolios retrievable until Monday, May 10, 2027. Export before this."""

MARKET_OPEN = _dt.time(9, 30)
MARKET_CLOSE = _dt.time(16, 0)

MARKET_HOLIDAYS: frozenset[_dt.date] = frozenset(
    {
        _dt.date(2026, 9, 7),    # Labor Day (day before competition opens)
        _dt.date(2026, 11, 26),  # Thanksgiving -- the only full closure in-window
        _dt.date(2026, 12, 25),  # Christmas (after competition ends)
    }
)
"""NYSE full closures relevant to the competition window.

Note: Friday, November 27, 2026 is a 1 p.m. early close, not a closure. Because
SMG prices every trade at the closing price, an early close changes nothing
about how orders fill -- it is deliberately not listed here.
"""

# --------------------------------------------------------------------------
# Account mechanics
# --------------------------------------------------------------------------

STARTING_CASH = 100_000.00
"""Each team begins with $100,000 in cash."""

MARGIN_BORROW_RATE = 0.50
"""Teams may borrow an additional 50% of Total Equity in margin."""

TRANSACTION_FEE = 5.00
"""Flat per-trade commission. Applies to every buy and every sell."""

MAX_POSITION_PCT = round(0.20 * 1.5, 10)
"""Maximum 20% of Total Equity multiplied by 1.5 in any one security = 30%.

Enforcement is asymmetric and this matters strategically: the cap blocks new
*purchases*. If a holding appreciates through the cap the team keeps the shares
and simply cannot add more.
"""

MIN_NET_COST_PER_ASSET_CLASS = 10_000.00
"""Minimum investment per asset class, measured as Net Cost (excludes the fee).

Guidelines: confirm by adding the Net Cost of investments per asset class
(minus the $5 transaction fee) on the Account Holdings page.
"""

DIVERSIFICATION_RESTORE_BUSINESS_DAYS = 1
"""If a class is sold below the minimum after the deadline, restore within one
business day."""

RANKING_BENCHMARK = "S&P 500 Growth"
"""Rankings are determined by Percent Return as compared to S&P 500 Growth."""

BENCHMARK_TICKERS = ("IVW", "^SP500G")
"""Investable/index proxies for the ranking benchmark, in preference order.

IVW (iShares S&P 500 Growth ETF) is the reliable free-data proxy.
"""

ICDC_QUALIFYING_RANK = 25
"""Top 25 teams from each DECA region are submitted for ICDC qualification."""

MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 3

MAX_PITCH_DECK_SLIDES = 20
"""Pitch deck limited to 20 slides, including the appendix."""

MAX_PRESENTATION_MINUTES = 15
"""Oral presentation maximum, including judge's questions."""


# --------------------------------------------------------------------------
# Asset classification
# --------------------------------------------------------------------------


class AssetClass(str, Enum):
    """The three classes that carry a $10,000 diversification minimum."""

    STOCK = "stock"
    MUTUAL_FUND = "mutual_fund"
    BOND = "bond"


DIVERSIFIED_CLASSES: tuple[AssetClass, ...] = (
    AssetClass.STOCK,
    AssetClass.MUTUAL_FUND,
    AssetClass.BOND,
)

CLASSIFICATION_NOTES = {
    AssetClass.STOCK: (
        "All ETFs -- including bond ETFs -- are classified as stocks. "
        "Only LONG stock positions count toward the stock minimum; short "
        "positions do not."
    ),
    AssetClass.MUTUAL_FUND: (
        "All bond mutual funds are classified as mutual funds, not bonds. "
        "This is the cheapest way to satisfy a class minimum with a "
        "higher-volatility instrument."
    ),
    AssetClass.BOND: (
        "Only actual bonds count. A bond ETF counts as a stock and a bond "
        "mutual fund counts as a mutual fund -- neither satisfies this class."
    ),
}


class Side(str, Enum):
    """Order sides permitted by the game."""

    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"


PROHIBITED_INSTRUMENTS: frozenset[str] = frozenset(
    {"future", "futures", "option", "options", "commodity", "commodities",
     "currency", "currencies", "forex", "fx", "bitcoin", "crypto",
     "cryptocurrency"}
)
"""Trading in futures, options, commodities, currencies and bitcoin is not
permitted."""


# --------------------------------------------------------------------------
# Trading calendar helpers
# --------------------------------------------------------------------------


def is_trading_day(day: _dt.date) -> bool:
    """True when the market is open on ``day`` (weekday and not a holiday)."""
    return day.weekday() < 5 and day not in MARKET_HOLIDAYS


def next_trading_day(day: _dt.date) -> _dt.date:
    """The first trading day strictly after ``day``."""
    cursor = day + _dt.timedelta(days=1)
    while not is_trading_day(cursor):
        cursor += _dt.timedelta(days=1)
    return cursor


def add_business_days(day: _dt.date, count: int) -> _dt.date:
    """``day`` advanced by ``count`` trading days."""
    cursor = day
    for _ in range(count):
        cursor = next_trading_day(cursor)
    return cursor


def trading_days_between(start: _dt.date, end: _dt.date) -> list[_dt.date]:
    """Every trading day in the inclusive range ``[start, end]``."""
    days: list[_dt.date] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += _dt.timedelta(days=1)
    return days


def competition_trading_days() -> list[_dt.date]:
    """Every trading day of the competition window."""
    return trading_days_between(COMPETITION_START.date(), COMPETITION_END.date())


def trading_days_remaining(now: _dt.datetime | None = None) -> int:
    """Trading days left in the competition, counting today only if tradeable.

    "Tradeable today" means the market is open and it is before the 4 p.m. ET
    close -- after the close, an order prices at the *next* business day.
    """
    now = now or _dt.datetime.now()
    if now >= COMPETITION_END:
        return 0
    start = now.date()
    if not (is_trading_day(start) and now.time() < MARKET_CLOSE):
        start = next_trading_day(start)
    if start > COMPETITION_END.date():
        return 0
    return len(trading_days_between(start, COMPETITION_END.date()))


def pricing_day(placed_at: _dt.datetime) -> _dt.date:
    """The date whose *closing price* an order placed at ``placed_at`` receives.

    Trades placed during market hours are priced at that business day's closing
    price. Trades entered after hours or on a holiday price at the next
    business day's closing price.

    The practical consequence: intraday timing is worth exactly nothing in this
    game. There is no advantage to watching a ticker all day.
    """
    day = placed_at.date()
    if is_trading_day(day) and MARKET_OPEN <= placed_at.time() < MARKET_CLOSE:
        return day
    if is_trading_day(day) and placed_at.time() < MARKET_OPEN:
        return day
    return next_trading_day(day)


# --------------------------------------------------------------------------
# Deadline reporting
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Deadline:
    """A dated competition obligation."""

    name: str
    when: _dt.datetime
    detail: str
    disqualifying: bool

    def days_remaining(self, now: _dt.datetime | None = None) -> float:
        now = now or _dt.datetime.now()
        return (self.when - now).total_seconds() / 86400.0

    def is_past(self, now: _dt.datetime | None = None) -> bool:
        return (now or _dt.datetime.now()) >= self.when


DEADLINES: tuple[Deadline, ...] = (
    Deadline(
        "Competition begins",
        COMPETITION_START,
        "First day trades may be placed. A team receives no ranking at all "
        "until its initial transaction is entered successfully.",
        disqualifying=False,
    ),
    Deadline(
        "Student name submission",
        STUDENT_NAME_DEADLINE,
        "Only students submitted before this instant are eligible to rank in "
        "the top 25. No substitutions or additions afterwards.",
        disqualifying=True,
    ),
    Deadline(
        "Asset diversification",
        DIVERSIFICATION_DEADLINE,
        "Every asset class (stock / mutual fund / bond) must hold >= $10,000 "
        "net cost, maintained through the close of competition.",
        disqualifying=True,
    ),
    Deadline(
        "Competition ends",
        COMPETITION_END,
        "Final Percent Return is struck, net of any borrowed funds.",
        disqualifying=False,
    ),
    Deadline(
        "Portfolio deletion",
        _dt.datetime.combine(PORTFOLIO_DELETION, _dt.time(0, 0)),
        "Export all portfolio and transaction data before this date -- the "
        "pitch deck depends on it.",
        disqualifying=False,
    ),
)
