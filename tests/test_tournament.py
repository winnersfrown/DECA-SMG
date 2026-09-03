"""The tournament model must behave the way tournament mathematics says it should."""

import pytest

np = pytest.importorskip("numpy")

from decasmg.tournament import (
    FieldModel,
    advise_risk,
    p_qualify,
    portfolio_factors,
    sample_field,
    simulate,
)
from decasmg.tournament import FactorParams

SIMS = 6000
DAYS = 45


def _fs(**kw):
    return sample_field(DAYS, FieldModel(**kw), n_sims=SIMS, seed=1)


def test_more_idiosyncratic_volatility_raises_p_qualify():
    """The central claim: for a top-1% cutoff, variance is the point."""
    fs = _fs()
    ps = [p_qualify(fs, my_beta=1.0, my_idio_vol=v, my_leverage=1.0)
          for v in (0.10, 0.25, 0.45, 0.70)]
    assert ps == sorted(ps)
    assert ps[-1] > ps[0] * 5


def test_idiosyncratic_risk_beats_beta_at_equal_leverage():
    """Beta lifts the qualifying bar along with you; idiosyncratic risk does not."""
    fs = _fs()
    high_beta = p_qualify(fs, my_beta=2.0, my_idio_vol=0.10, my_leverage=1.5)
    high_idio = p_qualify(fs, my_beta=0.4, my_idio_vol=0.50, my_leverage=1.5)
    assert high_idio > high_beta


def test_a_larger_region_is_harder():
    small = p_qualify(_fs(n_teams=200), 1.0, 0.45, 1.5)
    large = p_qualify(_fs(n_teams=3000), 1.0, 0.45, 1.5)
    assert small > large


def test_being_ahead_helps_and_being_behind_hurts():
    fs = _fs()
    behind = p_qualify(fs, 1.0, 0.45, 1.5, current_return=-0.20)
    flat = p_qualify(fs, 1.0, 0.45, 1.5, current_return=0.0)
    ahead = p_qualify(fs, 1.0, 0.45, 1.5, current_return=0.40)
    assert behind < flat < ahead


def test_leverage_raises_p_qualify_when_far_behind():
    fs = _fs()
    low = p_qualify(fs, 1.0, 0.35, 1.0)
    high = p_qualify(fs, 1.0, 0.35, 1.5)
    assert high > low


def test_advisor_says_cut_risk_when_safely_ahead_late():
    adv = advise_risk(my_beta=1.0, current_idio_vol=0.60, current_leverage=1.5,
                      days=5, current_return=1.20, n_sims=4000)
    assert adv.posture == "DEFEND"
    assert adv.target_idio_vol <= 0.20
    assert adv.target_leverage == 1.0


def test_advisor_says_add_risk_when_far_behind():
    adv = advise_risk(my_beta=1.0, current_idio_vol=0.12, current_leverage=1.0,
                      days=40, current_return=0.01, n_sims=4000)
    assert adv.posture in ("BUILD VARIANCE", "ALL-IN", "ADD RISK, SELECTIVELY")
    assert adv.target_idio_vol > 0.12
    assert adv.p_at_target > adv.p_now


def test_portfolio_factors_diversify_idiosyncratic_risk():
    """Four independent 60%-vol names carry less idio risk than one does."""
    f = {s: FactorParams(s, beta=1.0, idio_vol=0.60, total_vol=0.7, drift=0.1)
         for s in "ABCD"}
    beta1, idio1 = portfolio_factors({"A": 1.0}, f)
    beta4, idio4 = portfolio_factors({s: 0.25 for s in "ABCD"}, f)
    assert beta1 == pytest.approx(beta4)
    assert idio4 == pytest.approx(idio1 / 2, rel=1e-6)  # 1/sqrt(4)


def test_field_sample_is_reusable_and_deterministic():
    a = sample_field(DAYS, FieldModel(), n_sims=2000, seed=7)
    b = sample_field(DAYS, FieldModel(), n_sims=2000, seed=7)
    assert np.allclose(a.thresholds, b.thresholds)
    assert p_qualify(a, 1.0, 0.4, 1.2) == p_qualify(b, 1.0, 0.4, 1.2)


def test_simulate_reports_a_coherent_required_return():
    res = simulate(my_beta=1.0, my_idio_vol=0.3, days=DAYS, n_sims=SIMS,
                   current_return=0.10)
    assert res.threshold_p10 < res.median_threshold < res.threshold_p90
    expected = (1 + res.median_threshold) / 1.10 - 1
    assert res.required_return == pytest.approx(expected, abs=1e-4)
