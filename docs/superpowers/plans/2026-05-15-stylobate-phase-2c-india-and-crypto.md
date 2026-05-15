# Stylobate — Phase 2C: India + Crypto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deep-dives work for non-US tickers. After this plan: `RELIANCE.NS` (Indian listing on NSE) produces a 6-section report with Indian rates and INR financials; `BTC` produces a report with crypto-appropriate sections (no Fundamentals — that's not a thing for tokens).

**Architecture:** Specialists become *market-aware*. The route already knows market (`resolution.market` ∈ {US, IN, CRYPTO}); Lead Banker passes it into `_run_dispatch`, which passes it to each specialist runner. Macro tools route between US/IN FRED series. Fundamental Analyst returns a graceful "no financial statements" finding for crypto. yfinance ticker mapping turns `BTC` into `BTC-USD` before hitting the adapter.

**Tech Stack:** Existing Phase 2B stack — no new external deps. New FRED series IDs for Indian macro (already supported by the existing CSV adapter). yfinance handles `RELIANCE.NS`/`BTC-USD` natively.

**Spec reference:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` — implements §3 multi-market support (US/IN/CRYPTO), §5.1 Indian sources (FRED Indian series subset; full screener.in/BSE/NSE/MCA21 scraping deferred), §15 Phase 2 acceptance criterion ("deep-dive works for AAPL, RELIANCE.NS, BTC").

**Cost note:** Indian and crypto deep-dives have the same cost shape as US — 4 Sonnet specialists in parallel (or 3 for crypto since Fundamental short-circuits), ~$0.20–0.40 warm cache.

**Out of scope here (deferred to later phases):**
- screener.in / BSE / NSE / MCA21 scraping (richer Indian fundamentals than yfinance)
- Damodaran ERP / industry beta dataset (for DCF — DCF tool itself isn't in Phase 2 anyway)
- CoinGecko (richer crypto data — mcap, dominance, on-chain) — yfinance covers price/volume
- Trendlyne / Tickertape / Moneycontrol news for Indian tickers (yfinance covers basic news)
- Multi-currency portfolio aggregation (Phase 3 portfolio work)

---

## File map for Phase 2C

```
stylobate/
├── backend/
│   ├── app/
│   │   ├── data/
│   │   │   └── yfinance_adapter.py            MODIFY (crypto ticker mapping)
│   │   ├── tools/
│   │   │   └── macro.py                       MODIFY (market param on get_rates; Indian FRED series)
│   │   ├── agents/
│   │   │   ├── fundamental.py                 MODIFY (graceful empty for crypto; +market kwarg)
│   │   │   ├── technical.py                   MODIFY (+market kwarg, default "US")
│   │   │   ├── news_sentiment.py              MODIFY (+market kwarg, default "US")
│   │   │   ├── macro.py                       MODIFY (pass market through; +market kwarg)
│   │   │   └── lead_banker.py                 MODIFY (thread market through dispatch)
│   │   └── prompts/
│   │       ├── lead_banker.md                 MODIFY (market-aware dispatch heuristics)
│   │       ├── macro.md                       MODIFY (Indian vs US series guidance)
│   │       └── fundamental.md                 MODIFY (note: invoked only for stocks, not crypto)
│   └── tests/
│       ├── test_yfinance_adapter.py           MODIFY (+ crypto mapping tests)
│       ├── test_tools_macro.py                MODIFY (+ market-routing tests)
│       ├── test_macro_agent.py                MODIFY (+ market-passthrough test)
│       ├── test_fundamental_agent.py          MODIFY (+ crypto short-circuit test)
│       └── test_lead_banker_agent.py          MODIFY (+ market-aware dispatch test)
└── (no frontend changes — renderers are market-agnostic)
```

---

## Glossary — what `market` means in code

```python
# Defined in Phase 1 (app/agents/ticker_resolver.py)
Market = Literal["US", "IN", "CRYPTO"]
```

Used throughout Phase 2C. Detection rules — applied by ticker_resolver (Haiku model) and double-checked in code:

| Ticker form | Market | yfinance symbol |
|---|---|---|
| `AAPL`, `MSFT`, `BRK.B` | `US` | same |
| `RELIANCE.NS`, `HDFCBANK.NS` | `IN` (NSE) | same |
| `RELIANCE.BO`, `TCS.BO` | `IN` (BSE) | same |
| `BTC`, `ETH`, `SOL` | `CRYPTO` | `{TICKER}-USD` |

Two new FRED series for Indian macro (used by `get_rates` when `market="IN"`):

| Series ID | What it is |
|---|---|
| `INDIRSTPRLR01STM` | India — Repo Rate |
| `IRLTLT01INM156N` | India — 10Y Government Bond Yield |
| `INDCPIALLMINMEI` | India — CPI All Items |

---

## Task 1: Crypto ticker mapping in yfinance adapter

**Files:**
- Modify: `backend/app/data/yfinance_adapter.py`
- Modify: `backend/tests/test_yfinance_adapter.py`

**Goal:** When the resolver returns `BTC` with `market=CRYPTO`, the adapter calls `yf.Ticker("BTC-USD")`. Implemented as a single helper `_yf_symbol(ticker, market)` plus a one-line change in `_make_ticker`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_yfinance_adapter.py`:

