"""Market data with graceful degradation.

Providers are tried in order and the first that answers wins:

  1. ``yfinance``  -- best coverage (stocks, ETFs, mutual funds), if installed
  2. ``stooq``     -- keyless CSV over HTTPS, good for US equities/ETFs
  3. ``cache``     -- whatever was last fetched to disk
  4. ``synthetic`` -- deterministic pseudo-market, for testing and demos

Everything fetched is cached to ``~/.decasmg/cache`` so the toolkit still works
on a weekend, on a school network that blocks finance sites, or on a plane the
night before the pitch.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(os.environ.get("DECASMG_CACHE", Path.home() / ".decasmg" / "cache"))
CACHE_TTL_HOURS = 12.0


@dataclass
class PriceHistory:
    """A daily close series for one symbol."""

    symbol: str
    dates: list[_dt.date]
    closes: list[float]
    source: str = "unknown"

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last(self) -> float:
        return self.closes[-1] if self.closes else 0.0

    def returns(self) -> list[float]:
        """Simple daily returns."""
        return [
            (self.closes[i] / self.closes[i - 1]) - 1.0
            for i in range(1, len(self.closes))
            if self.closes[i - 1] > 0
        ]

    def log_returns(self) -> list[float]:
        return [
            math.log(self.closes[i] / self.closes[i - 1])
            for i in range(1, len(self.closes))
            if self.closes[i - 1] > 0 and self.closes[i] > 0
        ]

    def annualized_vol(self, window: int | None = None) -> float:
        """Annualized volatility of daily returns (252 trading days)."""
        r = self.returns()
        if window:
            r = r[-window:]
        if len(r) < 2:
            return 0.0
        mean = sum(r) / len(r)
        var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
        return math.sqrt(var) * math.sqrt(252)

    def total_return(self, window: int | None = None) -> float:
        c = self.closes[-window:] if window else self.closes
        if len(c) < 2 or c[0] <= 0:
            return 0.0
        return c[-1] / c[0] - 1.0

    def max_drawdown(self) -> float:
        """Worst peak-to-trough decline, as a negative fraction."""
        peak, worst = -math.inf, 0.0
        for c in self.closes:
            peak = max(peak, c)
            if peak > 0:
                worst = min(worst, c / peak - 1.0)
        return worst

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "dates": [d.isoformat() for d in self.dates],
            "closes": self.closes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PriceHistory":
        return cls(
            symbol=d["symbol"],
            dates=[_dt.date.fromisoformat(x) for x in d["dates"]],
            closes=[float(x) for x in d["closes"]],
            source=d.get("source", "cache"),
        )


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper().replace('^', '_').replace('/', '_')}.json"


def _read_cache(symbol: str, max_age_hours: float = CACHE_TTL_HOURS) -> PriceHistory | None:
    p = _cache_path(symbol)
    if not p.exists():
        return None
    age_h = (_dt.datetime.now().timestamp() - p.stat().st_mtime) / 3600.0
    if max_age_hours >= 0 and age_h > max_age_hours:
        return None
    try:
        ph = PriceHistory.from_dict(json.loads(p.read_text()))
        ph.source = "cache"
        return ph
    except Exception:
        return None


def _write_cache(ph: PriceHistory) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ph.symbol).write_text(json.dumps(ph.to_dict()))
    except OSError:
        pass  # a read-only home directory must not break analysis


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def _fetch_yfinance(symbol: str, days: int) -> PriceHistory | None:
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None
    try:
        period = f"{max(5, int(days * 1.6))}d"
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        dates = [d.date() if hasattr(d, "date") else d for d in df.index]
        closes = [float(x) for x in df["Close"].tolist()]
        pairs = [(d, c) for d, c in zip(dates, closes) if c and c > 0]
        if len(pairs) < 2:
            return None
        return PriceHistory(
            symbol.upper(), [p[0] for p in pairs], [p[1] for p in pairs], "yfinance"
        )
    except Exception:
        return None


def _fetch_stooq(symbol: str, days: int) -> PriceHistory | None:
    """Keyless daily CSV. US tickers need the ``.us`` suffix."""
    try:
        import requests  # type: ignore
    except ImportError:
        return None
    sym = symbol.lower().lstrip("^")
    candidates = [f"{sym}.us", sym]
    for cand in candidates:
        try:
            url = f"https://stooq.com/q/d/l/?s={cand}&i=d"
            resp = requests.get(url, timeout=20)
            if resp.status_code != 200 or "Date" not in resp.text[:64]:
                continue
            rows = list(csv.DictReader(io.StringIO(resp.text)))
            pairs = []
            for r in rows:
                try:
                    c = float(r["Close"])
                    if c > 0:
                        pairs.append((_dt.date.fromisoformat(r["Date"]), c))
                except (ValueError, KeyError, TypeError):
                    continue
            if len(pairs) < 2:
                continue
            pairs = pairs[-days:]
            return PriceHistory(
                symbol.upper(), [p[0] for p in pairs], [p[1] for p in pairs], "stooq"
            )
        except Exception:
            continue
    return None


def _synthetic(symbol: str, days: int) -> PriceHistory:
    """A deterministic pseudo-market seeded by the ticker.

    Not a forecast of anything -- it exists so every code path can be exercised
    and demonstrated without a network. Runs are reproducible for a given
    symbol, which makes tests stable.
    """
    seed = int(hashlib.sha256(symbol.upper().encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    ann_vol = 0.18 + (seed % 70) / 100.0          # 18%..87% annualized
    ann_drift = -0.05 + (seed % 41) / 100.0        # -5%..+35% annualized
    dvol = ann_vol / math.sqrt(252)
    ddrift = ann_drift / 252
    price = 20.0 + (seed % 380)
    today = _dt.date.today()
    dates: list[_dt.date] = []
    closes: list[float] = []
    d = today - _dt.timedelta(days=int(days * 1.45))
    while len(closes) < days:
        if d.weekday() < 5:
            price *= math.exp(ddrift - 0.5 * dvol**2 + dvol * rng.gauss(0, 1))
            dates.append(d)
            closes.append(round(max(0.5, price), 2))
        d += _dt.timedelta(days=1)
    return PriceHistory(symbol.upper(), dates, closes, "synthetic")


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

PROVIDERS = ("yfinance", "stooq", "cache", "synthetic")


def get_history(
    symbol: str,
    days: int = 252,
    providers: tuple[str, ...] = PROVIDERS,
    use_cache: bool = True,
) -> PriceHistory:
    """Daily closes for ``symbol``, trying each provider in order.

    Always returns a series -- ``synthetic`` is the terminal fallback so callers
    never have to handle ``None``. Check ``.source`` when the distinction
    matters; ``smg`` commands print it so you always know if you are looking at
    real prices.
    """
    symbol = symbol.upper().strip()

    if use_cache and "cache" in providers:
        fresh = _read_cache(symbol)
        if fresh is not None and len(fresh) >= min(days, 30):
            return fresh

    for name in providers:
        if name == "yfinance":
            ph = _fetch_yfinance(symbol, days)
        elif name == "stooq":
            ph = _fetch_stooq(symbol, days)
        elif name == "cache":
            ph = _read_cache(symbol, max_age_hours=-1)  # any age, as a fallback
        elif name == "synthetic":
            ph = _synthetic(symbol, days)
        else:
            continue
        if ph is not None and len(ph) >= 2:
            if ph.source in ("yfinance", "stooq"):
                _write_cache(ph)
            return ph

    return _synthetic(symbol, days)


def get_many(
    symbols: list[str], days: int = 252, providers: tuple[str, ...] = PROVIDERS
) -> dict[str, PriceHistory]:
    """Fetch several symbols, preserving order and skipping nothing."""
    return {s.upper().strip(): get_history(s, days, providers) for s in symbols}


def latest_prices(symbols: list[str], providers: tuple[str, ...] = PROVIDERS) -> dict[str, float]:
    """Most recent close per symbol -- what you mark a portfolio to."""
    return {s: h.last for s, h in get_many(symbols, days=30, providers=providers).items()}


def align(histories: dict[str, PriceHistory]) -> tuple[list[_dt.date], dict[str, list[float]]]:
    """Restrict every series to the dates they all share.

    Correlation is meaningless across misaligned calendars -- mutual funds,
    ETFs and stocks often have different histories -- so the simulator aligns
    before it does anything else.
    """
    if not histories:
        return [], {}
    common: set[_dt.date] | None = None
    for h in histories.values():
        s = set(h.dates)
        common = s if common is None else (common & s)
    dates = sorted(common or set())
    out: dict[str, list[float]] = {}
    for sym, h in histories.items():
        lookup = dict(zip(h.dates, h.closes))
        out[sym] = [lookup[d] for d in dates]
    return dates, out


def import_csv(path: str | Path, symbol: str | None = None) -> PriceHistory:
    """Load a hand-exported CSV with ``Date`` and ``Close`` columns.

    The universal escape hatch: if every provider is blocked, export from any
    source that works and feed it in here.
    """
    p = Path(path)
    sym = (symbol or p.stem).upper()
    rows = list(csv.DictReader(p.read_text().splitlines()))
    dcol = next((c for c in ("Date", "date", "DATE") if rows and c in rows[0]), "Date")
    ccol = next(
        (c for c in ("Close", "close", "Adj Close", "CLOSE") if rows and c in rows[0]),
        "Close",
    )
    pairs = []
    for r in rows:
        try:
            c = float(r[ccol])
            if c > 0:
                pairs.append((_dt.date.fromisoformat(r[dcol][:10]), c))
        except (ValueError, KeyError, TypeError):
            continue
    pairs.sort()
    ph = PriceHistory(sym, [x[0] for x in pairs], [x[1] for x in pairs], f"csv:{p.name}")
    _write_cache(ph)
    return ph
