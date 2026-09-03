"""Pitch deck scaffolding, scored against the official evaluation form.

Placing top 25 in your region qualifies you for ICDC. It does not score you
there -- the 20-slide pitch deck and a 15-minute presentation do, against a
100-point rubric. This module builds the deck skeleton with the required
structure and numbering already correct, auto-fills it from your actual
portfolio data, allocates the slide and speaking budget across the rubric in
proportion to the points each section is worth, and prints the penalty-point
checklist.

What it deliberately does NOT do is write your analysis. Sections III and IV
are worth 30 points for *your* research and *your* reasoning, the rules require
that "each team must complete their own research," and a judge who asks one
follow-up question can tell instantly. Every generated section is a prompt with
your real numbers attached, not prose to recite.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field as _field
from pathlib import Path

from .portfolio import Portfolio
from .rules import (
    COMPETITION_END,
    COMPETITION_START,
    MAX_PITCH_DECK_SLIDES,
    MAX_PRESENTATION_MINUTES,
    RANKING_BENCHMARK,
    STARTING_CASH,
)
from .tracker import PerformanceLog

# Presentation Evaluation Form, DECA Guide 2026-27 p.117.
RUBRIC: tuple[tuple[str, int, str], ...] = (
    ("Overview", 15,
     "Delivers a compelling overview of the investment project, including the "
     "portfolio's scope, objectives, and overall performance."),
    ("Portfolio Performance", 15,
     "Analyzes portfolio performance by clearly communicating returns, trends, "
     "and outcomes over the competition period using relevant data and evidence."),
    ("Investment Rationale", 15,
     "Demonstrates informed decision-making by communicating the research "
     "process, diversification strategy, and reasoning behind selected stocks "
     "and/or funds."),
    ("Investment Conclusions", 15,
     "Evaluates the effectiveness of the investment strategy by summarizing key "
     "findings and connecting results to the overall objectives of the portfolio."),
    ("Strategic Reflection", 15,
     "Reflects on the strengths and limitations of the investment strategy and "
     "proposes thoughtful, evidence-based adjustments for future portfolio "
     "management."),
    ("Organization", 5,
     "Information is presented in a logical sequence that can be easily "
     "followed and understood."),
    ("Effectiveness", 5,
     "Presentation effectively persuades, informs, or inspires by communicating "
     "a clear message."),
    ("Delivery", 5,
     "Presentation engages the audience through interactive techniques "
     "(e.g., visual storytelling)."),
    ("Presentation Design", 5,
     "Visual aids and themes are used throughout and are appropriate, "
     "professional, and add value to the presentation."),
    ("Overall Impression", 5,
     "Demonstrates overall career readiness through professionalism, poise and "
     "confidence."),
)

PENALTY_CHECKLIST: tuple[str, ...] = (
    "Cover slide is first. It does NOT need to be numbered '1'.",
    "Title slide is numbered '2' and carries: STOCK MARKET GAME, school name, "
    "school address, city/state/province, ZIP/postal code, participant names, date.",
    "Table of contents is numbered '3' and occupies exactly ONE slide.",
    "Body begins at slide '4'; every following slide is numbered in sequence.",
    "Numbering runs continuously through the bibliography and the appendix.",
    f"Total slide count is at most {MAX_PITCH_DECK_SLIDES}, INCLUDING the appendix.",
    "Every section is titled -- including the bibliography and the appendix.",
    "Bibliography is present. It is required, not optional.",
    "Prepared Event Statement of Assurances and Academic Integrity is signed and "
    "submitted as a SEPARATE document, and is NOT included in the page numbering.",
    f"Presentation runs at most {MAX_PRESENTATION_MINUTES} minutes INCLUDING the "
    "judge's questions and your own setup time.",
    "Every team member speaks. This is explicitly required.",
    "Visual aids are hand-carried and self-set-up. No wheeled carts, no moving straps.",
    "No electrical power and no internet are supplied. Bring nothing that needs either.",
    "No food or drinks.",
)


@dataclass
class Section:
    """One deck section with its slide budget and prompts."""

    number: str
    title: str
    slides: int
    rubric_points: int
    prompts: list[str] = _field(default_factory=list)
    data: list[str] = _field(default_factory=list)


@dataclass
class DeckPlan:
    """A complete deck plan: numbered slides, prompts, and the checklist."""

    team: str
    school: str
    address: str
    city_state_zip: str
    participants: list[str]
    date: _dt.date
    sections: list[Section]
    charts: list[str] = _field(default_factory=list)
    stats: dict = _field(default_factory=dict)

    @property
    def total_slides(self) -> int:
        # cover + title + table of contents + body
        return 3 + sum(s.slides for s in self.sections)

    def speaking_plan(self) -> list[tuple[str, float]]:
        """Minutes per section, weighted by rubric points.

        Budgets 11 of the 15 minutes for presenting, leaving 4 for the judge's
        questions -- which count against the same 15 minutes and are where the
        Overall Impression points are won or lost.
        """
        present = MAX_PRESENTATION_MINUTES - 4.0
        total = sum(s.rubric_points for s in self.sections) or 1
        return [
            (s.title, round(present * s.rubric_points / total, 1))
            for s in self.sections
        ]

    def render(self) -> str:
        """The full deck plan as markdown."""
        lines = [
            f"# Pitch Deck Plan -- {self.team}",
            "",
            f"*Stock Market Game | {COMPETITION_START:%b %d} - "
            f"{COMPETITION_END:%b %d, %Y} | generated {_dt.date.today():%b %d, %Y}*",
            "",
            f"**{self.total_slides} of {MAX_PITCH_DECK_SLIDES} slides used.**",
        ]
        if self.total_slides > MAX_PITCH_DECK_SLIDES:
            lines.append(
                f"> **OVER THE LIMIT by {self.total_slides - MAX_PITCH_DECK_SLIDES} "
                f"slide(s).** The cap includes the appendix. Cut before submitting."
            )
        lines += ["", "---", "", "## Front matter", "",
                  "**Slide 1 -- Cover** *(need not be numbered)*", "",
                  f"- Stock Market Game -- {self.team}", "",
                  "**Slide 2 -- Title** *(numbered `2`)*", "",
                  "- STOCK MARKET GAME",
                  f"- {self.school}",
                  f"- {self.address}",
                  f"- {self.city_state_zip}",
                  f"- {', '.join(self.participants) if self.participants else '[participant names]'}",
                  f"- {self.date:%B %d, %Y}", "",
                  "**Slide 3 -- Table of Contents** *(numbered `3`, exactly one slide)*",
                  ""]
        for s in self.sections:
            lines.append(f"- {s.number}. {s.title}")
        lines += ["", "---", "", "## Body"]

        slide_no = 4
        for s in self.sections:
            span = (
                f"slide `{slide_no}`" if s.slides == 1
                else f"slides `{slide_no}`-`{slide_no + s.slides - 1}`"
            )
            lines += [
                "",
                f"### {s.number}. {s.title}",
                "",
                f"*{span} | {s.slides} slide(s) | worth {s.rubric_points} rubric points*",
                "",
            ]
            if s.data:
                lines.append("**Your data (auto-filled):**")
                lines.append("")
                for d in s.data:
                    lines.append(f"- {d}")
                lines.append("")
            if s.prompts:
                lines.append("**What the judge is scoring -- answer these:**")
                lines.append("")
                for p in s.prompts:
                    lines.append(f"- [ ] {p}")
                lines.append("")
            slide_no += s.slides

        if self.charts:
            lines += ["---", "", "## Generated charts", "",
                      "Drop these into Section II:", ""]
            for c in self.charts:
                lines.append(f"- `{c}`")
            lines.append("")

        lines += ["---", "", "## Speaking plan",
                  "",
                  f"{MAX_PRESENTATION_MINUTES} minutes total, INCLUDING the judge's "
                  f"questions. Budget 11 minutes of content and hold 4 back.",
                  "",
                  "| Section | Minutes |", "| --- | --- |"]
        for title, mins in self.speaking_plan():
            lines.append(f"| {title} | {mins} |")
        lines += [f"| *Judge's questions* | *4.0* |", "",
                  f"Split the speaking across all {len(self.participants) or 1} "
                  f"member(s) -- every participant must take part.", ""]

        lines += ["---", "", "## Scoring rubric (100 points)", "",
                  "| # | Category | Points |", "| --- | --- | --- |"]
        for i, (name, pts, _) in enumerate(RUBRIC, 1):
            lines.append(f"| {i} | {name} | {pts} |")
        lines += ["", "---", "", "## Penalty point checklist", ""]
        for c in PENALTY_CHECKLIST:
            lines.append(f"- [ ] {c}")
        lines.append("")
        return "\n".join(lines)


def build_deck(
    pf: Portfolio | None = None,
    log: PerformanceLog | None = None,
    team: str = "",
    school: str = "[School name]",
    address: str = "[School address]",
    city_state_zip: str = "[City, State/Province, ZIP]",
    participants: list[str] | None = None,
    charts: list[str] | None = None,
    date: _dt.date | None = None,
) -> DeckPlan:
    """Build a deck plan, auto-filled from real portfolio and log data."""
    participants = participants or []
    stats = log.stats() if log else {}
    data_over: list[str] = []
    data_perf: list[str] = []
    data_rat: list[str] = []
    data_con: list[str] = []

    if pf is not None:
        eq = pf.total_equity
        data_over += [
            f"Starting capital ${STARTING_CASH:,.0f}; final Total Equity ${eq:,.2f}.",
            f"Percent Return **{pf.percent_return:+.2%}** -- the metric rankings use.",
            f"{len(pf.holdings)} position(s) across "
            f"{len({h.asset_class for h in pf.holdings})} asset classes.",
        ]
        if pf.margin_loan > 0:
            data_over.append(
                f"Margin borrowed ${pf.margin_loan:,.0f} "
                f"({pf.leverage:.2f}x gross leverage). Rankings are net of it."
            )
        nc = pf.net_cost_by_class()
        data_rat.append(
            "Net cost by class: "
            + ", ".join(f"{k.value.replace('_', ' ')} ${v:,.0f}" for k, v in nc.items())
            + " (the $10,000 minimum per class was a binding constraint)."
        )
        conc = pf.concentration()
        if conc:
            top = sorted(conc.items(), key=lambda kv: -kv[1])[:3]
            data_rat.append(
                "Largest positions: "
                + ", ".join(f"{s} {w:.1%} of equity" for s, w in top)
                + " (30% single-security cap)."
            )
        winners = sorted(pf.holdings, key=lambda h: -h.unrealized)[:3]
        losers = sorted(pf.holdings, key=lambda h: h.unrealized)[:3]
        if winners:
            data_con.append(
                "Top contributors: "
                + ", ".join(f"{h.symbol} {h.unrealized:+,.0f} ({h.return_pct:+.1%})"
                            for h in winners)
            )
        if losers and losers[0].unrealized < 0:
            data_con.append(
                "Largest detractors: "
                + ", ".join(f"{h.symbol} {h.unrealized:+,.0f} ({h.return_pct:+.1%})"
                            for h in losers if h.unrealized < 0)
            )
        data_con.append(f"Total fees paid: ${sum(t.fee for t in pf.transactions):,.2f} "
                        f"across {len(pf.transactions)} trades.")

    if stats:
        data_perf += [
            f"Percent Return {stats['current_return']:+.2%} vs "
            f"{RANKING_BENCHMARK} {stats['benchmark_return']:+.2%} -- "
            f"excess **{stats['excess_return']:+.2%}**.",
            f"Maximum drawdown {stats['max_drawdown']:+.2%}; realized volatility "
            f"{stats['realized_vol_annualized']:.1%} annualized.",
            f"Best day {stats['best_daily_move']:+.2%}, worst day "
            f"{stats['worst_daily_move']:+.2%}, over {stats['days_logged']} "
            f"days logged.",
        ]

    sections = [
        Section("I", "Overview", 1, 15, data=data_over, prompts=[
            "State the portfolio's scope and objectives in one sentence a judge "
            "can repeat back.",
            "Give the headline Percent Return up front -- do not make the judge "
            "wait for it.",
            "Name the strategy in a few words (e.g. concentrated growth with a "
            "required-minimum bond sleeve).",
            "The guidelines specify ONE slide here. Respect it.",
        ]),
        Section("II", "Analysis of Portfolio Performance", 4, 15, data=data_perf,
                prompts=[
                    "Include charts and diagrams -- the guidelines require them "
                    "explicitly and 15 points ride on it.",
                    "Show the return path against the benchmark, not just the "
                    "final number. Trends and outcomes are named in the rubric.",
                    "Explain your largest drawdown and what caused it. Owning a "
                    "loss reads as competence, not weakness.",
                    "Tie each visible inflection to a decision or a market event.",
                ]),
        Section("III", "Rationale", 4, 15, data=data_rat, prompts=[
            "A. Describe the research you did BEFORE selecting -- sources, "
            "screens, metrics. This is your own work and a judge will probe it.",
            "B. Explain the diversification strategy, including how you met the "
            "$10,000-per-asset-class requirement and what it cost you.",
            "C. Show how each selection fits the strategy. Every position needs "
            "a reason you can defend in one sentence.",
            "Explain the constraints you optimized against: the 30% cap, 50% "
            "margin, the $5 fee, end-of-day pricing.",
        ]),
        Section("IV", "Conclusions and Findings", 4, 30, data=data_con, prompts=[
            "A. Was the strategy effective? Answer with evidence, and connect "
            "results back to the objectives you stated in Section I.",
            "A. Separate skill from luck honestly. Judges reward the team that "
            "can tell the difference.",
            "B. Propose specific changes for future investing -- this is "
            "Strategic Reflection, a full 15 points of its own.",
            "B. Name the strategy's limitations, not only its strengths. The "
            "rubric asks for both.",
        ]),
        Section("V", "Bibliography", 1, 0, prompts=[
            "Required, not optional. List every source used in the deck.",
            "Cite data providers, screeners, filings and news you actually used.",
            "Keep it consistently formatted -- Organization is 5 points.",
        ]),
        Section("VI", "Appendix", 3, 0, prompts=[
            "Optional. Exhibits appropriate to the deck but not important enough "
            "for the body: the full transaction log, per-position tables, "
            "additional charts.",
            "Appendix slides COUNT toward the 20-slide cap. Cut here first.",
            "Numbering continues in sequence through the appendix.",
        ]),
    ]

    return DeckPlan(
        team=team or (pf.team if pf else "My Team"),
        school=school,
        address=address,
        city_state_zip=city_state_zip,
        participants=participants,
        date=date or _dt.date.today(),
        sections=sections,
        charts=charts or [],
        stats=stats,
    )


def write_deck(plan: DeckPlan, path: str | Path = "pitch_deck_plan.md") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plan.render())
    return p