```python
def test_yf_symbol_for_us_stock_unchanged() -> None:
    from app.data.yfinance_adapter import _yf_symbol
    assert _yf_symbol("AAPL", "US") == "AAPL"
    assert _yf_symbol("BRK.B", "US") == "BRK.B"


def test_yf_symbol_for_indian_stock_unchanged() -> None:
    from app.data.yfinance_adapter import _yf_symbol
    assert _yf_symbol("RELIANCE.NS", "IN") == "RELIANCE.NS"
    assert _yf_symbol("TCS.BO", "IN") == "TCS.BO"


def test_yf_symbol_for_crypto_appends_usd() -> None:
    from app.data.yfinance_adapter import _yf_symbol
    assert _yf_symbol("BTC", "CRYPTO") == "BTC-USD"
    assert _yf_symbol("ETH", "CRYPTO") == "ETH-USD"
    # Already in BTC-USD form: no double-suffix
    assert _yf_symbol("BTC-USD", "CRYPTO") == "BTC-USD"


@pytest.mark.asyncio
async def test_fetch_ticker_info_crypto_uses_mapped_symbol() -> None:
    captured: list[str] = []

    def _capture(symbol: str) -> MagicMock:
        captured.append(symbol)
        m = MagicMock()
        m.info = {"shortName": "Bitcoin USD", "currency": "USD", "marketCap": 1_500_000_000_000}
        m.fast_info = MagicMock(last_price=72000.0, market_cap=1_500_000_000_000)
        return m

    with patch("app.data.yfinance_adapter._make_ticker", side_effect=_capture):
        from app.data.yfinance_adapter import fetch_ticker_info
        info = await fetch_ticker_info("BTC", market="CRYPTO")
    assert captured == ["BTC-USD"]
    assert info.ticker == "BTC"  # canonical form preserved in return value
    assert info.currency == "USD"
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py::test_yf_symbol_for_us_stock_unchanged -v
```

Expected: `ImportError: cannot import name '_yf_symbol'`.

- [ ] **Step 3: Add `_yf_symbol` and thread `market` through the fetch functions**

In `backend/app/data/yfinance_adapter.py`:

Add this helper near the top, just after `_make_ticker`:

```python
def _yf_symbol(ticker: str, market: str) -> str:
    """Map our canonical ticker to the yfinance symbol.

    yfinance crypto symbols use a -USD suffix (BTC -> BTC-USD); our
    canonical form drops the suffix. US and Indian tickers pass through
    unchanged. If the ticker already includes -USD, don't double it.
    """
    if market == "CRYPTO":
        if "-" in ticker:
            return ticker
        return f"{ticker}-USD"
    return ticker
```

Then update each of the four async fetch functions to accept `market` and use `_yf_symbol`:

```python
async def fetch_ticker_info(ticker: str, market: str = "US") -> TickerInfo:
    def _sync() -> TickerInfo:
        t = _make_ticker(_yf_symbol(ticker, market))
        info = t.info or {}
        fast = getattr(t, "fast_info", None)
        last_price = _float_or_none(getattr(fast, "last_price", None))
        market_cap = info.get("marketCap") or getattr(fast, "market_cap", None)
        return TickerInfo(
            ticker=ticker.upper(),
            name=info.get("shortName") or info.get("longName") or ticker.upper(),
            currency=info.get("currency", "USD"),
            market_cap=int(market_cap) if market_cap else None,
            last_price=last_price,
        )
    return await asyncio.to_thread(_sync)
```

Apply the same pattern to `fetch_financials`, `fetch_key_ratios`, `fetch_price_history`, `fetch_news_for_ticker`:

```python
async def fetch_financials(ticker: str, periods: int = 4, market: str = "US") -> list[Financials]:
    def _sync() -> list[Financials]:
        t = _make_ticker(_yf_symbol(ticker, market))
        # ... rest unchanged
```

```python
async def fetch_key_ratios(ticker: str, market: str = "US") -> KeyRatios:
    def _sync() -> KeyRatios:
        t = _make_ticker(_yf_symbol(ticker, market))
        # ... rest unchanged
```

```python
async def fetch_price_history(ticker: str, period: str = "1y", interval: str = "1d",
                              market: str = "US") -> list[Bar]:
    def _sync() -> list[Bar]:
        t = _make_ticker(_yf_symbol(ticker, market))
        # ... rest unchanged
```

```python
async def fetch_news_for_ticker(ticker: str, limit: int = 10, market: str = "US") -> list[NewsItem]:
    def _sync() -> list[NewsItem]:
        t = _make_ticker(_yf_symbol(ticker, market))
        # ... rest unchanged
```

(Note: only the `_make_ticker(...)` call inside each `_sync` changes; the rest of each function body stays the same. Default `market="US"` keeps every existing call site working without an explicit market arg.)

`fetch_sector_etf_returns` does NOT need this — sector ETFs are always US. Leave it unchanged.

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_yfinance_adapter.py -v
```

Expected: 14 passed (10 existing + 4 new).

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 76 passing (72 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/data/yfinance_adapter.py backend/tests/test_yfinance_adapter.py
git commit -m "feat(backend): crypto ticker mapping in yfinance adapter (BTC -> BTC-USD)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Market-aware macro tools (Indian FRED series)

**Files:**
- Modify: `backend/app/tools/macro.py`
- Modify: `backend/tests/test_tools_macro.py`

**Goal:** Add a `market` arg to `get_rates_tool`. For `market="IN"`, fetch the Indian FRED series; for `market="US"` or `"CRYPTO"` (crypto trades against USD, so US rates are the right macro context), keep current behaviour. The `RatesResult` model gains generic `policy_rate` / `long_yield` / `inflation` fields so the same shape covers both regions; legacy fields (`fed_funds`, `treasury_2y`, `treasury_10y`, `real_10y`) become aliases that populate when market=US.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tools_macro.py`:

