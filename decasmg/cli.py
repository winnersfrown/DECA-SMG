"""Command-line interface: ``smg``.

Daily loop during the competition::

    smg check              # am I about to be disqualified?
    smg track              # log today's Percent Return
    smg odds               # what is my probability of top 25?

Weekly, or whenever the picture changes::

    smg screen             # find high-idiosyncratic-volatility candidates
    smg optimize           # propose an allocation that maximizes P(top 25)
    smg plan               # turn it into an executable order list
    smg record ...         # record each fill so the local file stays in sync

Before the December 4 close, and again before ICDC::

    smg charts             # performance charts for Section II
    smg deck               # the 20-slide plan and penalty checklist
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import __version__
from .rules import (
    AssetClass,
    COMPETITION_END,
    COMPETITION_START,
    DEADLINES,
    DIVERSIFICATION_DEADLINE,
    ICDC_QUALIFYING_RANK,
    MARGIN_BORROW_RATE,
    MAX_POSITION_PCT,
    MIN_NET_COST_PER_ASSET_CLASS,
    RANKING_BENCHMARK,
    STARTING_CASH,
    STUDENT_NAME_DEADLINE,
    TRANSACTION_FEE,
    trading_days_remaining,
)

DEFAULT_PORTFOLIO = "portfolio.json"
DEFAULT_LOG = "performance_log.json"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _load_portfolio(path: str):
    from .portfolio import Portfolio

    p = Path(path)
    if not p.exists():
        sys.exit(
            f"No portfolio at {path!r}. Create one with:  smg init --portfolio {path}"
        )
    return Portfolio.load(p)


def _refresh_prices(pf, offline: bool = False) -> str:
    """Mark the portfolio to the latest available closes."""
    from .data import PROVIDERS, get_many

    syms = sorted({h.symbol for h in pf.holdings})
    if not syms:
        return "no holdings"
    providers = ("cache", "synthetic") if offline else PROVIDERS
    hist = get_many(syms, days=30, providers=providers)
    pf.mark({s: h.last for s, h in hist.items()})
    sources = sorted({h.source for h in hist.values()})
    return ", ".join(sources)


def _factors_for(pf, lookback: int, offline: bool = False):
    """Estimate factor parameters for every held symbol."""
    from .data import PROVIDERS, get_history, get_many
    from .optimize import BOND_PROXY
    from .rules import BENCHMARK_TICKERS
    from .tournament import estimate_factors

    providers = ("cache", "synthetic") if offline else PROVIDERS
    syms = sorted({h.symbol for h in pf.holdings})
    bench = get_history(BENCHMARK_TICKERS[0], days=lookback, providers=providers)
    hist = get_many(syms, days=lookback, providers=providers)
    factors = {}
    for s, h in hist.items():
        if len(h) < 30:
            continue
        try:
            factors.update(estimate_factors({s: h}, bench))
        except ValueError:
            continue
    # Bonds have no meaningful price series here; model them explicitly.
    for h in pf.holdings:
        if h.asset_class is AssetClass.BOND and h.symbol not in factors:
            fp = BOND_PROXY
            factors[h.symbol] = type(fp)(h.symbol, fp.beta, fp.idio_vol,
                                         fp.total_vol, fp.drift)
    return factors


def _current_weights(pf) -> dict[str, float]:
    eq = pf.total_equity
    if eq <= 0:
        return {}
    w: dict[str, float] = {}
    for h in pf.holdings:
        signed = -h.exposure if h.is_short else h.exposure
        w[h.symbol] = w.get(h.symbol, 0.0) + signed / eq
    return w


def _field_from_args(args):
    from .tournament import FieldModel

    return FieldModel(
        n_teams=args.teams,
        market_drift=args.market_drift,
        market_vol=args.market_vol,
    )


def _days(args) -> int:
    if getattr(args, "days", None):
        return int(args.days)
    return max(1, trading_days_remaining())


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_rules(args) -> None:
    now = _dt.datetime.now()
    print("=" * 72)
    print("  DECA STOCK MARKET GAME 2026-27  -  RULES AT A GLANCE")
    print("=" * 72)
    print(f"  Starting cash            ${STARTING_CASH:,.0f}")
    print(f"  Margin                   up to {MARGIN_BORROW_RATE:.0%} of Total Equity "
          f"(max {1 + MARGIN_BORROW_RATE:.0%} gross exposure)")
    print(f"  Single-security cap      {MAX_POSITION_PCT:.0%} of Total Equity "
          f"(20% x 1.5) -- blocks new BUYS, not ownership")
    print(f"  Per-class minimum        ${MIN_NET_COST_PER_ASSET_CLASS:,.0f} net cost "
          f"in EACH of stock / mutual fund / bond")
    print(f"  Transaction fee          ${TRANSACTION_FEE:.0f} flat, per trade")
    print(f"  Pricing                  end of day. Intraday timing is irrelevant.")
    print(f"  Ranking metric           Percent Return vs {RANKING_BENCHMARK}, "
          f"net of borrowed funds")
    print(f"  Qualification            top {ICDC_QUALIFYING_RANK} teams per DECA region")
    print("-" * 72)
    print("  Classification traps:")
    print("    * ALL ETFs -- including bond ETFs -- count as STOCKS.")
    print("    * Bond mutual funds count as MUTUAL FUNDS, not bonds.")
    print("    * Only LONG stock positions count toward the stock minimum.")
    print("    * Futures, options, commodities, currencies and bitcoin are banned.")
    print("-" * 72)
    print("  Deadlines:")
    for d in DEADLINES:
        days = d.days_remaining(now)
        flag = "  <-- DISQUALIFYING" if d.disqualifying else ""
        when = "PASSED" if days < 0 else f"in {days:6.1f} days"
        print(f"    {d.when:%Y-%m-%d %H:%M}  {d.name:<26} {when}{flag}")
    print("-" * 72)
    print(f"  {trading_days_remaining(now)} trading day(s) remain in the competition.")
    print("=" * 72)


def cmd_init(args) -> None:
    from .portfolio import Portfolio

    p = Path(args.portfolio)
    if p.exists() and not args.force:
        sys.exit(f"{p} already exists. Use --force to overwrite.")
    pf = Portfolio(team=args.team, region=args.region, cash=STARTING_CASH)
    pf.save(p)
    print(f"Created {p} for team {args.team!r} with ${STARTING_CASH:,.0f} cash.")
    print("\nNext steps:")
    print("  1. smg rules                 # the constraints you are optimizing against")
    print("  2. smg screen                # find high-idiosyncratic-vol candidates")
    print("  3. smg optimize              # an allocation that maximizes P(top 25)")
    print("  4. smg check                 # never skip this")


def cmd_check(args) -> None:
    from .compliance import audit

    pf = _load_portfolio(args.portfolio)
    if not args.offline:
        src = _refresh_prices(pf, offline=args.offline)
        print(f"(prices: {src})\n")
    members = args.members.split(",") if args.members else None
    report = audit(pf, members=members)
    print(report.render(show_ok=not args.quiet))
    if not report.is_compliant:
        sys.exit(1)


def cmd_validate(args) -> None:
    from .compliance import validate_order

    pf = _load_portfolio(args.portfolio)
    _refresh_prices(pf, offline=args.offline)
    ac = AssetClass(args.asset_class)
    print(f"Pre-trade check: {args.side} {args.quantity} {args.symbol} @ ${args.price}\n")
    bad = False
    for f in validate_order(pf, args.symbol, args.side, args.quantity, args.price, ac):
        print(f)
        if f.severity.value == "VIOLATION":
            bad = True
    sys.exit(1 if bad else 0)


def cmd_screen(args) -> None:
    from .screen import (
        DEFAULT_UNIVERSE,
        UNIVERSE_BOND_NOTE,
        UNIVERSE_ETF,
        UNIVERSE_HIGH_VOL_EQUITY,
        UNIVERSE_MUTUAL_FUND,
        render,
        screen,
    )

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.universe == "equity":
        syms = UNIVERSE_HIGH_VOL_EQUITY
    elif args.universe == "etf":
        syms = UNIVERSE_ETF
    elif args.universe == "funds":
        syms = UNIVERSE_MUTUAL_FUND
    else:
        syms = DEFAULT_UNIVERSE + UNIVERSE_MUTUAL_FUND

    rows = screen(
        symbols=syms,
        days_remaining=_days(args),
        lookback=args.lookback,
        field=_field_from_args(args),
    )
    print(render(rows, top=args.top, show_why=args.why))
    if args.universe in ("all", "funds"):
        print()
        print("  BONDS: " + UNIVERSE_BOND_NOTE)


def cmd_odds(args) -> None:
    from .tournament import portfolio_factors, simulate

    pf = _load_portfolio(args.portfolio)
    src = _refresh_prices(pf, offline=args.offline)
    factors = _factors_for(pf, args.lookback, args.offline)
    weights = _current_weights(pf)
    beta, idio = portfolio_factors(weights, factors)
    days = _days(args)

    print(f"(prices: {src}; {len(factors)} symbol(s) modelled)\n")
    res = simulate(
        my_beta=beta,
        my_idio_vol=idio,
        days=days,
        field=_field_from_args(args),
        my_leverage=1.0,  # weights already carry leverage
        current_return=pf.percent_return,
        n_sims=args.sims,
    )
    print(res.render())

    if args.advise:
        from .tournament import advise_risk

        print()
        adv = advise_risk(
            my_beta=beta,
            current_idio_vol=idio,
            current_leverage=pf.leverage,
            days=days,
            current_return=pf.percent_return,
            field=_field_from_args(args),
        )
        print(adv.render())


def cmd_optimize(args) -> None:
    from .data import PROVIDERS, get_history, get_many
    from .optimize import compare_leverage, optimize
    from .rules import BENCHMARK_TICKERS
    from .screen import DEFAULT_UNIVERSE, UNIVERSE_MUTUAL_FUND, classify
    from .tournament import estimate_factors

    pf = _load_portfolio(args.portfolio) if Path(args.portfolio).exists() else None
    if pf is not None:
        _refresh_prices(pf, offline=args.offline)
        equity = pf.total_equity
        current_return = pf.percent_return
    else:
        equity, current_return = STARTING_CASH, 0.0

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = DEFAULT_UNIVERSE + UNIVERSE_MUTUAL_FUND

    providers = ("cache", "synthetic") if args.offline else PROVIDERS
    bench = get_history(BENCHMARK_TICKERS[0], days=args.lookback, providers=providers)
    hist = get_many(syms, days=args.lookback, providers=providers)
    factors = {}
    for s, h in hist.items():
        if len(h) < 30:
            continue
        try:
            factors.update(estimate_factors({s: h}, bench))
        except ValueError:
            continue
    classes = {s: classify(s) for s in factors}
    days = _days(args)

    alloc = optimize(
        factors, classes, days=days, equity=equity,
        current_return=current_return, field=_field_from_args(args),
        allow_margin=not args.no_margin, n_sims=args.sims,
        max_names=args.max_names,
    )
    print(alloc.render())
    if not alloc.feasible:
        sys.exit(1)

    if args.compare_leverage:
        print()
        print(compare_leverage(factors, classes, days, equity, current_return,
                              _field_from_args(args)))

    if args.save:
        Path(args.save).write_text(
            json.dumps(
                {
                    "weights": alloc.weights,
                    "classes": {k: v.value for k, v in alloc.classes.items()},
                    "dollars": alloc.dollars,
                    "p_qualify": alloc.p_qualify,
                    "generated": _dt.date.today().isoformat(),
                },
                indent=2,
            )
        )
        print(f"\nTarget saved to {args.save} -- feed it to `smg plan --target`.")


def cmd_plan(args) -> None:
    from .data import PROVIDERS, latest_prices
    from .planner import plan_diversification_fix, plan_to_target

    pf = _load_portfolio(args.portfolio)
    _refresh_prices(pf, offline=args.offline)
    providers = ("cache", "synthetic") if args.offline else PROVIDERS

    if args.fix_diversification:
        syms = [s for s in (args.stock, args.fund) if s]
        prices = latest_prices([s.upper() for s in syms], providers=providers) if syms else {}
        prices.update({args.bond.upper(): args.bond_price})
        plan = plan_diversification_fix(
            pf, prices=prices, stock_symbol=args.stock, fund_symbol=args.fund,
            bond_symbol=args.bond, bond_price=args.bond_price,
        )
        print(plan.render())
        sys.exit(0 if plan.post_trade_ok else 1)

    if not args.target:
        sys.exit(
            "Give a target: `smg plan --target target.json` (from `smg optimize "
            "--save`) or `smg plan --fix-diversification`."
        )

    tgt = json.loads(Path(args.target).read_text())
    weights = {k.upper(): float(v) for k, v in tgt["weights"].items()}
    classes = {k.upper(): AssetClass(v) for k, v in tgt.get("classes", {}).items()}

    # `smg optimize` emits a generic "BOND" placeholder because it has no way to
    # know which bond your SMG trade screen offers. Map it onto the real one so
    # the plan tops up your existing bond instead of opening a second position.
    bond = args.bond.upper()
    if bond != "BOND" and "BOND" in weights:
        weights[bond] = weights.pop("BOND")
        classes[bond] = classes.pop("BOND", AssetClass.BOND)

    syms = sorted(set(weights) | {h.symbol for h in pf.holdings})
    prices = latest_prices([s for s in syms if s != args.bond.upper()],
                           providers=providers)
    prices[args.bond.upper()] = args.bond_price
    for h in pf.holdings:
        prices.setdefault(h.symbol, h.price)

    plan = plan_to_target(pf, weights, classes, prices)
    print(plan.render())
    sys.exit(0 if plan.post_trade_ok else 1)


def cmd_record(args) -> None:
    """Record a trade you actually placed in SMG, keeping the local file in sync."""
    from .compliance import Severity, audit, validate_order
    from .portfolio import Transaction

    pf = _load_portfolio(args.portfolio)
    ac = AssetClass(args.asset_class)

    if not args.force:
        problems = [
            f for f in validate_order(pf, args.symbol, args.side, args.quantity,
                                      args.price, ac)
            if f.severity is Severity.VIOLATION
        ]
        if problems:
            for f in problems:
                print(f)
            sys.exit(
                "\nRefusing to record a rule-breaking trade. Use --force if SMG "
                "already filled it and you need the local file to match reality."
            )

    when = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    try:
        pf.apply(
            Transaction(when, args.symbol, args.side, args.quantity, args.price,
                        ac, note=args.note)
        )
    except ValueError as exc:
        sys.exit(str(exc))
    pf.as_of = when
    pf.save(args.portfolio)

    print(f"Recorded: {args.side} {args.quantity:g} {args.symbol.upper()} "
          f"@ ${args.price:,.2f} on {when}")
    print(f"Cash ${pf.cash:,.2f} | Total Equity ${pf.total_equity:,.2f} | "
          f"Percent Return {pf.percent_return:+.2%}\n")

    report = audit(pf, now=_dt.datetime.now())
    for f in report.sorted_findings():
        if f.severity in (Severity.VIOLATION, Severity.WARNING):
            print(f)
    if report.is_compliant and not report.warnings:
        print("Still fully compliant.")


def cmd_track(args) -> None:
    from .tracker import PerformanceLog, benchmark_return_since_start

    pf = _load_portfolio(args.portfolio)
    src = _refresh_prices(pf, offline=args.offline)
    log = PerformanceLog(args.log)
    bench = args.benchmark_return
    if bench is None:
        bench = 0.0 if args.offline else benchmark_return_since_start()
    when = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    snap = log.record(pf, benchmark_return=bench, when=when, note=args.note)
    pf.as_of = when
    pf.save(args.portfolio)
    print(f"(prices: {src})")
    print(f"Logged {snap.date}: equity ${snap.total_equity:,.2f}, "
          f"Percent Return {snap.percent_return:+.2%}, "
          f"excess {snap.excess:+.2%}\n")
    print(log.render())


def cmd_charts(args) -> None:
    from .tracker import PerformanceLog, make_charts

    pf = _load_portfolio(args.portfolio)
    _refresh_prices(pf, offline=args.offline)
    log = PerformanceLog(args.log)
    if not log.snapshots:
        sys.exit(f"No snapshots in {args.log}. Run `smg track` first.")
    files = make_charts(log, pf, outdir=args.outdir)
    print(f"Wrote {len(files)} chart(s) to {args.outdir}/:")
    for f in files:
        print(f"  {f}")
    print("\nThese belong in Section II of the pitch deck, which is worth 15 points")
    print("and explicitly requires charts and diagrams of performance.")


def cmd_deck(args) -> None:
    from .deck import build_deck, write_deck
    from .tracker import PerformanceLog

    pf = _load_portfolio(args.portfolio) if Path(args.portfolio).exists() else None
    if pf is not None:
        _refresh_prices(pf, offline=args.offline)
    log = PerformanceLog(args.log) if Path(args.log).exists() else None
    charts = sorted(str(p) for p in Path(args.charts_dir).glob("*.png")) \
        if Path(args.charts_dir).exists() else []
    plan = build_deck(
        pf=pf, log=log,
        team=args.team or (pf.team if pf else "My Team"),
        school=args.school, address=args.address,
        city_state_zip=args.city_state_zip,
        participants=[m.strip() for m in args.members.split(",")] if args.members else [],
        charts=charts,
    )
    out = write_deck(plan, args.out)
    print(f"Wrote {out} -- {plan.total_slides} of 20 slides planned.")
    if plan.total_slides > 20:
        print("WARNING: over the 20-slide cap. Trim the appendix first.")
    print("\nSpeaking plan (15 minutes total, including the judge's questions):")
    for title, mins in plan.speaking_plan():
        if mins > 0:
            print(f"  {title:<38} {mins:>5.1f} min")
    print(f"  {'Judge questions':<38} {4.0:>5.1f} min")


def cmd_explain(args) -> None:
    """Print the strategic reasoning the toolkit encodes."""
    from .tournament import FieldModel, beta_is_cheap

    days = _days(args)
    print("=" * 72)
    print("  WHY THIS TOOLKIT OPTIMIZES WHAT IT DOES")
    print("=" * 72)
    print(f"""
  Rankings use Percent Return, and the payoff is a step function: rank
  {ICDC_QUALIFYING_RANK} qualifies for ICDC, rank {ICDC_QUALIFYING_RANK + 1} gets nothing. That makes this a
  TOURNAMENT, not an investing problem -- and the two have opposite optima.

  Ordinary investing maximizes risk-adjusted expected return. A tournament
  with a top-1% cutoff maximizes P(return > threshold). A steady +8%
  portfolio has a fine Sharpe ratio and almost no chance of placing, because
  a hundred teams in your region will clear +8% by luck alone.

  Four consequences the tools act on:

  1. VARIANCE IS THE POINT. Insufficient volatility, not excess volatility,
     is what keeps teams out of the top {ICDC_QUALIFYING_RANK}. `smg odds --advise` will often
     recommend more risk than feels comfortable. That is the correct answer
     for this objective, and the same tool tells you to cut risk once you
     are safely above the bar.

  2. BETA IS NEARLY WORTHLESS; IDIOSYNCRATIC RISK IS NOT. If the market
     rallies, you gain -- but so does every other team, and the qualifying
     bar rises with you. Only the uncorrelated part of your return moves you
     up the ranking. This is why the screener ranks by idiosyncratic
     volatility rather than by total volatility or by quality.

  3. THE RULES SET A FLOOR ON DIVERSIFICATION. A {MAX_POSITION_PCT:.0%} per-name cap against
     {1 + MARGIN_BORROW_RATE:.0%} gross exposure makes fewer than 5 positions impossible. And
     ${MIN_NET_COST_PER_ASSET_CLASS:,.0f} each in a mutual fund and a bond is forced capital -- so hold
     the bond at exactly the minimum, and put the fund slug somewhere that
     actually moves.

  4. INTRADAY TIMING IS WORTH NOTHING. Every order fills at a closing
     price. There is no advantage to watching a ticker all day, and the
     flat ${TRANSACTION_FEE:.0f} fee punishes churn: it is 0.03% on a $15,000 order and
     1.0% on a $500 one. Trade rarely, in size.

  The honest caveat: none of this predicts returns. It shapes the
  DISTRIBUTION of your outcomes toward the tail that qualifies. Raising
  P(top {ICDC_QUALIFYING_RANK}) from 3% to 30% is a real edge and still loses most of the time.
