#!/usr/bin/env python
"""Bootstrap S&P 500 + Nifty 500 universe JSONs from Wikipedia.

Run quarterly (or as needed) to refresh constituent lists:

    cd backend
    uv pip install --no-deps pandas lxml
    uv run python scripts/build_universes.py

The script writes `data/universes/sp500.json` and `data/universes/nifty500.json`.
Commit the resulting JSONs to git. This script itself is NEVER imported by the
running app — pandas/lxml are NOT runtime dependencies.
"""
from __future__ import annotations

import json
import urllib.request
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-not-found]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _fetch_html(url: str) -> str:
    """Fetch a URL with a real browser User-Agent (Wikipedia 403s the default)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _read_html_tables(url: str) -> list[Any]:
    html = _fetch_html(url)
    return pd.read_html(StringIO(html))


def _slug_to_ticker(symbol: str, suffix: str = "") -> str:
    """Normalise to a yfinance-compatible ticker."""
    t = symbol.strip().upper().replace(".", "-")
    return f"{t}{suffix}" if suffix else t


def _build_sp500() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = _read_html_tables(url)
    df = tables[0]
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        symbol = str(row["Symbol"])
        name = str(row["Security"])
        sector = str(row["GICS Sector"])
        out.append({
            "ticker": _slug_to_ticker(symbol),
            "name": name,
            "sector": sector,
            "currency": "USD",
            "asset_class": "equity",
            "market": "US",
        })
    return out


def _build_nifty500() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/NIFTY_500"
    tables = _read_html_tables(url)

    df = None
    # 1) Prefer a table that already has Symbol + Company Name in its columns.
    for t in tables:
        cols = [str(c) for c in t.columns]
        if "Symbol" in cols and any("Company" in c or "Name" in c for c in cols):
            df = t
            break
    # 2) Otherwise look for a table whose first row contains Symbol +
    #    Company Name — Wikipedia sometimes ships the constituents table
    #    headerless. Promote that row to the header.
    if df is None:
        for t in tables:
            first_row = [str(v) for v in t.iloc[0].tolist()] if len(t) else []
            if "Symbol" in first_row and any(
                "Company" in v or "Name" in v for v in first_row
            ):
                df = t.iloc[1:].copy()
                df.columns = first_row
                break
    if df is None:
        raise RuntimeError("Could not find Symbol column in Nifty 500 page")

    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol or symbol == "nan":
            continue
        name = str(row.get("Company Name", row.get("Name", "")))
        sector = str(row.get("Sector", row.get("Industry", "")))
        if sector == "nan":
            sector = ""
        out.append({
            "ticker": _slug_to_ticker(symbol, suffix=".NS"),
            "name": name,
            "sector": sector or None,
            "currency": "INR",
            "asset_class": "equity",
            "market": "IN",
        })
    return out


def main() -> None:
    target_dir = Path(__file__).parent.parent / "data" / "universes"
    target_dir.mkdir(parents=True, exist_ok=True)

    sp500 = _build_sp500()
    nifty500 = _build_nifty500()

    sp500_path = target_dir / "sp500.json"
    nifty_path = target_dir / "nifty500.json"
    sp500_path.write_text(json.dumps(sp500, indent=2) + "\n")
    nifty_path.write_text(json.dumps(nifty500, indent=2) + "\n")

    print(f"SP500:    {len(sp500)} constituents -> {sp500_path}")
    print(f"Nifty500: {len(nifty500)} constituents -> {nifty_path}")


if __name__ == "__main__":
    main()