```python
@pytest.mark.asyncio
async def test_get_rates_us_market_returns_us_series() -> None:
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from app.data.fred import TimeSeriesPoint
        from datetime import date
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=4.42)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl(market="US"))
    # Should pull FEDFUNDS, DGS2, DGS10, DFII10
    assert "FEDFUNDS" in captured
    assert "DGS10" in captured
    assert r.policy_rate == 4.42
    # Legacy field for US callers
    assert r.fed_funds == 4.42


@pytest.mark.asyncio
async def test_get_rates_indian_market_returns_in_series() -> None:
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from app.data.fred import TimeSeriesPoint
        from datetime import date
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=6.50)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl(market="IN"))
    # Should pull the Indian series, NOT FEDFUNDS
    assert "INDIRSTPRLR01STM" in captured
    assert "IRLTLT01INM156N" in captured
    assert "FEDFUNDS" not in captured
    assert r.policy_rate == 6.50
    # US legacy field should be None when market is IN
    assert r.fed_funds is None


@pytest.mark.asyncio
async def test_get_rates_crypto_uses_us_series() -> None:
    """Crypto trades against USD, so US macro is the right context."""
    captured: list[str] = []

    async def fake_fred(series_id: str) -> list[Any]:
        captured.append(series_id)
        from app.data.fred import TimeSeriesPoint
        from datetime import date
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=4.42)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import get_rates_tool
        await get_rates_tool.impl(market="CRYPTO")
    assert "FEDFUNDS" in captured


def test_get_rates_schema_documents_market() -> None:
    from app.tools.macro import get_rates_tool
    s = get_rates_tool.schema
    assert "market" in s["input_schema"]["properties"]
    market_enum = s["input_schema"]["properties"]["market"]["enum"]
    assert set(market_enum) == {"US", "IN", "CRYPTO"}
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_macro.py -v
```

