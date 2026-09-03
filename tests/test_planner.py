"""Trade plans must be executable and must not create violations."""

import datetime as dt

import pytest

from decasmg.planner import plan_diversification_fix, plan_to_target
from decasmg.portfolio import Portfolio, Transaction
from decasmg.rules import AssetClass, MAX_POSITION_PCT, MIN_NET_COST_PER_ASSET_CLASS

D = dt.date(2026, 9, 8)
NOW = dt.datetime(2026, 10, 20, 10, 0)


def test_class_fills_round_up_and_clear_the_minimum():
    """Whole-share rounding must never leave a class below $10,000."""
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "NVDA", "buy", 300, 100.0, AssetClass.STOCK))
    plan = plan_diversification_fix(
        pf, prices={"FSELX": 173.37}, fund_symbol="FSELX",
        bond_symbol="BOND", bond_price=99.4, now=NOW,
    )
    assert len(plan.orders) == 2
    for o in plan.orders:
        assert o.notional > MIN_NET_COST_PER_ASSET_CLASS
    nc = plan.projected.net_cost_by_class()
    assert nc[AssetClass.MUTUAL_FUND] >= MIN_NET_COST_PER_ASSET_CLASS
    assert nc[AssetClass.BOND] >= MIN_NET_COST_PER_ASSET_CLASS


def test_plan_reports_a_pre_existing_violation_rather_than_claiming_success():
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "NVDA", "buy", 450, 100.0, AssetClass.STOCK))  # 45%, over cap
    plan = plan_diversification_fix(
        pf, prices={"FSELX": 100.0}, fund_symbol="FSELX",
        bond_symbol="BOND", bond_price=100.0, now=NOW,
    )
    assert not plan.post_trade_ok
    assert any("Concentration" in f for f in plan.post_trade_findings)


def test_sells_are_ordered_before_buys():
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "OLD", "buy", 200, 100.0, AssetClass.STOCK))
    plan = plan_to_target(
        pf,
        target_weights={"NEW": 0.20},
        classes={"NEW": AssetClass.STOCK},
        prices={"OLD": 100.0, "NEW": 50.0},
        now=NOW,
    )
    ordered = sorted(plan.orders, key=lambda o: o.priority)
    sides = [o.side for o in ordered]
    assert sides.index("sell") < sides.index("buy")


def test_small_drift_does_not_generate_a_trade():
    """A 1% drift is not worth a $5 fee."""
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "NVDA", "buy", 200, 100.0, AssetClass.STOCK))
    plan = plan_to_target(
        pf, target_weights={"NVDA": 0.205}, classes={"NVDA": AssetClass.STOCK},
        prices={"NVDA": 100.0}, now=NOW,
    )
    assert plan.orders == []


def test_only_tight_classes_get_the_rounding_buffer():
    """A class funded far above the minimum needs no buffer."""
    pf = Portfolio(team="T")
    plan = plan_to_target(
        pf,
        target_weights={"AAA": 0.30, "BBB": 0.098, "FUND": 0.105, "BOND": 0.10},
        classes={
            "AAA": AssetClass.STOCK, "BBB": AssetClass.STOCK,
            "FUND": AssetClass.MUTUAL_FUND, "BOND": AssetClass.BOND,
        },
        prices={"AAA": 100.0, "BBB": 100.0, "FUND": 100.0, "BOND": 100.0},
        now=NOW,
    )
    reasons = {o.symbol: o.reason for o in plan.orders}
    # Stock class totals ~40% of equity, far above $10,000 -- no buffer.
    assert "minimum" not in reasons["BBB"]
    # Fund and bond classes sit right at the minimum -- buffered.
    assert "minimum" in reasons["FUND"]
    assert "minimum" in reasons["BOND"]


def test_fees_are_five_dollars_per_order():
    pf = Portfolio(team="T")
    plan = plan_to_target(
        pf, target_weights={"A": 0.2, "B": 0.2, "C": 0.2},
        classes={s: AssetClass.STOCK for s in "ABC"},
        prices={"A": 10.0, "B": 10.0, "C": 10.0}, now=NOW,
    )
    assert len(plan.orders) == 3
    assert plan.fees == pytest.approx(15.0)


def test_plan_values_the_book_at_the_prices_it_trades_at():
    """Regression: sizing off stale marks pushed positions past the 30% cap.

    The portfolio is marked at 200 but the plan quotes 100. If the planner sizes
    against the stale mark and fills at the quote, every weight lands wrong.
    """
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "AAA", "buy", 100, 200.0, AssetClass.STOCK))
    pf.mark({"AAA": 200.0})

    plan = plan_to_target(
        pf,
        target_weights={"AAA": 0.30, "BBB": 0.30},
        classes={"AAA": AssetClass.STOCK, "BBB": AssetClass.STOCK},
        prices={"AAA": 100.0, "BBB": 50.0},
        now=NOW,
    )
    conc = plan.projected.concentration()
    for sym, w in conc.items():
        assert w <= MAX_POSITION_PCT + 1e-9, f"{sym} at {w:.3%} exceeds the cap"


def test_plan_never_proposes_a_position_over_the_cap():
    """Fee drag lowers equity, so weights must be sized against equity AFTER fees."""
    pf = Portfolio(team="T")
    plan = plan_to_target(
        pf,
        target_weights={s: 0.30 for s in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        classes={s: AssetClass.STOCK for s in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        prices={s: 100.0 for s in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        now=NOW,
    )
    for sym, w in plan.projected.concentration().items():
        assert w <= MAX_POSITION_PCT + 1e-9, f"{sym} at {w:.3%}"


def test_target_gross_over_the_margin_limit_is_scaled_down_not_rejected():
    pf = Portfolio(team="T")
    syms = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")
    plan = plan_to_target(
        pf,
        target_weights={s: 0.30 for s in syms},   # 210% gross, way over the limit
        classes={s: AssetClass.STOCK for s in syms},
        prices={s: 100.0 for s in syms},
        now=NOW,
    )
    assert any("margin limit" in w for w in plan.warnings)
    assert plan.projected.leverage <= 1.5 + 1e-6
