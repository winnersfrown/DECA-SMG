# Strategy notes

The reasoning the toolkit encodes, with the math made explicit. Read this before
trusting any number the tools print — and before defending your strategy to a
judge, since Section III of the pitch deck is worth 15 points for exactly this
kind of reasoning.

---

## 1. This is a tournament, not an investing problem

Rankings use Percent Return. The top 25 teams per region qualify for ICDC.
Rank 26 receives nothing.

That payoff structure changes the objective completely. Write $R$ for your
return and $T$ for the return of the 25th-place team:

- An investor maximizes $\mathbb{E}[R]$ subject to a variance penalty.
- You maximize $\Pr(R > T)$.

These recommend opposite portfolios. Suppose the qualifying bar lands near +45%
over the twelve-week competition. A portfolio with an expected return of +8% and
a 15% standard deviation needs a **2.5-sigma** move to qualify — roughly a 0.6%
chance. Triple its volatility to 45% and the same +8% expected return needs only
0.8 sigma — roughly a 21% chance. Expected return did not change. The
probability of qualifying rose 35-fold.

This is why `smg odds --advise` frequently recommends more risk than feels
comfortable. It is not a bug and it is not recklessness — it is the correct
answer to the question the competition actually asks.

**And it runs both ways.** Once you are clearly above the projected bar with
little time left, additional variance can only knock you back below a line you
have already crossed. The same grid search then recommends cutting risk, and
labels the posture `DEFEND`. The tool is not biased toward aggression; it is
biased toward whatever maximizes $\Pr(R > T)$.

---

## 2. The qualifying bar is random, and correlated with you

This is the part most people miss.

$T$ is not a fixed number. It is the 25th-order statistic of a few hundred to a
few thousand other teams' returns, and those teams hold broadly the same market
you do. If the market rallies 15%, you do well — and so does the whole field, so
$T$ rises too.

The consequence is sharp: **market beta barely helps you win.**

Decompose your return into a market component and an idiosyncratic one:

$$R = \beta R_m + \epsilon$$

The $\beta R_m$ term moves you *and* the bar together. Only $\epsilon$ moves you
*relative* to the field, and relative position is the only thing rankings
measure.

This is why `decasmg/tournament.py` simulates your portfolio and the field
against a **shared** market path rather than independently. Independent
simulation would badly overstate the value of leverage and beta. You can see the
effect directly:

```bash
smg explain
```

At equal total volatility, shifting exposure from beta into idiosyncratic risk
raises P(top 25) several-fold. Practically: three uncorrelated high-volatility
names beat one leveraged index fund of the same total volatility, every time.

---

## 3. What the rules actually constrain

| Rule | Number | What it really means |
| --- | --- | --- |
| Starting cash | $100,000 | — |
| Margin | 50% of Total Equity | Max 150% gross exposure |
| Single-security cap | 20% × 1.5 = **30%** | Blocks new **buys**, not ownership |
| Per-class minimum | $10,000 net cost × 3 | $20,000 of forced non-core capital |
| Transaction fee | $5 flat | 0.03% on $15k, 1.0% on $500 |
| Pricing | End of day | Intraday timing is worth nothing |
| Ranking | Percent Return, net of borrowed funds | Leverage does not flatter the metric |

Four things fall out of these numbers:

**A floor on diversification.** 150% gross exposure against a 30% per-name cap
requires at least $\lceil 1.5 / 0.3 \rceil = 5$ positions. You cannot build a
three-stock portfolio at full margin — the rules forbid it.

**The cap is asymmetric, and that is exploitable.** It blocks *purchases*, not
*ownership*. A position that appreciates through 30% is retained; you simply
cannot add to it. So a winner is allowed to run. `smg check` distinguishes these
two cases and will not flag an appreciated position as a violation.

**$20,000 of your capital is conscripted.** The mutual fund and bond minimums are
mandatory. The bond slug is pure drag on Percent Return — hold *exactly* the
minimum, never a dollar more, which is what `smg optimize` enforces. The mutual
fund slug is different: since that capital is forced anyway, an aggressive growth
or sector fund puts it to work, while a money-market or short-bond fund
guarantees a drag. Note the trap — **a bond mutual fund counts as a mutual fund,
not as a bond**, so it satisfies the wrong requirement and lowers your return.

