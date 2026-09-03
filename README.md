# DECA Stock Market Game Toolkit

Tools for placing **top 25 in your DECA region** in the 2026–27 Stock Market
Game (September 8 – December 4, 2026), and for building the ICDC pitch deck
afterward.

Every rule, date, fee and limit is taken from the official
*DECA Guide 2026-27* Stock Market Game guidelines and encoded in
[`decasmg/rules.py`](decasmg/rules.py) as the single source of truth.

---

## The idea behind the toolkit

Rankings use **Percent Return**, and the payoff is a step function: the top 25
teams in each region qualify for ICDC, and rank 26 gets nothing. That makes this
a **tournament**, not an investing problem — and the two have opposite optima.

Ordinary investing maximizes risk-adjusted expected return. A tournament with a
top-1% cutoff maximizes **P(return > threshold)**. A steady +8% portfolio has an
excellent Sharpe ratio and almost no chance of placing, because a hundred teams
in your region will clear +8% by luck alone.

Four consequences the tools act on:

1. **Variance is the point.** Insufficient volatility, not excess volatility, is
   what keeps teams out of the top 25. The toolkit will often recommend more
   risk than feels comfortable — and it will tell you to cut risk once you are
   safely above the bar.

2. **Market beta is nearly worthless; idiosyncratic risk is not.** If the market
   rallies you gain, but so does everyone, and the qualifying bar rises with
   you. Only the *uncorrelated* part of your return moves you up the ranking.
   This is why the simulator runs your portfolio and the field against a
   **shared** market path, and why the screener ranks by idiosyncratic
   volatility rather than by total volatility or by quality.

3. **The rules set a floor on diversification.** A 30% per-name cap against 150%
   gross exposure makes fewer than five positions impossible. And $10,000 each
   in a mutual fund and a bond is *forced* capital — so hold the bond at exactly
   the minimum, and put the fund slug somewhere that actually moves.

4. **Intraday timing is worth nothing.** Every order fills at a closing price,
   so there is no advantage to watching a ticker all day. The flat $5 fee
   punishes churn: 0.03% on a $15,000 order, 1.0% on a $500 one.

Run `smg explain` for the reasoning with a live demonstration.

**The honest caveat:** none of this predicts returns. It shapes the
*distribution* of your outcomes toward the tail that qualifies. Raising your
probability of a top-25 finish from 3% to 30% is a real edge — and still loses
most of the time.

---

## Install

```bash
git clone https://github.com/winnersfrown/deca-smg.git
cd deca-smg
pip install -e ".[all]"
```

The compliance engine — the part that keeps you from being disqualified — is
**pure standard library** and works with no dependencies at all. `numpy` is
needed for simulation, `matplotlib` for charts, and `yfinance`/`requests` for
live market data.

---

## Quick start

```bash
smg rules                      # every date, limit and classification trap
smg init --team "Team Alpha"   # create portfolio.json
smg screen --why               # rank candidates by contribution to P(top 25)
smg optimize --save target.json
smg plan --target target.json  # an executable, rule-checked order list
smg check                      # never skip this
```

Then, once you have placed the trades in SMG:

```bash
smg record NVDA buy 129 167.21
smg record FSELX buy 60.7 173.37 --asset-class mutual_fund
smg record T-NOTE-2030 buy 103.6 99.40 --asset-class bond
```

---

## Daily and weekly routine

**Every trading day (about 30 seconds):**

```bash
smg check      # compliance audit -- exits non-zero if a rule is broken
smg track      # log today's Percent Return against the benchmark
```

**Weekly, or whenever your position changes materially:**

```bash
smg odds --advise    # P(top 25), and how much risk your standing calls for
smg optimize         # re-solve the allocation for the days that remain
```

**Two hard deadlines, both disqualifying — put them in your calendar:**

| Date | Deadline | Why it ends your run |
| --- | --- | --- |
| **Fri Oct 16, 2026, 4 p.m. ET** | Student name submission | Only students submitted before this instant are *eligible to rank in the top 25*. A misspelled name is an ineligible name. |
| **Fri Oct 23, 2026, 4 p.m. ET** | Asset diversification | $10,000 net cost in **each** of stock, mutual fund and bond, held through December 4. |

```bash
smg plan --fix-diversification --fund FSELX --bond T-NOTE-2030 --bond-price 99.4
```

Three orders, $15 in fees, and the single most common disqualification becomes
impossible. Run it the day your strategy is set, and again the day before the
deadline.

---

## Commands

