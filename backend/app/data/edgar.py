# backend/app/data/edgar.py
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel

_SEC_BASE = "https://www.sec.gov"
_DATA_BASE = "https://data.sec.gov"
_USER_AGENT = "Stylobate research-bot (contact: rakhisinha100896@gmail.com)"


class FilingRef(BaseModel):
    type: str
    date: date
    url: str
    accession: str | None = None
    summary: str | None = None


_ticker_cik_cache: dict[str, str] | None = None


def _clear_caches() -> None:
    """Test helper. Resets the in-process ticker->CIK lookup."""
    global _ticker_cik_cache
    _ticker_cik_cache = None


async def _load_ticker_cik_map() -> dict[str, str]:
    global _ticker_cik_cache
    if _ticker_cik_cache is not None:
        return _ticker_cik_cache
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=10.0) as c:
        resp = await c.get(f"{_SEC_BASE}/files/company_tickers.json")
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json()
    table: dict[str, str] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik_int = entry.get("cik_str")
        if ticker and cik_int is not None:
            table[ticker] = f"{int(cik_int):010d}"
    _ticker_cik_cache = table
    return table


async def resolve_cik(ticker: str) -> str | None:
    table = await _load_ticker_cik_map()
    return table.get(ticker.upper())


async def fetch_filings(
    ticker: str,
    types: list[str] | None = None,
    limit: int = 5,
) -> list[FilingRef]:
    cik = await resolve_cik(ticker)
    if cik is None:
        return []

    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=15.0) as c:
        resp = await c.get(f"{_DATA_BASE}/submissions/CIK{cik}.json")
        resp.raise_for_status()
        submissions: dict[str, Any] = resp.json()

    recent: dict[str, Any] = submissions.get("filings", {}).get("recent", {})
    forms: list[str] = recent.get("form", [])
    dates: list[str] = recent.get("filingDate", [])
    accs: list[str] = recent.get("accessionNumber", [])
    docs: list[str] = recent.get("primaryDocument", [])

    cik_nz = str(int(cik))  # strip leading zeros for the Archives URL
    wanted = set(t.upper() for t in (types or ["10-K", "10-Q", "8-K"]))

    refs: list[FilingRef] = []
    for form, dt, acc, doc in zip(forms, dates, accs, docs, strict=False):
        if form.upper() not in wanted:
            continue
        acc_no_dashes = acc.replace("-", "")
        url = f"{_SEC_BASE}/Archives/edgar/data/{cik_nz}/{acc_no_dashes}/{doc}"
        refs.append(
            FilingRef(
                type=form,
                date=date.fromisoformat(dt),
                url=url,
                accession=acc,
            )
        )
        if len(refs) >= limit:
            break
    return refs