""")
    print("-" * 72)
    print(beta_is_cheap(days, FieldModel(n_teams=args.teams)))
    print("=" * 72)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smg",
        description="Tools for placing top 25 in the DECA Stock Market Game.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"decasmg {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, portfolio=True, offline=True):
        if portfolio:
            sp.add_argument("--portfolio", default=DEFAULT_PORTFOLIO,
                            help="portfolio JSON file (default: %(default)s)")
        if offline:
            sp.add_argument("--offline", action="store_true",
                            help="skip network fetches; use cache or synthetic data")

    def sim_opts(sp):
        sp.add_argument("--days", type=int, default=None,
                        help="trading days remaining (default: computed from today)")
        sp.add_argument("--teams", type=int, default=900,
                        help="teams in your DECA region (default: %(default)s). "
                             "Check your regional ranking page and set this -- "
                             "results are sensitive to it.")
        sp.add_argument("--market-drift", type=float, default=0.08,
                        help="annualized market drift assumption (default: %(default)s)")
        sp.add_argument("--market-vol", type=float, default=0.16,
                        help="annualized market volatility (default: %(default)s)")
        sp.add_argument("--sims", type=int, default=20000,
                        help="Monte Carlo paths (default: %(default)s)")
        sp.add_argument("--lookback", type=int, default=252,
                        help="trading days of history for factor estimation")

    sp = sub.add_parser("rules", help="key dates, limits and classification traps")
    sp.set_defaults(func=cmd_rules)

    sp = sub.add_parser("init", help="create a starter portfolio file")
    common(sp, offline=False)
    sp.add_argument("--team", default="My Team")
    sp.add_argument("--region", default="")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("check", help="full compliance audit (run this daily)")
    common(sp)
    sp.add_argument("--members", default=None, help="comma-separated team members")
    sp.add_argument("--quiet", action="store_true", help="hide passing checks")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("validate", help="pre-trade check for a single order")
    common(sp)
    sp.add_argument("symbol")
    sp.add_argument("side", choices=["buy", "sell", "short", "cover"])
    sp.add_argument("quantity", type=float)
    sp.add_argument("price", type=float)
    sp.add_argument("--asset-class", default="stock",
                    choices=[a.value for a in AssetClass])
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("screen", help="rank candidates by contribution to P(top 25)")
    common(sp, portfolio=False)
    sim_opts(sp)
    sp.add_argument("--symbols", default=None, help="comma-separated tickers")
    sp.add_argument("--universe", default="all",
                    choices=["all", "equity", "etf", "funds"])
    sp.add_argument("--top", type=int, default=25)
    sp.add_argument("--why", action="store_true", help="explain each score")
    sp.set_defaults(func=cmd_screen)

    sp = sub.add_parser("odds", help="P(top 25) for your current portfolio")
    common(sp)
    sim_opts(sp)
    sp.add_argument("--advise", action="store_true",
                    help="also recommend a risk posture")
    sp.set_defaults(func=cmd_odds)

    sp = sub.add_parser("optimize", help="propose an allocation maximizing P(top 25)")
    common(sp)
    sim_opts(sp)
    sp.add_argument("--symbols", default=None, help="candidate tickers")
    sp.add_argument("--max-names", type=int, default=8)
    sp.add_argument("--no-margin", action="store_true", help="cap gross at 100%%")
    sp.add_argument("--compare-leverage", action="store_true")
    sp.add_argument("--save", default=None, help="write the target to JSON")
    sp.set_defaults(func=cmd_optimize)

    sp = sub.add_parser("plan", help="build an executable, rule-checked order list")
    common(sp)
    sp.add_argument("--target", default=None, help="target JSON from `smg optimize --save`")
    sp.add_argument("--fix-diversification", action="store_true",
                    help="emit only the trades that satisfy the $10,000 minimums")
    sp.add_argument("--stock", default=None, help="symbol to fill the stock minimum")
    sp.add_argument("--fund", default=None, help="symbol to fill the mutual fund minimum")
    sp.add_argument("--bond", default="BOND", help="bond identifier (default: %(default)s)")
    sp.add_argument("--bond-price", type=float, default=100.0)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("record", help="record a trade you placed in SMG")
    common(sp, offline=False)
    sp.add_argument("symbol")
    sp.add_argument("side", choices=["buy", "sell", "short", "cover"])
    sp.add_argument("quantity", type=float)
    sp.add_argument("price", type=float)
    sp.add_argument("--asset-class", default="stock",
                    choices=[a.value for a in AssetClass])
    sp.add_argument("--date", default=None, help="fill date, YYYY-MM-DD")
    sp.add_argument("--note", default="")
    sp.add_argument("--force", action="store_true",
                    help="record even if it breaks a rule (to match a real fill)")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("track", help="log today's Percent Return")
    common(sp)
    sp.add_argument("--log", default=DEFAULT_LOG)
    sp.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    sp.add_argument("--benchmark-return", type=float, default=None)
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_track)

    sp = sub.add_parser("charts", help="render performance charts for the deck")
    common(sp)
    sp.add_argument("--log", default=DEFAULT_LOG)
    sp.add_argument("--outdir", default="charts")
    sp.set_defaults(func=cmd_charts)

    sp = sub.add_parser("deck", help="generate the 20-slide pitch deck plan")
    common(sp)
    sp.add_argument("--log", default=DEFAULT_LOG)
    sp.add_argument("--charts-dir", default="charts")
    sp.add_argument("--out", default="pitch_deck_plan.md")
    sp.add_argument("--team", default=None)
    sp.add_argument("--school", default="[School name]")
    sp.add_argument("--address", default="[School address]")
    sp.add_argument("--city-state-zip", default="[City, State/Province, ZIP]")
    sp.add_argument("--members", default=None, help="comma-separated participants")
    sp.set_defaults(func=cmd_deck)

    sp = sub.add_parser("explain", help="the strategy reasoning, with a demonstration")
    sim_opts(sp)
    sp.set_defaults(func=cmd_explain)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
