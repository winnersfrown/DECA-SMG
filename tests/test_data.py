"""The data layer must degrade gracefully and never return None."""

import datetime as dt

import pytest

from decasmg.data import PriceHistory, align, get_history, import_csv

OFFLINE = ("cache", "synthetic")


def test_synthetic_is_deterministic_per_symbol():
    a = get_history("NVDA", days=120, providers=("synthetic",), use_cache=False)
    b = get_history("NVDA", days=120, providers=("synthetic",), use_cache=False)
    assert a.closes == b.closes
    c = get_history("AMD", days=120, providers=("synthetic",), use_cache=False)
    assert c.closes != a.closes


def test_get_history_always_returns_a_usable_series():
    h = get_history("NOT_A_REAL_TICKER_XYZ", days=60, providers=OFFLINE)
    assert len(h) >= 2
    assert h.last > 0


def test_statistics_are_sane():
    h = get_history("TSLA", days=252, providers=("synthetic",), use_cache=False)
    assert 0.0 < h.annualized_vol() < 3.0
    assert -1.0 <= h.max_drawdown() <= 0.0
    assert len(h.returns()) == len(h) - 1


def test_align_restricts_to_shared_dates():
    d = [dt.date(2026, 1, i) for i in range(1, 6)]
    a = PriceHistory("A", d, [1, 2, 3, 4, 5])
    b = PriceHistory("B", d[1:4], [10, 20, 30])
    dates, out = align({"A": a, "B": b})
    assert dates == d[1:4]
    assert out["A"] == [2, 3, 4]
    assert out["B"] == [10, 20, 30]


def test_csv_import_is_the_escape_hatch(tmp_path):
    p = tmp_path / "MYSTOCK.csv"
    p.write_text("Date,Close\n2026-01-02,100.0\n2026-01-05,110.0\n2026-01-06,99.0\n")
    h = import_csv(p)
    assert h.symbol == "MYSTOCK"
    assert h.closes == [100.0, 110.0, 99.0]
    assert h.total_return() == pytest.approx(-0.01)
