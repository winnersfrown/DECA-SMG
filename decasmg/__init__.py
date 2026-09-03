"""decasmg -- tools for placing top 25 in the DECA Stock Market Game.

The competition ranks on Percent Return and pays off as a step function: the
top 25 teams in each DECA region qualify for ICDC and everyone else does not.
That makes it a tournament rather than an investing problem, and this package
optimizes accordingly -- for P(top 25), not for expected return.

Modules
-------
rules       every date, limit and fee from the official guidelines
portfolio   holdings, valuation and Percent Return net of borrowed funds
compliance  the disqualification firewall; run it daily
data        market data with cache and offline fallbacks
tournament  Monte Carlo estimation of P(top 25) against a simulated field
screen      candidate ranking by contribution to P(top 25)
optimize    constrained allocation search
planner     executable, rule-checked order lists
tracker     daily performance log and pitch-deck charts
deck        20-slide plan mapped to the 100-point evaluation form
"""

__version__ = "1.0.0"

from .rules import AssetClass, Side  # noqa: F401

__all__ = ["AssetClass", "Side", "__version__"]