**Leverage is worth using here, for a reason that would be irresponsible
elsewhere.** Margin multiplies both tails. In real investing the downside tail is
what ruins you. In a step-function tournament, finishing 400th and finishing
900th are the *same outcome* — so the downside margin adds costs you nothing you
had. That reasoning depends entirely on this being a simulation with no real
money. It does not transfer.

---

## 4. Modelling the field

`FieldModel` describes your region: how many teams, and how their betas,
idiosyncratic volatilities and leverage are distributed. Defaults describe a
typical field — most teams long, moderately concentrated, lightly levered, with
a heavy right tail from the minority who concentrate hard and use full margin.
Those are the teams you are actually racing for the last qualifying slots.

**The single most important parameter is `--teams`.** The bar is the 25th-order
statistic, so it depends directly on how many draws there are. Twenty-five out of
400 is the 94th percentile; 25 out of 2,000 is the 98.75th. Those are very
different bars.

```bash
smg odds --teams 400
smg odds --teams 2000
```

Find your real count on the regional ranking page and set it. If your conclusion
flips between those two numbers, you do not have a conclusion yet — you have an
artifact of an assumption.

Be equally skeptical of the rest. The model assumes lognormal returns and
independent idiosyncratic shocks. Real markets have fat tails, and real
portfolios cluster in the same crowded names. Treat every probability it prints
as an order of magnitude, not a measurement.

---

## 5. The timeline

| Phase | Days | What matters |
| --- | --- | --- |
| **Sep 8 – Oct 16** | ~28 | Build the position. Confirm every student name — the eligibility gate is absolute. |
| **Oct 16 – Oct 23** | ~5 | Satisfy all three class minimums *before* the deadline, not on it. |
| **Oct 23 – Nov 20** | ~20 | The main run. Re-check `smg odds --advise` weekly as the horizon shortens. |
| **Nov 20 – Dec 4** | ~10 | Endgame. Either defend a qualifying position or take the last real swing. |

The endgame is where the posture logic earns its keep. With ten days left, the
distribution of remaining outcomes is narrow — there is simply not enough time
for a moderate portfolio to close a 30-point gap. If you are behind, the only
strategies with any chance are the ones that would be irresponsible in real
investing, and they cost you nothing because rank 400 and rank 900 pay the same.
If you are ahead, the reverse holds and variance becomes pure downside.

`smg odds --advise` recomputes this every time you run it, and the recommended
posture flips on its own as the days run down.

---

## 6. Things that do not work

Worth knowing so you don't waste the competition on them.

- **Day trading.** Every order fills at a closing price. Intraday movement is
  invisible to your account, and each round trip costs $10 in fees.
- **Chasing the daily leaderboard.** Early rankings are dominated by teams making
  one enormous concentrated bet. Most of them will be gone by December.
- **Holding cash to "wait for a dip."** Cash earns nothing here and the clock is
  63 trading days. Time out of the market is variance you did not take.
- **Over-diversifying.** Twenty positions wash out the idiosyncratic volatility
  that is the entire source of your edge, and cost $100 in fees to build. The
  rules already force a five-position floor at full margin; going far beyond it
  works directly against you.
- **Bond ETFs to satisfy the bond requirement.** They count as stocks. This is
  the most common disqualification in the game, and `smg check` is specifically
  built to catch it.

---

## 7. Turning this into pitch-deck points

Section III (Rationale, 15 points) and Section IV (Conclusions, 15 points, plus
Strategic Reflection, 15 more) reward exactly this kind of reasoning. A judge
sees many teams say "we picked good companies and diversified." A team that can
explain *why a top-25 cutoff makes variance an asset*, *why market beta cannot
improve a relative ranking*, and *what the $10,000 class minimums cost them in
expected return* is demonstrating the informed decision-making the rubric asks
for by name.

Two things make that credible rather than recited:

**Own the losses.** Section IV asks you to evaluate effectiveness, not to claim
success. Explain your largest drawdown and what caused it. Separate skill from
luck honestly — with 63 trading days and a concentrated portfolio, luck did a
great deal of the work regardless of where you finished, and saying so
demonstrates more sophistication than any return number.

**Do your own research.** The rules require that portfolios be distinct and
reflect your own strategy, and portfolios are reviewed. Use the screener to
generate candidates, then form your own view on each one. A judge only needs one
follow-up question to find out whether you did.