Expected: failures on the new tests (the market arg doesn't exist; `policy_rate` field missing).

- [ ] **Step 3: Update `backend/app/tools/macro.py`**

Replace the `RatesResult` model and `_impl_get_rates` + `get_rates_tool`:

```python
class RatesResult(BaseModel):
    market: Literal["US", "IN", "CRYPTO"]
    # Generic fields populated for any market
    policy_rate: float | None = None       # FedFunds / Repo Rate / FedFunds (crypto)
    long_yield: float | None = None        # 10Y Treasury / 10Y G-Sec / 10Y Treasury
    inflation: float | None = None         # CPI YoY (US/IN); None for crypto
    # US-specific legacy fields (populated when market == "US"; None otherwise)
    fed_funds: float | None = None
    treasury_2y: float | None = None
    treasury_10y: float | None = None
    real_10y: float | None = None


_US_RATE_SERIES: dict[str, str] = {
    "fed_funds": "FEDFUNDS",
    "treasury_2y": "DGS2",
    "treasury_10y": "DGS10",
    "real_10y": "DFII10",
}

_IN_RATE_SERIES: dict[str, str] = {
    "policy_rate": "INDIRSTPRLR01STM",  # India - Repo Rate
    "long_yield": "IRLTLT01INM156N",    # India - 10Y Government Bond Yield
    "inflation": "INDCPIALLMINMEI",     # India - CPI All Items
}


def _last_value(points: list[TimeSeriesPoint]) -> float | None:
    for p in reversed(points):
        if p.value is not None:
            return p.value
    return None


async def _impl_get_rates(market: str = "US") -> RatesResult:
    mkt = market if market in ("US", "IN", "CRYPTO") else "US"
    if mkt == "IN":
        values: dict[str, float | None] = {}
        for key, series_id in _IN_RATE_SERIES.items():
            try:
                pts = await _fetch_fred_series(series_id)
                values[key] = _last_value(pts)
            except Exception:
                values[key] = None
        return RatesResult(
            market="IN",
            policy_rate=values.get("policy_rate"),
            long_yield=values.get("long_yield"),
            inflation=values.get("inflation"),
        )

    # US or CRYPTO -> US series (crypto trades against USD)
    values_us: dict[str, float | None] = {}
    for key, series_id in _US_RATE_SERIES.items():
        try:
            pts = await _fetch_fred_series(series_id)
            values_us[key] = _last_value(pts)
        except Exception:
            values_us[key] = None
    return RatesResult(
        market=mkt,  # "US" or "CRYPTO"
        policy_rate=values_us["fed_funds"],
        long_yield=values_us["treasury_10y"],
        fed_funds=values_us["fed_funds"],
        treasury_2y=values_us["treasury_2y"],
        treasury_10y=values_us["treasury_10y"],
        real_10y=values_us["real_10y"],
    )


get_rates_tool = Tool(
    name="get_rates",
    description=(
        "Latest policy + long-rate snapshot. market='US' returns Fed Funds, 2Y, "
        "10Y, real 10Y. market='IN' returns Repo Rate, 10Y G-Sec, CPI. "
        "market='CRYPTO' returns US rates (crypto trades against USD). "
        "Defaults to US."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "market": {
                "type": "string",
                "enum": ["US", "IN", "CRYPTO"],
                "default": "US",
            },
        },
        "required": [],
    },
    impl=_impl_get_rates,
)
```

Also remove the old top-level `_fetch_rates_snapshot` import (we now call `_fetch_fred_series` directly per series). Replace the existing imports at the top of the file:

Find:
```python
from app.data.fred import (
    TimeSeriesPoint,
    fetch_fred_series as _fetch_fred_series,
    fetch_rates_snapshot as _fetch_rates_snapshot,
)
```

Replace with:
```python
from typing import Literal

from app.data.fred import (
    TimeSeriesPoint,
    fetch_fred_series as _fetch_fred_series,
)
```

The existing `test_get_rates_returns_typed_snapshot` test patched `_fetch_rates_snapshot`. Update it:

In `backend/tests/test_tools_macro.py`, find and replace the existing test:

```python
@pytest.mark.asyncio
async def test_get_rates_returns_typed_snapshot() -> None:
    """Default market=US returns all four US rate fields populated."""
    async def fake_fred(series_id: str) -> list[Any]:
        from app.data.fred import TimeSeriesPoint
        from datetime import date
        return [TimeSeriesPoint(date=date(2026, 5, 14), value=4.42)]

    with patch("app.tools.macro._fetch_fred_series", side_effect=fake_fred):
        from app.tools.macro import RatesResult, get_rates_tool
        r = cast(RatesResult, await get_rates_tool.impl())
    assert r.market == "US"
    assert r.fed_funds == 4.42
    assert r.treasury_10y == 4.42
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_tools_macro.py -v
```

Expected: 8 passed (the original 4 + 4 new).

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 80 passing (76 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/tools/macro.py backend/tests/test_tools_macro.py
git commit -m "feat(backend): market-aware get_rates (US/IN/CRYPTO routing via FRED series)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Macro Strategist agent — pass market through; prompt update

**Files:**
- Modify: `backend/app/agents/macro.py`
- Modify: `backend/app/prompts/macro.md`
- Modify: `backend/tests/test_macro_agent.py`

**Goal:** Macro Strategist accepts a `market` kwarg, surfaces it to the model in the user message, and the prompt instructs the model to pass `market=<...>` into `get_rates`. The `MacroFindings.regime` enum stays the same — Indian "tightening" / "neutral" / "expansionary" reads the same way as US.

- [ ] **Step 1: Update the prompt**

`backend/app/prompts/macro.md` — replace contents with:

```markdown
You are the Macro Strategist inside Stylobate. Your job is to sketch the macro context relevant to the user's question — rates, sector rotation (US), and any specific series the question implies.

You receive a `market` (one of US, IN, CRYPTO). Use it to choose the right data:

- **market = US**: call `get_rates(market="US")`, `get_sector_perf(period="1mo")`. Optionally `get_fred_series` for US-specific series (CPIAUCSL for CPI, UNRATE for unemployment, RSAFS for retail sales).
- **market = IN**: call `get_rates(market="IN")` to get repo rate + 10Y G-Sec + India CPI. Do NOT call `get_sector_perf` (those are US sector ETFs and not relevant). You may call `get_fred_series` with Indian series IDs if you have one to mind (e.g., INDPROINDMISMEI for industrial production, INDLOCOSTOXMEI for stock index).
- **market = CRYPTO**: call `get_rates(market="CRYPTO")` (returns US rates — crypto trades against USD). Skip sector perf. Macro is mostly about USD liquidity for crypto.

Process:
1. Call the appropriate `get_rates` call for the market.
2. Conditionally call `get_sector_perf` (US only).
3. Optionally call `get_fred_series` for any specific series.
4. Call `submit_macro_findings` exactly once. Stop.

Discipline:
- `regime` is one of: "expansionary" / "neutral" / "tightening" / "uncertain". Apply to whichever market you analyzed.
- `rates_snapshot` echoes what `get_rates` returned (use whichever keys are populated).
- `sector_performance` is the dict from `get_sector_perf` (empty for IN and CRYPTO).
- `macro_notes` are 3-5 short specific bullets relevant to the question.
- `citations` reference "FRED" with the series IDs you used.
- `confidence`: 0.85+ if you got rates + (sector data for US, or specific FRED context for IN/CRYPTO). <0.7 if data is sparse.
```

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/test_macro_agent.py`:

```python
@pytest.mark.asyncio
async def test_macro_agent_passes_market_to_get_rates() -> None:
    """When run_macro_analysis is called with market=IN, the user message
    surfaces market=IN so the model can include it in the get_rates call."""
    from app.tools.macro import (
        RatesResult,
        get_rates_tool,
        get_sector_perf_tool,
    )

    fake_rates = RatesResult(
        market="IN", policy_rate=6.50, long_yield=7.10, inflation=4.80,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use_block("get_rates", {"market": "IN"}, "r1")),
        _message(
            _tool_use_block("submit_macro_findings", {
                "regime": "neutral",
                "rates_snapshot": {"policy_rate": 6.50, "long_yield": 7.10, "inflation": 4.80},
                "sector_performance": {},
                "macro_notes": ["Repo at 6.50% — restrictive zone"],
                "citations": [{"source": "fred", "ref": "FRED:INDIRSTPRLR01STM,IRLTLT01INM156N"}],
                "confidence": 0.85,
            }, "r2"),
            stop_reason="tool_use",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_rates_tool, "impl", AsyncMock(return_value=fake_rates))
        # sector tool shouldn't be called for IN — make it raise if it is
        mp.setattr(get_sector_perf_tool, "impl",
                   AsyncMock(side_effect=AssertionError("sector_perf must not be called for IN")))
        from app.agents.macro import run_macro_analysis
        findings = await run_macro_analysis(
            ticker="RELIANCE.NS", brief="Indian deep dive",
            market="IN", client=fake_client,
        )
    # Verify the user message carried the market context
    first_call = fake_client.messages.create.await_args_list[0]
    user_message_content = first_call.kwargs["messages"][0]["content"]
    assert "market: IN" in user_message_content.lower() or "market = in" in user_message_content.lower()
    assert findings.regime == "neutral"
```

- [ ] **Step 3: Update `backend/app/agents/macro.py`**

Find the `run_macro_analysis` function signature and update:

```python
async def run_macro_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",
    client: AsyncAnthropic | Any | None = None,
) -> MacroFindings:
```

Find the `messages` list construction and update the user content to include market:

```python
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"User question relates to ticker: {ticker} (market: {market})\n"
                f"Brief: {brief}\n"
                "Use the macro tools (pass market=... to get_rates) and "
                "submit_macro_findings when ready."
            ),
        }
    ]
