"""Parser for the PSE RCEm page (no Home Assistant imports, unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import re

MONTHS_PL = {
    "styczeń": 1,
    "luty": 2,
    "marzec": 3,
    "kwiecień": 4,
    "maj": 5,
    "czerwiec": 6,
    "lipiec": 7,
    "sierpień": 8,
    "wrzesień": 9,
    "październik": 10,
    "listopad": 11,
    "grudzień": 12,
}

_YEAR_RE = re.compile(r"^(20\d{2})$")
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


@dataclass(frozen=True, slots=True)
class MonthPrice:
    """RCEm for one month, as published by PSE."""

    year: int
    month: int
    original: float  # first published RCEm [PLN/MWh]
    original_published: date | None
    corrected: float | None  # latest "skorygowana RCEm" [PLN/MWh]
    corrected_published: date | None

    @property
    def key(self) -> str:
        """Return the month key, e.g. 2026-09."""
        return f"{self.year:04d}-{self.month:02d}"

    def value(self, use_corrected: bool = True) -> float:
        """Return the price in PLN/MWh."""
        if use_corrected and self.corrected is not None:
            return self.corrected
        return self.original

    def published(self, use_corrected: bool = True) -> date | None:
        """Return the publication date of the price returned by value()."""
        if use_corrected and self.corrected is not None:
            return self.corrected_published
        return self.original_published


class _TableRows(HTMLParser):
    """Collect the text of every table cell, grouped by row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _parse_number(text: str) -> float | None:
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(text: str) -> date | None:
    if (m := _DATE_RE.match(text.strip())) is None:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_rcem(html: str) -> dict[str, MonthPrice]:
    """Parse the PSE page and return prices keyed by 'YYYY-MM'."""
    collector = _TableRows()
    collector.feed(html)

    found: dict[tuple[int, int], dict] = {}
    year: int | None = None
    month: int | None = None

    for cells in collector.rows:
        cells = [c for c in cells if c != ""] or cells
        if not cells:
            continue
        first = cells[0].strip()

        if len(cells) == 1:
            if m := _YEAR_RE.match(first):
                year, month = int(m.group(1)), None
                continue
            name = first.rstrip("*").strip().lower()
            if name in MONTHS_PL:
                month = MONTHS_PL[name]
            continue

        if year is None or month is None or len(cells) < 2:
            continue

        label = first.rstrip("*").strip().lower()
        value = _parse_number(cells[1])
        if value is None:
            continue
        published = _parse_date(cells[2]) if len(cells) > 2 else None
        entry = found.setdefault((year, month), {})

        if label == "rcem":
            entry["original"] = value
            entry["original_published"] = published
        elif label == "skorygowana rcem":
            # Several corrections may exist: keep the most recently published one.
            prev = entry.get("corrected_published")
            if "corrected" not in entry or (
                published is not None and (prev is None or published >= prev)
            ):
                entry["corrected"] = value
                entry["corrected_published"] = published

    result: dict[str, MonthPrice] = {}
    for (y, mth), entry in sorted(found.items()):
        if "original" not in entry:
            continue
        price = MonthPrice(
            year=y,
            month=mth,
            original=entry["original"],
            original_published=entry.get("original_published"),
            corrected=entry.get("corrected"),
            corrected_published=entry.get("corrected_published"),
        )
        result[price.key] = price
    return result
