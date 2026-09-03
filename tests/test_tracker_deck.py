"""Performance logging and the pitch deck plan."""

import datetime as dt

import pytest

from decasmg.deck import PENALTY_CHECKLIST, RUBRIC, build_deck
from decasmg.portfolio import Portfolio, Transaction
from decasmg.rules import AssetClass, MAX_PITCH_DECK_SLIDES
from decasmg.tracker import PerformanceLog

D = dt.date(2026, 9, 8)


def _pf():
    pf = Portfolio(team="T")
    pf.apply(Transaction(D, "NVDA", "buy", 100, 100.0, AssetClass.STOCK))
    return pf


def test_log_replaces_rather_than_duplicates_a_date(tmp_path):
    log = PerformanceLog(tmp_path / "log.json")
    pf = _pf()
    log.record(pf, when=D)
    pf.mark({"NVDA": 120.0})
    log.record(pf, when=D)
    assert len(log.snapshots) == 1
    assert log.snapshots[0].total_equity == pf.total_equity


def test_log_persists_across_instances(tmp_path):
    p = tmp_path / "log.json"
    PerformanceLog(p).record(_pf(), when=D)
    assert len(PerformanceLog(p).snapshots) == 1


def test_excess_return_is_portfolio_minus_benchmark(tmp_path):
    log = PerformanceLog(tmp_path / "log.json")
    snap = log.record(_pf(), benchmark_return=0.02, when=D)
    assert snap.excess == pytest.approx(snap.percent_return - 0.02)


def test_csv_export_has_a_header_and_a_row_per_snapshot(tmp_path):
    log = PerformanceLog(tmp_path / "log.json")
    pf = _pf()
    for i in range(3):
        log.record(pf, when=D + dt.timedelta(days=i))
    rows = log.export_csv(tmp_path / "out.csv").read_text().strip().splitlines()
    assert len(rows) == 4
    assert rows[0].startswith("date,total_equity,percent_return")


def test_rubric_sums_to_one_hundred_points():
    assert sum(pts for _, pts, _ in RUBRIC) == 100


def test_deck_plan_fits_the_twenty_slide_cap():
    plan = build_deck(pf=_pf(), participants=["A", "B"])
    assert plan.total_slides <= MAX_PITCH_DECK_SLIDES


def test_deck_follows_the_required_section_sequence():
    plan = build_deck(pf=_pf())
    assert [s.number for s in plan.sections] == ["I", "II", "III", "IV", "V", "VI"]
    assert plan.sections[0].title == "Overview"
    assert plan.sections[0].slides == 1          # guidelines: one slide
    assert plan.sections[4].title == "Bibliography"  # required, not optional


def test_deck_body_starts_at_slide_four():
    md = build_deck(pf=_pf()).render()
    assert "**Slide 2 -- Title**" in md
    assert "**Slide 3 -- Table of Contents**" in md
    assert "*slide `4`" in md


def test_speaking_plan_leaves_room_for_judge_questions():
    plan = build_deck(pf=_pf())
    assert sum(m for _, m in plan.speaking_plan()) == pytest.approx(11.0, abs=0.3)


def test_penalty_checklist_covers_the_statement_of_assurances():
    joined = " ".join(PENALTY_CHECKLIST).lower()
    assert "statement of assurances" in joined
    assert "separate document" in joined
    assert "bibliography" in joined