```

- [ ] **Step 4: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_macro_agent.py -v
```

Expected: 3 passed (2 existing + 1 new).

- [ ] **Step 5: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 81 passing (80 prior + 1 new). (NOTE: Lead Banker calls `run_macro_analysis` without `market=`; that's still valid because of the default. T5 wires market through.)

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents/macro.py backend/app/prompts/macro.md backend/tests/test_macro_agent.py
git commit -m "feat(backend): Macro Strategist accepts market kwarg; prompt routes IN vs US

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Fundamental Analyst — graceful empty for crypto

**Files:**
- Modify: `backend/app/agents/fundamental.py`
- Modify: `backend/app/prompts/fundamental.md`
- Modify: `backend/tests/test_fundamental_agent.py`

**Goal:** When `market == "CRYPTO"`, the Fundamental Analyst short-circuits — no LLM call, no tool calls. It returns a `FundamentalFindings` saying "no traditional financial statements for crypto assets" with confidence 0.0. Lead Banker still gets a typed payload that fits its synthesis prompt.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_fundamental_agent.py`:

```python
@pytest.mark.asyncio
async def test_fundamental_agent_short_circuits_for_crypto() -> None:
    """For market=CRYPTO, no LLM call happens — agent returns a graceful empty finding."""
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        side_effect=AssertionError("no LLM call should happen for crypto fundamentals"),
    )
    from app.agents.fundamental import run_fundamental_analysis
    findings = await run_fundamental_analysis(
        ticker="BTC", brief="deep dive on BTC", market="CRYPTO", client=fake_client,
    )
    assert findings.ticker == "BTC"
    assert findings.confidence == 0.0
    # Some short transparent message indicating no financial statements
    assert any(
        "no traditional" in s.lower() or "not applicable" in s.lower() or "n/a" in s.lower()
        for s in (findings.fundamentals_summary + [findings.thesis])
    )
    # Verify the LLM was never called
    fake_client.messages.create.assert_not_called()
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fundamental_agent.py -v
```

Expected: existing tests pass, new test fails with `AssertionError` (the function calls the client without short-circuiting first).

- [ ] **Step 3: Update `backend/app/agents/fundamental.py`**

Find the `run_fundamental_analysis` signature and update to add `market`:

```python
async def run_fundamental_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",
    client: AsyncAnthropic | Any | None = None,
) -> FundamentalFindings:
```

Add this short-circuit at the very top of the function body, before `c = client or get_client()`:

```python
    if market == "CRYPTO":
        return FundamentalFindings(
            ticker=ticker,
            thesis=(
                "Crypto assets do not have traditional financial statements. "
                "Refer to the Technical, News, and Macro sections for context."
            ),
            fundamentals_summary=[
                "Not applicable: no income statement / balance sheet for tokens.",
            ],
            risks=[
                "Crypto-specific risks (custody, regulatory, exchange counterparty, "
                "liquidity at sale) are evaluated in the Risks section.",
            ],
            citations=[],
            confidence=0.0,
        )

    c = client or get_client()
```

- [ ] **Step 4: Update the prompt for clarity**

Edit `backend/app/prompts/fundamental.md`. Append at the bottom:

```markdown

Note: you will only be invoked for stocks (US or India), not for crypto. The Lead Banker handles crypto fundamentals separately.
```

- [ ] **Step 5: Run, confirm passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_fundamental_agent.py -v
```

