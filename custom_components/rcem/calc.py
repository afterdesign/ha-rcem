"""Price lookup helpers (no Home Assistant imports, unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass

from .parser import MonthPrice


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    """Price that applies to a given month."""

    month: str  # month the price applies to, e.g. 2026-10
    source_month: str  # month whose published RCEm was used
    net_kwh: float  # PLN/kWh, net
    estimated: bool  # True when the month is not published yet


def month_key(year: int, month: int) -> str:
    """Return 'YYYY-MM'."""
    return f"{year:04d}-{month:02d}"


def resolve_price(
    prices: dict[str, MonthPrice],
    key: str,
    *,
    use_corrected: bool = True,
    estimate: bool = True,
) -> ResolvedPrice | None:
    """Return the RCEm for month `key` in PLN/kWh (net).

    When the month is not published yet and `estimate` is set, the latest
    month published before it is used and the result is flagged as estimated.
    """
    if (price := prices.get(key)) is not None:
        return ResolvedPrice(key, key, price.value(use_corrected) / 1000, False)
    if not estimate:
        return None
    earlier = [k for k in prices if k < key]
    if not earlier:
        return None
    src = max(earlier)
    return ResolvedPrice(key, src, prices[src].value(use_corrected) / 1000, True)


def gross(net: float, vat_percent: float) -> float:
    """Apply VAT."""
    return net * (1 + vat_percent / 100)
