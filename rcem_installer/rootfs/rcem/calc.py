"""Price lookup helpers (no Home Assistant imports, unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .parser import MonthPrice

Method = Literal["published", "rce", "last_published"]


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    """Price that applies to a given month."""

    month: str  # month the price applies to, e.g. 2026-10
    source_month: str  # month whose RCEm (or RCE estimate) was used
    net_kwh: float  # PLN/kWh, net
    method: Method

    @property
    def estimated(self) -> bool:
        """Return True when the month is not published yet."""
        return self.method != "published"


def month_key(year: int, month: int) -> str:
    """Return 'YYYY-MM'."""
    return f"{year:04d}-{month:02d}"


def resolve_price(
    prices: dict[str, MonthPrice],
    key: str,
    *,
    use_corrected: bool = True,
    estimate: bool = True,
    rce_estimates: dict[str, float] | None = None,
) -> ResolvedPrice | None:
    """Return the RCEm for month `key` in PLN/kWh (net).

    For a month PSE hasn't published, `rce_estimates` (PLN/MWh, estimated from
    RCE) is used: the month's own estimate, else the latest earlier one. Without
    any, the latest month published before it is reused.
    """
    if (price := prices.get(key)) is not None:
        return ResolvedPrice(key, key, price.value(use_corrected) / 1000, "published")
    if not estimate:
        return None
    if rce_estimates:
        if key in rce_estimates:
            return ResolvedPrice(key, key, rce_estimates[key] / 1000, "rce")
        latest_published = max(prices, default="")
        earlier = [k for k in rce_estimates if latest_published < k < key]
        if earlier:
            src = max(earlier)
            return ResolvedPrice(key, src, rce_estimates[src] / 1000, "rce")
    earlier = [k for k in prices if k < key]
    if not earlier:
        return None
    src = max(earlier)
    return ResolvedPrice(key, src, prices[src].value(use_corrected) / 1000, "last_published")


# Since February 2025 the prosumer deposit is RCEm × 1.23 (Dz.U. 2024 poz. 1847);
# before that it was RCEm alone. Checked against Enea deposits (01–02.2025).
DEPOSIT_FACTOR_FROM = "2025-02"


def gross(net: float, vat_percent: float, month: str | None = None) -> float:
    """Apply the deposit multiplier (1 + VAT) for months where it applies."""
    if month is not None and month < DEPOSIT_FACTOR_FROM:
        return net
    return net * (1 + vat_percent / 100)