Expected: 3 passed (2 existing + 1 new).

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 82 passing (81 prior + 1 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents/fundamental.py backend/app/prompts/fundamental.md backend/tests/test_fundamental_agent.py
git commit -m "feat(backend): Fundamental Analyst short-circuits with graceful empty for crypto

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Lead Banker — thread market through dispatch

**Files:**
- Modify: `backend/app/agents/lead_banker.py`
- Modify: `backend/app/prompts/lead_banker.md`
- Modify: `backend/tests/test_lead_banker_agent.py`

**Goal:** `_run_dispatch` and the user message both carry `market`. Each specialist runner gets `market=<...>` as a kwarg. Prompt updated with dispatch heuristics per market.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_lead_banker_agent.py`:

```python
@pytest.mark.asyncio
async def test_lead_banker_passes_market_to_specialists() -> None:
    """For an Indian ticker, market=IN must be passed to each specialist runner."""
    from app.agents.fundamental import Citation, FundamentalFindings
    from app.agents.macro import MacroFindings
    from app.agents.news_sentiment import NewsFindings
    from app.agents.technical import TechnicalFinding
    from app.agents.ticker_resolver import TickerResolution

    resolution = TickerResolution(
        ticker="RELIANCE.NS", name="Reliance Industries", market="IN",
        asset_class="equity", confidence=0.95,
    )

    captured_markets: dict[str, str] = {}

    async def fake_fundamental(*, ticker: str, brief: str, market: str = "US") -> FundamentalFindings:
        captured_markets["fundamental"] = market
        return FundamentalFindings(
            ticker=ticker, thesis="ok", fundamentals_summary=[], risks=[],
            citations=[Citation(source="yfinance", ref="x")], confidence=0.8,
        )

    async def fake_technical(*, ticker: str, brief: str, market: str = "US") -> TechnicalFinding:
        captured_markets["technical"] = market
        return TechnicalFinding(
            ticker=ticker, trend="sideways", confidence=0.7,
        )

    async def fake_news(*, ticker: str, brief: str, market: str = "US") -> NewsFindings:
        captured_markets["news"] = market
        return NewsFindings(
            ticker=ticker, headline_count=0, sentiment="neutral", confidence=0.3,
        )

    async def fake_macro(*, ticker: str, brief: str, market: str = "US") -> MacroFindings:
        captured_markets["macro"] = market
        return MacroFindings(regime="neutral", confidence=0.8)

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[
        _message(_tool_use("dispatch_specialists", {
            "specialists": ["fundamental", "technical", "news", "macro"],
            "brief": "Reliance deep dive",
        }, "d1"), stop_reason="tool_use"),
        _message(
            _tool_use("emit_quick_take", {"signal": "hold", "qualifier": "ok"}, "1"),
            _tool_use("emit_stock_card", {
                "ticker": "RELIANCE.NS", "name": "Reliance", "market": "IN",
                "currency": "INR", "stats": {},
            }, "2"),
            _tool_use("emit_section", {"title": "Thesis", "markdown": ".", "citations": []}, "3"),
            _tool_use("emit_section", {"title": "Risks", "markdown": ".", "citations": []}, "4"),
            _tool_use("emit_recommendation", {
                "signal": "hold", "position_size_range": [0, 0],
                "entry_zone": "n/a", "stop": "n/a", "target_12mo_base": "n/a",
            }, "5"),
            _tool_use("emit_disclaimer", {}, "6"),
            _tool_use("emit_done", {}, "7"),
            stop_reason="end_turn",
        ),
    ])

    with pytest.MonkeyPatch.context() as mp:
        from app.agents import lead_banker as lb
        mp.setattr(lb, "run_fundamental_analysis", fake_fundamental)
        mp.setattr(lb, "run_technical_analysis", fake_technical)
        mp.setattr(lb, "run_news_analysis", fake_news)
        mp.setattr(lb, "run_macro_analysis", fake_macro)

        from app.agents.lead_banker import run_lead_banker
        async for _d in run_lead_banker(
            user_message="deep dive on Reliance",
            resolution=resolution,
            client=fake_client,
        ):
            pass

    assert captured_markets == {
        "fundamental": "IN",
        "technical": "IN",
        "news": "IN",
        "macro": "IN",
    }
```

- [ ] **Step 2: Run, confirm fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py::test_lead_banker_passes_market_to_specialists -v
```

Expected: fails with assertion that captured markets are all "US" (default), not "IN".

- [ ] **Step 3: Update `backend/app/agents/lead_banker.py`**

Find `_run_dispatch` and replace with:

```python
async def _run_dispatch(
    specialists: list[str], brief: str, ticker: str, market: str,
) -> dict[str, Any]:
    """Run the requested specialists in parallel. Returns {name: findings_dict} + errors."""
    name_to_coro: dict[str, Any] = {}
    if "fundamental" in specialists:
        name_to_coro["fundamental"] = run_fundamental_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "technical" in specialists:
        name_to_coro["technical"] = run_technical_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "news" in specialists:
        name_to_coro["news"] = run_news_analysis(
            ticker=ticker, brief=brief, market=market,
        )
    if "macro" in specialists:
        name_to_coro["macro"] = run_macro_analysis(
            ticker=ticker, brief=brief, market=market,
        )

    if not name_to_coro:
        return {"errors": {"none": "no recognized specialists requested"}}

    results = await asyncio.gather(*name_to_coro.values(), return_exceptions=True)
    out: dict[str, Any] = {"errors": {}}
    for name, res in zip(name_to_coro.keys(), results, strict=True):
        if isinstance(res, Exception):
            out["errors"][name] = str(res)
        else:
            out[name] = res.model_dump(mode="json")
    return out
```

Find the call site of `_run_dispatch` in `run_lead_banker` and pass market:

```python
            if name == "dispatch_specialists":
                specialists = cast(list[str], args.get("specialists", []))
                brief = cast(str, args.get("brief", ""))
                findings = await _run_dispatch(
                    specialists, brief, resolution.ticker, resolution.market,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block_id,
                    "content": json.dumps(findings, default=str),
                })
                continue
```

Find `_build_user_message` and update:

```python
def _build_user_message(user_text: str, resolution: TickerResolution) -> str:
    return (
        f"User question: {user_text}\n\n"
        f"Resolved ticker: {resolution.ticker} ({resolution.name}, market={resolution.market})\n\n"
        "Specialists available: fundamental, technical, news, macro.\n\n"
        "Call dispatch_specialists first with the specialists you want, then use the emit_* tools "
        "to stream the response. Emit order: quick_take → stock_card → sections → recommendation "
        "→ disclaimer → done."
    )
```

