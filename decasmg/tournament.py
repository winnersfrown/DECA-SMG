"""Tournament mathematics: estimating and maximizing P(top 25 in region).

WHY THIS MODULE EXISTS
----------------------
SMG ranks on Percent Return and pays off as a step function: rank 25 qualifies
for ICDC, rank 26 gets nothing. That makes this a *tournament*, not an
investment problem, and the two have opposite optima.

Ordinary investing maximizes risk-adjusted expected return. A tournament with a
top-1% cutoff maximizes P(return > threshold). Those diverge sharply: a steady
+8% portfolio has an excellent Sharpe ratio and a near-zero chance of placing
top 25, because roughly a hundred teams in a region will clear +8% by luck
alone. Variance is not the enemy here -- insufficient variance is.

The second, less obvious point this module exists to capture: the qualifying
threshold is *itself* random and correlated with your portfolio. If the market
rallies hard, you do well -- but so does everyone, and the bar rises with you.
So the simulation runs your portfolio and the field against a *shared* market
path. The consequence falls out of the math: market beta is nearly worthless
for winning a tournament, because it lifts you and the bar together.
Idiosyncratic, uncorrelated return is what moves you up the ranking.

Requires numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "tournament simulation requires numpy -- `pip install numpy`"
    ) from exc

from .data import PriceHistory, align
from .rules import ICDC_QUALIFYING_RANK, MARGIN_BORROW_RATE, MAX_POSITION_PCT

TRADING_DAYS_YEAR = 252


# --------------------------------------------------------------------------
# Factor estimation
# --------------------------------------------------------------------------


@dataclass
class FactorParams:
    """A security reduced to what the tournament simulation needs.

    ``beta`` is exposure to the shared market factor -- the part of your return
    that moves the qualifying bar along with you. ``idio_vol`` is the part that
    does not, and is therefore the part that actually wins tournaments.
    """

    symbol: str
    beta: float
    idio_vol: float       # annualized
    total_vol: float      # annualized
    drift: float          # annualized, historical
    n_obs: int = 0

    @property
    def idio_share(self) -> float:
        """Fraction of variance that is idiosyncratic. Higher is better here."""
        if self.total_vol <= 0:
            return 0.0
        return round(min(1.0, (self.idio_vol / self.total_vol) ** 2), 4)


def estimate_factors(
    histories: dict[str, PriceHistory], benchmark: PriceHistory
) -> dict[str, FactorParams]:
    """Regress each symbol's daily returns on the benchmark to split beta from idio.

    Uses only dates every series shares, because a beta computed across
    misaligned calendars is noise.
    """
    merged = dict(histories)
    merged["__BENCH__"] = benchmark
    _, aligned = align(merged)
    bench = np.asarray(aligned.pop("__BENCH__"), dtype=float)
    if len(bench) < 20:
        raise ValueError("need at least 20 overlapping observations to estimate factors")

    br = np.diff(bench) / bench[:-1]
    bvar = float(np.var(br, ddof=1))

    out: dict[str, FactorParams] = {}
    for sym, closes in aligned.items():
        px = np.asarray(closes, dtype=float)
        r = np.diff(px) / px[:-1]
        total_vol = float(np.std(r, ddof=1)) * math.sqrt(TRADING_DAYS_YEAR)
        if bvar > 0:
            beta = float(np.cov(r, br, ddof=1)[0, 1] / bvar)
        else:
            beta = 0.0
        resid = r - beta * br
        idio_vol = float(np.std(resid, ddof=1)) * math.sqrt(TRADING_DAYS_YEAR)
        drift = float(np.mean(r)) * TRADING_DAYS_YEAR
        out[sym] = FactorParams(sym, round(beta, 4), round(idio_vol, 4),
                                round(total_vol, 4), round(drift, 4), len(r))
    return out


# --------------------------------------------------------------------------
# The field
# --------------------------------------------------------------------------


@dataclass
class FieldModel:
    """A model of the other teams in your DECA region.

    Defaults describe a typical regional field: several hundred to a couple of
    thousand teams, most running long, moderately concentrated, lightly levered
    equity portfolios. The heavy right tail comes from the minority who
    concentrate hard and use full margin -- those are the teams you are
    actually racing for the last qualifying slots.

    Every parameter is exposed because the honest answer is that you cannot
    know your region's field exactly. Run the sensitivity: if your conclusion
    flips between ``n_teams=400`` and ``n_teams=2000``, you do not have a
    conclusion yet.
    """

    n_teams: int = 900
    """Teams in the region. Check your regional ranking page for the real count."""

    market_drift: float = 0.08
    """Annualized expected market drift. Deliberately modest."""

    market_vol: float = 0.16
    """Annualized market volatility."""

    mean_beta: float = 1.05
    beta_sd: float = 0.35
    """Field beta: most teams hold ordinary large-cap equity."""

    mean_idio_vol: float = 0.30
    idio_vol_sd: float = 0.16
    """Field idiosyncratic vol -- the spread that creates the tail."""

    mean_leverage: float = 1.10
    leverage_sd: float = 0.28
    """Most teams under-use margin; a minority run the full 1.5x."""

    inactive_fraction: float = 0.12
    """Teams that never place a meaningful trade. They cannot rank, but they
    also cannot take a qualifying slot -- so they thin the real field."""

    def sample_teams(self, rng: "np.random.Generator") -> tuple:
        """Draw a panel of team parameters: (beta, idio_vol, leverage)."""
        n = self.n_teams
        beta = rng.normal(self.mean_beta, self.beta_sd, n)
        idio = np.abs(rng.normal(self.mean_idio_vol, self.idio_vol_sd, n))
        lev = np.clip(
            rng.normal(self.mean_leverage, self.leverage_sd, n),
            0.0,
            1.0 + MARGIN_BORROW_RATE,
        )
        # Inactive teams sit in cash and post ~0%.
        inactive = rng.random(n) < self.inactive_fraction
        beta[inactive] = 0.0
        idio[inactive] = 0.0
        lev[inactive] = 0.0
        return beta, idio, lev


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


@dataclass
class FieldSample:
    """A frozen draw of simulated regional outcomes.

    Simulating the field is by far the expensive part (an ``n_sims x n_teams``
    array). Because the field does not depend on *your* portfolio, it is drawn
    once and reused for every candidate allocation. That turns portfolio search
    from minutes into milliseconds, and -- because every candidate is scored
    against the *same* simulated regions -- comparisons use common random
    numbers, so small genuine differences are not drowned in Monte Carlo noise.
    """

    market_log: "np.ndarray"   # (n_sims,) shared market log return over horizon
    thresholds: "np.ndarray"   # (n_sims,) k-th best team return in each region
    days: int
    n_sims: int
    field: FieldModel

    @property
    def median_threshold(self) -> float:
        return float(np.median(self.thresholds))


def sample_field(
    days: int,
    field: FieldModel | None = None,
    n_sims: int = 20_000,
    seed: int = 12345,
    chunk: int = 2_000,
) -> FieldSample:
    """Draw the shared market path and the resulting qualifying threshold.

    Chunked so peak memory stays bounded no matter how large ``n_sims`` is.
    """
    field = field or FieldModel()
    rng = np.random.default_rng(seed)
    T = max(1, int(days))
    dt = T / TRADING_DAYS_YEAR

    beta_f, idio_f, lev_f = field.sample_teams(rng)
    k = min(ICDC_QUALIFYING_RANK, field.n_teams)

    mu_m = field.market_drift * dt - 0.5 * field.market_vol**2 * dt
    sd_m = field.market_vol * math.sqrt(dt)

    market = np.empty(n_sims)
    thresholds = np.empty(n_sims)

    done = 0
    while done < n_sims:
        m = min(chunk, n_sims - done)
        Rm = rng.normal(mu_m, sd_m, size=m)

        # Every team is driven by the same market path, so a rally lifts the
        # qualifying bar right along with your own portfolio.
        eps = rng.standard_normal((m, field.n_teams)) * (idio_f * math.sqrt(dt))
        team_ret = np.expm1(lev_f * (beta_f * Rm[:, None] + eps))
        thresholds[done : done + m] = -np.partition(-team_ret, k - 1, axis=1)[:, k - 1]
        market[done : done + m] = Rm
        done += m

    return FieldSample(market, thresholds, T, n_sims, field)


def my_returns(
    fs: FieldSample,
    my_beta: float,
    my_idio_vol: float,
    my_leverage: float = 1.0,
    my_alpha: float = 0.0,
    current_return: float = 0.0,
    seed: int = 999,
) -> "np.ndarray":
    """Your final Percent Return in each simulated region."""
    dt = fs.days / TRADING_DAYS_YEAR
    rng = np.random.default_rng(seed)
    eps = rng.standard_normal(fs.n_sims) * (my_idio_vol * math.sqrt(dt))
    log = my_leverage * (my_beta * fs.market_log + eps) + my_alpha * dt
    return (1.0 + current_return) * (1.0 + np.expm1(log)) - 1.0


def p_qualify(
    fs: FieldSample,
    my_beta: float,
    my_idio_vol: float,
    my_leverage: float = 1.0,
    my_alpha: float = 0.0,
    current_return: float = 0.0,
    seed: int = 999,
) -> float:
    """P(your return beats the 25th-best team) against a pre-drawn field."""
    final = my_returns(fs, my_beta, my_idio_vol, my_leverage, my_alpha,
                       current_return, seed)
    return float(np.mean(final > fs.thresholds))


@dataclass
class QualifyResult:
    """Output of a tournament simulation."""

    p_qualify: float
    median_threshold: float
    threshold_p10: float
    threshold_p90: float
    median_my_return: float
    my_return_p10: float
    my_return_p90: float
    required_return: float
    days: int
    n_sims: int
    my_beta: float = 0.0
    my_idio_vol: float = 0.0
    my_leverage: float = 1.0

    def render(self) -> str:
        lines = [
            "=" * 72,
            f"  P(TOP {ICDC_QUALIFYING_RANK} IN REGION) = {self.p_qualify:6.1%}",
            "=" * 72,
            f"  Horizon                 {self.days} trading days, {self.n_sims:,} simulations",
            f"  Your portfolio          beta {self.my_beta:.2f} | idio vol "
            f"{self.my_idio_vol:.0%} | leverage {self.my_leverage:.2f}x",
            "",
            f"  Qualifying threshold    {self.median_threshold:+7.1%} median "
            f"(10th-90th pct: {self.threshold_p10:+.1%} to {self.threshold_p90:+.1%})",
            f"  Your projected return   {self.median_my_return:+7.1%} median "
            f"(10th-90th pct: {self.my_return_p10:+.1%} to {self.my_return_p90:+.1%})",
            f"  Still needed (median)   {self.required_return:+7.1%} from here",
            "=" * 72,
        ]
        return "\n".join(lines)


def simulate(
    my_beta: float,
    my_idio_vol: float,
    days: int,
    field: FieldModel | None = None,
    my_leverage: float = 1.0,
    my_alpha: float = 0.0,
    current_return: float = 0.0,
    n_sims: int = 20_000,
    seed: int = 12345,
    chunk: int = 2_000,
    field_sample: FieldSample | None = None,
) -> QualifyResult:
    """Estimate P(top 25) by simulating you and the field on a shared market.

    Parameters
    ----------
    my_beta, my_idio_vol
        Your portfolio's factor exposure and annualized idiosyncratic vol.
    days
        Trading days remaining in the competition.
    my_leverage
        Gross exposure / equity. 1.0 unlevered, 1.5 fully margined.
    my_alpha
        Annualized excess return you believe you can generate. Keep this at 0
        unless you have a real reason -- the honest default is that you cannot
        predict returns, and the tool should not flatter you.
    current_return
        Percent Return already banked. Compounds with the simulated remainder.
    field_sample
        Reuse a previously drawn field instead of drawing a new one.
    """
    fs = field_sample or sample_field(days, field, n_sims, seed, chunk)
    final = my_returns(fs, my_beta, my_idio_vol, my_leverage, my_alpha, current_return)
    med_thresh = float(np.median(fs.thresholds))

    return QualifyResult(
        p_qualify=round(float(np.mean(final > fs.thresholds)), 4),
        median_threshold=round(med_thresh, 4),
        threshold_p10=round(float(np.percentile(fs.thresholds, 10)), 4),
        threshold_p90=round(float(np.percentile(fs.thresholds, 90)), 4),
        median_my_return=round(float(np.median(final)), 4),
        my_return_p10=round(float(np.percentile(final, 10)), 4),
        my_return_p90=round(float(np.percentile(final, 90)), 4),
        required_return=round((1.0 + med_thresh) / (1.0 + current_return) - 1.0, 4),
        days=fs.days,
        n_sims=fs.n_sims,
        my_beta=round(my_beta, 3),
        my_idio_vol=round(my_idio_vol, 3),
        my_leverage=round(my_leverage, 3),
    )


def portfolio_factors(
    weights: dict[str, float], factors: dict[str, FactorParams]
) -> tuple[float, float]:
    """Aggregate per-security factors into portfolio (beta, idio_vol).

    Weights are signed fractions of equity -- negative for a short. Idiosyncratic
    risks are treated as independent across securities, so they diversify away
    as sqrt(sum of squares). That is exactly why *concentration* raises idio vol
    and therefore raises P(top 25): four names keep half the idiosyncratic
    volatility that sixteen names would wash out.
    """
    beta = sum(w * factors[s].beta for s, w in weights.items() if s in factors)
    idio_var = sum(
        (w * factors[s].idio_vol) ** 2 for s, w in weights.items() if s in factors
    )
    return round(beta, 4), round(math.sqrt(max(0.0, idio_var)), 4)


# --------------------------------------------------------------------------
# Risk posture
# --------------------------------------------------------------------------


@dataclass
class RiskAdvice:
    """How much risk your current standing calls for."""

    posture: str
    target_idio_vol: float
    target_leverage: float
    rationale: str
    p_now: float
    p_at_target: float
    scan: list[tuple[float, float, float]] = field(default_factory=list)
    """(idio_vol, leverage, p_qualify) grid actually evaluated."""

    def render(self) -> str:
        lines = [
            "=" * 72,
            f"  RISK POSTURE: {self.posture}",
            "=" * 72,
            f"  {self.rationale}",
            "",
            f"  Current setting     P(top 25) = {self.p_now:.1%}",
            f"  Suggested setting   idio vol {self.target_idio_vol:.0%}, "
            f"leverage {self.target_leverage:.2f}x  ->  "
            f"P(top 25) = {self.p_at_target:.1%}",
        ]
        if self.scan:
            lines += ["", "  Sensitivity (annualized idio vol x leverage -> P):"]
            levs = sorted({round(l, 2) for _, l, _ in self.scan})
            header = "    idio\\lev  " + "".join(f"{l:>8.2f}x" for l in levs)
            lines.append(header)
            for v in sorted({round(v, 2) for v, _, _ in self.scan}):
                row = f"    {v:>6.0%}   "
                for l in levs:
                    hit = [p for vv, ll, p in self.scan
                           if abs(vv - v) < 1e-6 and abs(ll - l) < 1e-6]
                    row += f"{hit[0]:>8.1%}" if hit else " " * 9
                lines.append(row)
        lines.append("=" * 72)
        return "\n".join(lines)


def advise_risk(
    my_beta: float,
    current_idio_vol: float,
    current_leverage: float,
    days: int,
    current_return: float = 0.0,
    field: FieldModel | None = None,
    n_sims: int = 8_000,
    seed: int = 777,
) -> RiskAdvice:
    """Grid-search risk settings and report the one that maximizes P(top 25).

    This is the module's punchline. It routinely recommends *more* variance than
    feels comfortable, and that is the correct answer for a top-1% cutoff, not a
    bug. It will also recommend cutting risk -- late in the competition, well
    above the projected bar, protecting a qualifying position beats reaching for
    more, and the grid says so on its own.
    """
    field = field or FieldModel()
    vols = [0.15, 0.25, 0.35, 0.45, 0.60, 0.80, 1.00]
    levs = [1.0, 1.25, 1.5]

    # One field draw scores every candidate, so the grid is compared under
    # common random numbers rather than independent noise.
    fs = sample_field(days, field, n_sims=n_sims, seed=seed)

    scan = [
        (v, l, round(p_qualify(fs, my_beta, v, l, current_return=current_return), 4))
        for v in vols
        for l in levs
    ]
    p_now = round(
        p_qualify(fs, my_beta, current_idio_vol, current_leverage,
                  current_return=current_return),
        4,
    )

    best_v, best_l, best_p = max(scan, key=lambda t: t[2])

    median_bar = fs.median_threshold
    ahead = current_return > median_bar
    min_vol, max_vol = min(vols), max(vols)
    # Let the grid label itself: the posture is whichever end of the risk
    # axis the optimizer actually chose, not an arbitrary calendar cutoff.
    defending = best_v <= min_vol + 1e-9
    maxing = best_v >= max_vol - 1e-9

    gap = f"You are at {current_return:+.1%} against a {median_bar:+.1%} median bar with {days} trading day(s) left."

    if defending:
        posture = "DEFEND"
        rationale = (
            f"{gap} You are already clear of the bar, and the simulation says "
            f"every increase in risk from here *lowers* P(top 25) -- more "
            f"variance can only knock you back below a line you have already "
            f"crossed. Cut leverage and concentration and bank the qualification."
        )
    elif maxing and not ahead and days <= 15:
        posture = "ALL-IN"
        rationale = (
            f"{gap} There is not enough time left for a moderate portfolio to "
            f"close that gap. Concentration and leverage are the only levers "
            f"with any chance -- and a worse loss costs you nothing, because "
            f"rank 400 and rank 900 pay exactly the same."
        )
    elif maxing:
        posture = "BUILD VARIANCE"
        rationale = (
            f"{gap} A diversified low-volatility portfolio has almost no path "
            f"to the top {ICDC_QUALIFYING_RANK}; concentrated idiosyncratic "
            f"risk does. The grid below is still rising at its top edge, which "
            f"means you are under-risked for this objective."
        )
    elif ahead:
        posture = "PRESS CAUTIOUSLY"
        rationale = (
            f"{gap} You are ahead, but with enough time left for the field to "
            f"run you down. The optimum sits in the middle of the grid: hold "
            f"meaningful variance, but stop short of a position one bad week "
            f"can erase."
        )
    else:
        posture = "ADD RISK, SELECTIVELY"
        rationale = (
            f"{gap} You need more variance than you are carrying, but the grid "
            f"turns over before its top edge -- pushing past the suggested "
            f"setting starts costing you probability rather than buying it."
        )

    return RiskAdvice(
        posture=posture,
        target_idio_vol=best_v,
        target_leverage=best_l,
        rationale=rationale,
        p_now=p_now,
        p_at_target=round(best_p, 4),
        scan=scan,
    )


def beta_is_cheap(days: int, field: FieldModel | None = None, n_sims: int = 8000) -> str:
    """Demonstrate that market beta barely moves P(top 25), while idio vol does.

    Included because it is the single most counter-intuitive result in the game
    and the one most worth putting on a pitch-deck slide.
    """
    field = field or FieldModel()
    fs = sample_field(days, field, n_sims=n_sims)
    lines = ["Holding total volatility roughly fixed, shifting risk from market",
             "beta into idiosyncratic risk raises P(top 25):", ""]
    lines.append(f"  {'beta':>6} {'idio vol':>10} {'P(top 25)':>12}")
    for beta, idio in ((1.6, 0.10), (1.2, 0.25), (0.8, 0.38), (0.4, 0.48), (0.0, 0.55)):
        p = p_qualify(fs, my_beta=beta, my_idio_vol=idio, my_leverage=1.5)
        lines.append(f"  {beta:>6.1f} {idio:>10.0%} {p:>12.1%}")
    lines += ["", "Beta lifts you and the qualifying bar together. Only the",
              "idiosyncratic part moves you *relative* to the field."]
    return "\n".join(lines)
