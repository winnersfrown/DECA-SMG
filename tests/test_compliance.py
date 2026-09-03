"""The disqualification firewall. These are the tests that matter most."""

import datetime as dt

from decasmg.compliance import Severity, audit, check_diversification, validate_order
from decasmg.portfolio import Portfolio, Transaction
from decasmg.rules import AssetClass

D = dt.date(2026, 9, 8)
BEFORE_DEADLINE = dt.datetime(2026, 10, 1, 10, 0)
AFTER_DEADLINE = dt.datetime(2026, 10, 26, 10, 0)


def _funded(stock=15_000, fund=10_500, bond=10_500):
    pf = Portfolio(team="T")
    if stock:
        pf.apply(Transaction(D, "NVDA", "buy", stock / 100, 100.0, AssetClass.STOCK))
    if fund:
        pf.apply(Transaction(D, "FSELX", "buy", fund / 100, 100.0, AssetClass.MUTUAL_FUND))
    if bond:
        pf.apply(Transaction(D, "BOND", "buy", bond / 100, 100.0, AssetClass.BOND))
    return pf


def test_missing_class_is_a_warning_before_the_deadline_and_a_violation_after():
    pf = _funded(bond=0)
    before = check_diversification(pf, BEFORE_DEADLINE)
    bond_before = [f for f in before if "bond" in f.rule][0]
    assert bond_before.severity in (Severity.WARNING, Severity.INFO)

    after = check_diversification(pf, AFTER_DEADLINE)
    bond_after = [f for f in after if "bond" in f.rule][0]
    assert bond_after.severity is Severity.VIOLATION
    assert "one business day" in bond_after.remedy


def test_fully_funded_portfolio_passes_diversification():
    findings = check_diversification(_funded(), AFTER_DEADLINE)
    assert all(f.severity is Severity.OK for f in findings)


def test_bond_etf_and_bond_fund_do_not_satisfy_the_bond_class():
    """The classification trap: only an actual bond fills the bond minimum."""
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "NVDA", "buy", 150, 100.0, AssetClass.STOCK))
    pf.apply(Transaction(D, "BND", "buy", 150, 100.0, AssetClass.STOCK))       # bond ETF
    pf.apply(Transaction(D, "FBNDX", "buy", 150, 100.0, AssetClass.MUTUAL_FUND))  # bond fund
    bond = [f for f in check_diversification(pf, AFTER_DEADLINE) if "bond" in f.rule][0]
    assert bond.severity is Severity.VIOLATION


def test_purchased_over_cap_is_a_violation_but_drifting_over_is_not():
    bought_over = Portfolio(team="T")
    bought_over.apply(Transaction(D, "NVDA", "buy", 400, 100.0, AssetClass.STOCK))
    findings = audit(bought_over, now=BEFORE_DEADLINE).findings
    conc = [f for f in findings if f.rule.startswith("Concentration/NVDA")][0]
    assert conc.severity is Severity.VIOLATION

    drifted = Portfolio(team="T")
    drifted.apply(Transaction(D, "NVDA", "buy", 250, 100.0, AssetClass.STOCK))
    drifted.mark({"NVDA": 200.0})
    findings = audit(drifted, now=BEFORE_DEADLINE).findings
    conc = [f for f in findings if f.rule.startswith("Concentration/NVDA")][0]
    assert conc.severity is Severity.INFO
    assert "keep the shares" in conc.remedy


def test_unranked_portfolio_is_flagged_once_the_competition_is_open():
    empty = Portfolio(team="T")
    findings = audit(empty, now=BEFORE_DEADLINE).findings
    act = [f for f in findings if f.rule == "Initial transaction"][0]
    assert act.severity is Severity.VIOLATION
    assert "not ranked" in act.message


def test_team_larger_than_three_is_a_violation():
    findings = audit(_funded(), members=["a", "b", "c", "d"], now=BEFORE_DEADLINE).findings
    size = [f for f in findings if f.rule == "Team size"][0]
    assert size.severity is Severity.VIOLATION


def test_pretrade_blocks_a_purchase_past_the_thirty_percent_cap():
    pf = _funded()
    bad = validate_order(pf, "NVDA", "buy", 400, 100.0, AssetClass.STOCK)
    assert any(f.severity is Severity.VIOLATION for f in bad)
    ok = validate_order(pf, "AMD", "buy", 50, 100.0, AssetClass.STOCK)
    assert all(f.severity is Severity.OK for f in ok)


def test_pretrade_blocks_a_purchase_beyond_buying_power():
    pf = _funded()
    findings = validate_order(pf, "AMD", "buy", 100_000, 100.0, AssetClass.STOCK)
    assert any("buying power" in f.rule for f in findings
               if f.severity is Severity.VIOLATION)


def test_pretrade_warns_when_a_sale_would_break_a_class_minimum():
    pf = _funded()
    findings = validate_order(pf, "FSELX", "sell", 60, 100.0, AssetClass.MUTUAL_FUND)
    warn = [f for f in findings if "diversification" in f.rule]
    assert warn and warn[0].severity is Severity.WARNING


def test_margin_over_limit_is_a_violation():
    pf = Portfolio(team="T", cash=-60_000)
    pf.apply(Transaction(D, "NVDA", "buy", 1, 1.0, AssetClass.STOCK))
    pf.holdings[0].quantity = 1600
    pf.holdings[0].avg_cost = 100.0
    pf.holdings[0].price = 100.0
    findings = audit(pf, now=BEFORE_DEADLINE).findings
    margin = [f for f in findings if f.rule == "Margin"][0]
    assert margin.severity is Severity.VIOLATION


def test_report_exit_status_reflects_violations():
    assert audit(_funded(), now=AFTER_DEADLINE).is_compliant
    assert not audit(_funded(bond=0), now=AFTER_DEADLINE).is_compliant