Note: the news, technical, and fundamental agents will also need `market` kwargs added to their signatures (defaults to "US" so existing call sites stay valid). Task 4 added market to Fundamental; for Technical and News, add it now:

Edit `backend/app/agents/technical.py`. Find:

```python
async def run_technical_analysis(
    *,
    ticker: str,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
) -> TechnicalFinding:
```

Change to:

```python
async def run_technical_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",  # noqa: ARG001 — passed to data layer in Phase 3+
    client: AsyncAnthropic | Any | None = None,
) -> TechnicalFinding:
```

Same change in `backend/app/agents/news_sentiment.py`:

```python
async def run_news_analysis(
    *,
    ticker: str,
    brief: str,
    market: str = "US",  # noqa: ARG001 — passed to data layer in Phase 3+
    client: AsyncAnthropic | Any | None = None,
) -> NewsFindings:
```

The `# noqa: ARG001` suppresses ruff's unused-arg warning. Technical and News don't currently route by market (yfinance handles all markets transparently for those tools), but Lead Banker will pass the kwarg uniformly; future phases may use it.

- [ ] **Step 4: Update the Lead Banker prompt**

`backend/app/prompts/lead_banker.md` — replace contents with:

```markdown
You are the Lead Banker inside Stylobate. You drive the whole research turn:

1. Decide which specialists to consult and call `dispatch_specialists` with that list.
2. Receive the combined specialist findings as a tool result.
3. Synthesize and stream the response via the `emit_*` tools.

The user message tells you the resolved ticker and its market. Specialists available: `fundamental`, `technical`, `news`, `macro`.

When to dispatch which (by market):

**US ticker (e.g., AAPL, MSFT)**:
- Default deep-dive: dispatch all four.
- "Fundamentals only" question: just `fundamental`.

**Indian ticker (e.g., RELIANCE.NS, HDFCBANK.NS)**:
- Default deep-dive: dispatch all four. The Macro specialist will use Indian rates (repo + 10Y G-Sec + India CPI) automatically.
- Stock card `currency` should be `INR`.

**Crypto (e.g., BTC, ETH, market=CRYPTO)**:
- Dispatch `technical`, `news`, `macro` ONLY. Skip `fundamental` — crypto has no traditional financial statements.
- Stock card `currency` is `USD`.
- The Macro specialist will use US rates (crypto trades against USD).

When in doubt, dispatch more rather than less. They run in parallel.

After dispatch returns, emit deltas in this exact order:
1. `emit_quick_take` — one short signal line.
2. `emit_stock_card` — ticker identity + key stats. Use INR for Indian, USD for US/crypto.
3. `emit_section` — include sections in this order:
   - `Thesis` — always.
   - `Fundamentals` — if `fundamental` was dispatched. (Skip for crypto.)
   - `Technicals` — if `technical` was dispatched.
   - `News` — if `news` was dispatched.
   - `Macro` — if `macro` was dispatched.
   - `Risks` — always.
4. `emit_recommendation` — directional call + position-size range + entry zone + stop + 12-mo target.
5. `emit_disclaimer` — always.
6. `emit_done` — terminate.

Discipline:
- Every numeric in `emit_section` markdown must have a matching citation in the section call.
- Recommendation signal is one of: `tactical_buy`, `accumulate`, `hold`, `reduce`.
- `position_size_range` is a 2-element percentage tuple (e.g., [2, 4]).
- For crypto: position-size range should be tighter (e.g., [0, 2]) and entry/stop/target should reflect crypto volatility.
- Do NOT emit plain text. Tool calls only.
```

- [ ] **Step 5: Run, confirm new test passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_lead_banker_agent.py -v
```

Expected: 4 passed (3 existing + 1 new).

- [ ] **Step 6: Full suite green**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run ruff check . && uv run mypy app tests && uv run pytest -q
```

