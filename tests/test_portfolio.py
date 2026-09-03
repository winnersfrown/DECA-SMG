"""Valuation must match how SMG scores a team."""

import datetime as dt

import pytest

from decasmg.portfolio import Holding, Portfolio, Transaction
from decasmg.rules import AssetClass, STARTING_CASH

D = dt.date(2026, 9, 8)


def _pf():
    return Portfolio(team="T")


def test_buy_reduces_cash_by_notional_plus_fee():
    pf = _pf()
    pf.apply(Transaction(D, "NVDA", "buy", 100, 150.0, AssetClass.STOCK))
    assert pf.cash == pytest.approx(STARTING_CASH - 15_000 - 5)
    assert pf.total_equity == pytest.approx(STARTING_CASH - 5)


def test_percent_return_is_net_of_borrowed_funds():
    """Leverage must not inflate the ranking metric on its own."""
    pf = _pf()
    # Buy $150k of stock using $50k of margin.
    pf.apply(Transaction(D, "NVDA", "buy", 1000, 150.0, AssetClass.STOCK))
    assert pf.margin_loan == pytest.approx(50_005.0)
    # Before any price move, equity is unchanged apart from the fee.
    assert pf.total_equity == pytest.approx(STARTING_CASH - 5)
    assert pf.percent_return == pytest.approx(-5 / STARTING_CASH)
    # A 10% gain on 1.5x exposure is a ~15% gain on equity.
    pf.mark({"NVDA": 165.0})
    assert pf.percent_return == pytest.approx(0.15, abs=1e-3)


def test_short_position_is_a_liability_and_profits_when_price_falls():
    pf = _pf()
    pf.apply(Transaction(D, "TSLA", "short", 100, 300.0, AssetClass.STOCK))
    assert pf.cash == pytest.approx(STARTING_CASH + 30_000 - 5)
    assert pf.total_equity == pytest.approx(STARTING_CASH - 5)
    pf.mark({"TSLA": 250.0})
    assert pf.total_equity == pytest.approx(STARTING_CASH - 5 + 5_000)
    h = pf.get("TSLA", is_short=True)
    assert h.unrealized == pytest.approx(5_000)
    assert h.return_pct > 0


def test_shorts_do_not_count_toward_stock_diversification():
    """Only LONG stock positions satisfy the stock minimum."""
    pf = _pf()
    pf.apply(Transaction(D, "TSLA", "short", 500, 300.0, AssetClass.STOCK))
    assert pf.net_cost_by_class()[AssetClass.STOCK] == 0.0
    pf.apply(Transaction(D, "NVDA", "buy", 100, 150.0, AssetClass.STOCK))
    assert pf.net_cost_by_class()[AssetClass.STOCK] == pytest.approx(15_000)


def test_appreciated_position_keeps_shares_but_loses_headroom():
    pf = _pf()
    pf.apply(Transaction(D, "NVDA", "buy", 190, 150.0, AssetClass.STOCK))
    assert pf.headroom("NVDA") > 0
    pf.mark({"NVDA": 250.0})
    assert pf.concentration()["NVDA"] > 0.30
    assert pf.headroom("NVDA") == 0.0        # no more may be bought
    assert pf.get("NVDA").quantity == 190    # but the shares are retained


def test_margin_capacity_tracks_equity():
    pf = _pf()
    assert pf.margin_capacity == pytest.approx(50_000)
    pf.apply(Transaction(D, "NVDA", "buy", 1000, 150.0, AssetClass.STOCK))
    assert pf.margin_capacity == pytest.approx(0.0, abs=10)


def test_average_cost_blends_across_purchases():
    pf = _pf()
    pf.apply(Transaction(D, "NVDA", "buy", 100, 100.0, AssetClass.STOCK))
    pf.apply(Transaction(D, "NVDA", "buy", 100, 200.0, AssetClass.STOCK))
    assert pf.get("NVDA").avg_cost == pytest.approx(150.0)
    assert pf.get("NVDA").net_cost == pytest.approx(30_000)


def test_cannot_oversell_or_overcover():
    pf = _pf()
    pf.apply(Transaction(D, "NVDA", "buy", 10, 100.0, AssetClass.STOCK))
    with pytest.raises(ValueError):
        pf.apply(Transaction(D, "NVDA", "sell", 11, 100.0, AssetClass.STOCK))
    with pytest.raises(ValueError):
        pf.apply(Transaction(D, "AAPL", "sell", 1, 100.0, AssetClass.STOCK))
    with pytest.raises(ValueError):
        pf.apply(Transaction(D, "AAPL", "cover", 1, 100.0, AssetClass.STOCK))


def test_round_trips_through_json(tmp_path):
    pf = _pf()
    pf.apply(Transaction(D, "NVDA", "buy", 100, 150.0, AssetClass.STOCK))
    pf.apply(Transaction(D, "TSLA", "short", 50, 300.0, AssetClass.STOCK))
    p = tmp_path / "pf.json"
    pf.save(p)
    back = Portfolio.load(p)
    assert back.total_equity == pytest.approx(pf.total_equity)
    assert len(back.holdings) == 2
    assert back.get("TSLA", is_short=True) is not None