| Command | What it does |
| --- | --- |
| `smg rules` | Key dates, limits, classification traps, live countdown |
| `smg explain` | The strategic reasoning, with a demonstration |
| `smg init` | Create a starter `portfolio.json` |
| `smg check` | Full compliance audit; exits non-zero on any violation |
| `smg validate SYM SIDE QTY PRICE` | Pre-trade check for a single order |
| `smg record SYM SIDE QTY PRICE` | Record a fill and re-audit |
| `smg screen` | Rank candidates by contribution to P(top 25) |
| `smg odds --advise` | P(top 25) now, plus the risk posture your standing calls for |
| `smg optimize` | Constrained allocation search maximizing P(top 25) |
| `smg plan` | Executable, rule-checked order list |
| `smg track` | Log daily Percent Return vs the benchmark |
| `smg charts` | Performance charts for Section II of the deck |
| `smg deck` | 20-slide plan mapped to the 100-point evaluation form |

Add `--offline` to any command to skip network calls and work from cache.

---

## What the compliance engine checks

Placing badly is survivable; being disqualified is not, and the guidelines are
explicit that final determination rests solely with the SIFMA Foundation.
`smg check` verifies:

- **$10,000 net cost per asset class**, deadline-aware — a countdown before
  October 23, a violation with a one-business-day cure window afterward
- **The 30% single-security cap**, distinguishing a position *purchased* over
  the cap (a violation) from one that *appreciated* through it (allowed — you
  keep the shares but cannot buy more)
- **The 50% margin limit**, including the trap that a drawdown shrinks your
  equity and your borrowing limit together
- **Prohibited instruments** — futures, options, commodities, currencies, bitcoin
- **Team size and the name-submission gate**
- **Whether you are ranked at all** — a team receives no ranking until its
  initial transaction is entered successfully
- **Fee drag**, in basis points of Percent Return

Classification traps it enforces, straight from the guidelines:

> All ETFs — **including bond ETFs** — are classified as **stocks**.
> All bond mutual funds are classified as **mutual funds**.
> Only **long** stock positions count toward the stock minimum.

So a bond ETF does *not* satisfy the bond requirement, and neither does a bond
mutual fund. Only an actual bond does.

---

## About the strategy tools

`smg screen`, `smg optimize` and `smg odds` are **research and risk tools, not
stock picks.** The guidelines require that "each team must complete their own
research and portfolios must be distinct and reflect the individual team's
investment strategy," and portfolios are reviewed for exactly this.

The built-in universes are starting points for your own analysis. Every screen
output explains *why* a candidate scores as it does, so you can form — and
defend to a judge — your own view. Copying a ranked list without doing that work
is both a weak pitch and a rules problem.

Simulation results depend on assumptions you should set yourself:

```bash
smg odds --teams 1400 --market-vol 0.20 --market-drift 0.05
```

`--teams` matters most. Check your regional ranking page for the real count. If
your conclusion flips between `--teams 400` and `--teams 2000`, you do not have
a conclusion yet.

---

## After the competition

Top 25 qualifies you for ICDC. It does not score you there — a 20-slide pitch
deck and a 15-minute presentation do, against a 100-point rubric.

```bash
smg charts    # the charts Section II explicitly requires
smg deck --members "Name One,Name Two" --school "Lincoln High School"
```

`smg deck` produces a slide-by-slide plan with the required numbering already
correct (cover, title at `2`, table of contents at `3`, body from `4`), a slide
budget allocated in proportion to rubric points, a speaking plan that reserves
time for the judge's questions, and the full penalty-point checklist.

It fills in your real numbers and leaves the analysis to you — Sections III and
IV are worth 30 points for *your* research and *your* reasoning, and a judge who
asks one follow-up question can tell the difference.

**Portfolios are deleted May 10, 2027.** Export everything before then;
`smg track` keeps a local record that survives it.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

65 tests cover the competition calendar, valuation net of borrowed funds, every
compliance rule, the trade planner's rounding behavior, and the economic
invariants of the tournament model.

---

## Disclaimer

Educational software for a DECA competition simulation. It does not provide
investment advice, and the strategy it optimizes for — deliberately maximizing
variance to reach a top-1% cutoff — is appropriate *only* because SMG is a
simulation with a step-function payoff and no real money at stake. It would be
reckless applied to an actual portfolio.

Rules are encoded from the *DECA Guide 2026-27* Stock Market Game guidelines.
The SIFMA Foundation's Program Rules and Code of Conduct govern, and final
determination of disqualification rests solely with them. Verify anything
consequential against [deca.org/smg](https://www.deca.org/smg).
