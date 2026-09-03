"""The optimizer must never propose a portfolio that breaks a rule."""

import pytest

pytest.importorskip("numpy")

from decasmg.optimize import MAX_GROSS, optimize
from decasmg.rules import (
    AssetClass,
    MAX_POSITION_PCT,
    MIN_NET_COST_PER_ASSET_CLASS,
    STARTING_CASH,
)
from decasmg.tournament import FactorParams

STOCKS = {
    s: FactorParams(s, beta=1.0 + i * 0.1, idio_vol=0.35 + i * 0.08,
                    total_vol=0.5, drift=0.1)
    for i, s in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"])
}
FUNDS = {
    "XXXXX": FactorParams("XXXXX", beta=1.1, idio_vol=0.30, total_vol=0.4, drift=0.09)
}
FACTORS = {**STOCKS, **FUNDS}
CLASSES = {**{s: AssetClass.STOCK for s in STOCKS},
           **{s: AssetClass.MUTUAL_FUND for s in FUNDS}}


@pytest.fixture(scope="module")
def alloc():
    return optimize(FACTORS, CLASSES, days=45, equity=STARTING_CASH,
                    n_candidates=800, n_sims=4000, seed=11)


def test_no_position_exceeds_the_thirty_percent_cap(alloc):
    assert alloc.feasible
    for sym, w in alloc.weights.items():
        assert abs(w) <= MAX_POSITION_PCT + 1e-9, sym


def test_gross_exposure_respects_the_margin_limit(alloc):
    assert alloc.gross <= MAX_GROSS + 1e-9


def test_every_required_asset_class_meets_its_minimum(alloc):
    by_class = {}
    for sym, d in alloc.dollars.items():
        by_class.setdefault(alloc.classes[sym], 0.0)
        by_class[alloc.classes[sym]] += d
    for ac in (AssetClass.STOCK, AssetClass.MUTUAL_FUND, AssetClass.BOND):
        assert by_class.get(ac, 0.0) >= MIN_NET_COST_PER_ASSET_CLASS - 1e-6, ac


def test_the_bond_slug_is_held_at_exactly_the_minimum(alloc):
    """Bonds are required drag -- never a dollar more than the rules demand."""
    bond = [s for s, c in alloc.classes.items() if c is AssetClass.BOND]
    assert len(bond) == 1
    assert alloc.dollars[bond[0]] == pytest.approx(MIN_NET_COST_PER_ASSET_CLASS)


def test_the_rules_force_at_least_five_positions_at_full_margin(alloc):
    """30% per name against 150% gross makes fewer than 5 impossible."""
    if alloc.gross > 1.4:
        assert alloc.n_positions >= 5


def test_unlevered_search_stays_at_or_below_one_hundred_percent():
    a = optimize(FACTORS, CLASSES, days=45, allow_margin=False,
                 n_candidates=600, n_sims=4000, seed=3)
    assert a.feasible
    assert a.gross <= 1.0 + 1e-9


def test_missing_a_fund_candidate_is_reported_not_silently_ignored():
    a = optimize(STOCKS, {s: AssetClass.STOCK for s in STOCKS}, days=45,
                 n_candidates=200, n_sims=2000)
    assert not a.feasible
    assert any("mutual fund" in n.lower() for n in a.notes)


def test_tiny_equity_is_infeasible_and_says_why():
    """$10,000 per class cannot fit under a 30% cap on a small balance."""
    a = optimize(FACTORS, CLASSES, days=45, equity=20_000,
                 n_candidates=100, n_sims=2000)
    assert not a.feasible
    assert any("exceeds" in n for n in a.notes)