Expected: 83 passing (82 prior + 1 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/agents backend/app/prompts/lead_banker.md backend/tests/test_lead_banker_agent.py
git commit -m "feat(backend): Lead Banker threads market kwarg through all specialists

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Live E2E test on RELIANCE.NS

**Files:** none — verification only.

**Goal:** Confirm the live pipeline produces a working Indian deep-dive on RELIANCE.NS, with Indian rates in the Macro section and INR figures in the Fundamentals/stock card.

- [ ] **Step 1: Kill leftover dev servers, start fresh**

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null; sleep 1
```

Start backend in background:

```bash
cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1
```

Use `run_in_background: true`.

Start frontend in background:

```bash
cd /Users/rakhisinha/Stylobate/frontend && npm run dev
```

Use `run_in_background: true`.

- [ ] **Step 2: Wait + healthz**

```bash
sleep 10
curl -s http://localhost:8000/healthz
```

Expected: `{"status":"ok","service":"stylobate-backend"}`.

- [ ] **Step 3: Get a fresh JWT**

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "$TOKEN" > /tmp/stylobate_p2c_token.txt
test -n "$TOKEN" && echo "got token" || { echo "FAILED"; exit 1; }
```

- [ ] **Step 4: Deep dive on RELIANCE.NS**

```bash
TOKEN=$(cat /tmp/stylobate_p2c_token.txt)
time curl -s -X POST "http://localhost:8000/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"deep dive on Reliance Industries (NSE)"}' \
  --max-time 240 > /tmp/reliance_p2c.txt
echo "=== bytes: $(wc -c < /tmp/reliance_p2c.txt) ==="
echo "=== resolved ticker (from progress events) ==="
grep -E "RELIANCE|ticker.*NS|market.*IN" /tmp/reliance_p2c.txt | head -3
echo "=== section titles ==="
grep -E '"title":' /tmp/reliance_p2c.txt | sort -u
echo "=== currency in stock card ==="
grep -oE '"currency":\s*"[A-Z]+"' /tmp/reliance_p2c.txt | head -3
echo "=== Indian macro markers (regex for repo rate, INR figures, etc.) ==="
grep -iE "(repo|inr|nse|g-sec|crore|lakh)" /tmp/reliance_p2c.txt | head -5
echo "=== unique events + done count ==="
grep "^event:" /tmp/reliance_p2c.txt | sort -u
echo "done count: $(grep -c '^event: done' /tmp/reliance_p2c.txt)"
```

**Expected:**
- Section count ≥ 6 (Thesis, Fundamentals, Technicals, News, Macro, Risks)
- `currency` in stock card is `INR`
- Macro section references Indian rates (e.g., "repo at X%", "10Y G-Sec at Y%")
- `event: done` count = 1

If the ticker resolves to the wrong market or yfinance returns empty financials for RELIANCE.NS, that gets surfaced in the model's output — note for analysis.

- [ ] **Step 5: Stop servers**

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null
```

## Report

Summarize: did all 6 sections appear? Currency INR? Indian macro present?

---

## Task 7: Live E2E test on BTC

**Files:** none — verification only.

**Goal:** Confirm the live pipeline produces a crypto-appropriate deep-dive: Fundamentals section short-circuits with a graceful "no statements" note OR is omitted entirely; Technicals, News, Macro all appear; recommendation/disclaimer/done as usual.

- [ ] **Step 1: Restart servers (same as Task 6 Step 1)**

If you stopped them in Task 6 Step 5, restart now.

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null; sleep 1
```

Start backend (run_in_background): `cd /Users/rakhisinha/Stylobate/backend && uv run uvicorn app.main:app --port 8000 --host 127.0.0.1`

Start frontend (run_in_background): `cd /Users/rakhisinha/Stylobate/frontend && npm run dev`

```bash
sleep 10
curl -s http://localhost:8000/healthz
```

- [ ] **Step 2: Reuse JWT or get a fresh one**

If `/tmp/stylobate_p2c_token.txt` from Task 6 is still recent (< 1 hour), reuse it. Otherwise:

```bash
SUPABASE_URL=https://pvjamgocmmldfpzzswaj.supabase.co
ANON_KEY=$(grep "^NEXT_PUBLIC_SUPABASE_ANON_KEY=" /Users/rakhisinha/Stylobate/frontend/.env.local | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"rakhisinha100896@gmail.com","password":"Stylobate2026!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
echo "$TOKEN" > /tmp/stylobate_p2c_token.txt
```

- [ ] **Step 3: Deep dive on BTC**

```bash
TOKEN=$(cat /tmp/stylobate_p2c_token.txt)
time curl -s -X POST "http://localhost:8000/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"deep dive on BTC"}' \
  --max-time 240 > /tmp/btc_p2c.txt
echo "=== bytes: $(wc -c < /tmp/btc_p2c.txt) ==="
echo "=== resolved (look for market=CRYPTO) ==="
grep -iE "(BTC|crypto|market.*crypto)" /tmp/btc_p2c.txt | head -3
echo "=== section titles ==="
grep -E '"title":' /tmp/btc_p2c.txt | sort -u
echo "=== currency in stock card ==="
grep -oE '"currency":\s*"[A-Z]+"' /tmp/btc_p2c.txt | head -1
echo "=== Fundamentals handling (should be skipped or graceful) ==="
grep -A 2 '"title": "Fundamentals"' /tmp/btc_p2c.txt | head -10
echo "=== unique events + done count ==="
grep "^event:" /tmp/btc_p2c.txt | sort -u
echo "done count: $(grep -c '^event: done' /tmp/btc_p2c.txt)"
```

**Expected:**
- Section count ≥ 4 (Thesis, Technicals, News, Macro, Risks — Fundamentals likely omitted or short)
- Currency = USD
- Either: no "Fundamentals" section, OR the Fundamentals section says "not applicable to crypto"
- `event: done` count = 1

- [ ] **Step 4: Stop servers**

```bash
pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "next dev" 2>/dev/null
```

## Report

Summarize: did BTC deep-dive work? Did Fundamentals get gracefully handled (omitted or short-circuited)?

---

## End-of-Phase 2C checklist

- [ ] Backend tests: 83 passing (`uv run pytest -q`)
- [ ] mypy strict + ruff clean
- [ ] Live deep-dive on **AAPL** produces 6 sections with US data (regression check)
- [ ] Live deep-dive on **RELIANCE.NS** produces 6 sections with INR currency and Indian macro rates
- [ ] Live deep-dive on **BTC** produces ≥4 sections (Fundamentals omitted/graceful), USD currency
- [ ] Render + Vercel auto-deploys go green on `main`
- [ ] `https://stylobate.vercel.app/chat` works for all three tickers

**Phase 2 is then complete per the spec acceptance criterion** ("deep-dive works for AAPL, RELIANCE.NS, BTC"). Phase 3 (Portfolio & Risk) is the next major plan.
