# Stylobate — Phase 3C: Risk Manager Agent Design

> **For agentic workers:** This is a sub-phase spec. After approval, invoke `superpowers:writing-plans` to produce the implementation plan at `docs/superpowers/plans/`.

**Parent spec:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` (§4.3 decision specialists, §5.2 Risk Manager tools, §15 Phase 3 rollout).

**Predecessor specs:**
- `docs/superpowers/specs/2026-05-15-stylobate-phase-3a-portfolio-crud-design.md` (CRUD + UI shipped).
- `docs/superpowers/specs/2026-05-15-stylobate-phase-3b-portfolio-strategist-design.md` (Portfolio Strategist shipped — DB-reading + per-cohort math infrastructure that 3C extends).

**Goal:** Build the Risk Manager — a second Sonnet specialist that runs alongside the Portfolio Strategist in every portfolio analysis. Covers concentration check, historical VaR per cohort, pairwise correlations, and three pre-canned stress tests. Completes Phase 3 of the spec ("user can input holdings and get a health check").

**Architecture:** Risk Manager mirrors the Portfolio Strategist pattern — Sonnet agent, closure-bound `user_id` in tool factory, pure-math module. Lead Banker's `portfolio_mode` becomes parallel-aware: a single `dispatch_portfolio_analysts` tool runs both specialists via `asyncio.gather` and returns both findings to the LLM for combined synthesis. No new routes; no frontend changes.

**Tech Stack:** Existing — FastAPI, Pydantic v2, asyncpg, Anthropic Sonnet 4.6, yfinance, cache_kv. Reuses Phase 3B's `app/data/portfolio_stats.py`, `app/data/benchmarks.py`, `app/data/fx.py`, `app/tools/portfolio.py` for shared DB reads. No new external deps.

**Out of scope (deferred):**
- Sector concentration (yfinance.info.sector data is unreliable for INR/crypto). Add when better sector taxonomy exists.
- Single-issuer concentration (GOOG + GOOGL aggregation). Needs issuer-mapping table.
- Parametric VaR (mean + 1.645σ). Only historical VaR in 3C.
- User-defined stress scenarios. Pre-canned only.
- Risk-adjusted recommendations (e.g., "shift X% from crypto to USD-equity to reduce VaR by Y%"). Lead Banker can suggest qualitatively; auto-optimization is Phase 4+.

---

## 1. Architecture overview

```
/chat/stream  (portfolio query detected)
  → Lead Banker portfolio_mode
    → dispatch_portfolio_analysts tool
      → asyncio.gather(
          run_portfolio_strategist(user_id, ...),     # existing (3B)
          run_risk_manager(user_id, ...),             # NEW (3C)
        )
      → returns {strategist: {...}, risk: {...}}
    → Lead Banker synthesizes both into ~6-8 emit_section calls
```

Both agents:
- Capture `user_id` (+ optional `portfolio_id`) in closures at construction time.
- Read holdings via `app/db/portfolios.py` (Phase 3A) through `app/tools/portfolio.py`'s `_load_holdings` helper (Phase 3B).
- Pull live prices via `app/data/prices_cache.py` (Phase 3A).
- Pull benchmark/FX history via `app/data/benchmarks.py` + `app/data/fx.py` (Phase 3B).

Risk Manager's own tools (`app/tools/risk.py`) build on these — minimal additional adapter code.

The Lead Banker prompt is updated so the portfolio_mode section describes:
- New tool: `dispatch_portfolio_analysts(specialists=["strategist", "risk"])`.
- The old `dispatch_portfolio_strategist` is REMOVED.
- New section ordering for synthesis (8 sections: Snapshot, Returns, Risk Metrics, Concentration, VaR, Correlations, Stress Tests, Rebalance Plan optional + closing).

3B's test for Lead Banker portfolio_mode (`tests/test_lead_banker_portfolio_mode.py`) must be updated — its mock now responds with the new tool name.

## 2. Backend surface

### New files

```
backend/app/agents/risk_manager.py           NEW — Sonnet agent
backend/app/tools/risk.py                    NEW — 4 closure-bound Tool wrappers
backend/app/data/risk_stats.py               NEW — pure math
backend/app/models/risk_manager.py           NEW — Pydantic models
backend/app/prompts/risk_manager.md          NEW — agent system prompt
```

### Modified files

```
backend/app/agents/lead_banker.py            MODIFY — replace dispatch_portfolio_strategist
                                              with dispatch_portfolio_analysts; parallel gather
backend/app/prompts/lead_banker.md           MODIFY — portfolio_mode prompt with new sections
backend/app/routes/chat_portfolio.py         MODIFY — direct endpoint also runs both specialists
                                              in parallel, includes risk sections in renderer
backend/tests/test_lead_banker_portfolio_mode.py  MODIFY — mock the new tool name
```

### No new routes

Both routes from 3B (`/chat/stream` keyword-detected and `/chat/portfolio` direct) continue to work. The direct endpoint also runs the Risk Manager in parallel and includes the new sections in its renderer.

## 3. Data model

### Risk findings

```python
class ConcentrationFlag(BaseModel):
    position_id: UUID
    ticker: str
    weight_pct: float                            # weight in WHOLE portfolio (USD-equivalent)
    threshold_pct: float                         # the rule that was tripped
    severity: Literal["warn", "critical"]        # warn 10-20%; critical >20%


class CohortVaR(BaseModel):
    cohort: tuple[str, Literal["equity_etf", "crypto"]]
    confidence: float                            # 0.95
    horizon_days: int                            # 10
    var_pct: float | None                        # -value, e.g., -0.07 = -7%
    var_native: float | None                     # absolute amount in cohort's currency
    methodology: Literal["historical"]
    insufficient_history: bool                   # True if < 100 days


class CorrelationMatrix(BaseModel):
    tickers: list[str]
    matrix: list[list[float]]                    # NxN Pearson coefficients
    excluded: list[str]                          # positions skipped due to insufficient history


class StressResult(BaseModel):
    scenario: Literal["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"]
    per_position: list["PositionStressDelta"]
    by_cohort: list["CohortStressDelta"]
    total_delta_usd: float                       # cross-cohort sum via USDINR
    assumed_fx: dict[str, float]                 # e.g., {"USDINR": 83.50}


class PositionStressDelta(BaseModel):
    ticker: str
    before_native: float
    after_native: float
    delta_native: float                          # signed; negative = loss
    delta_pct: float


class CohortStressDelta(BaseModel):
    cohort: tuple[str, Literal["equity_etf", "crypto"]]
    before_native: float
    after_native: float
    delta_native: float


class RiskFindings(BaseModel):
    portfolio_id: UUID | None
    portfolio_name: str
    concentration: list[ConcentrationFlag]       # empty if no flags
    var_by_cohort: list[CohortVaR]
    correlations: CorrelationMatrix | None       # None if <2 positions or all excluded
    stress_results: list[StressResult]           # always 3 scenarios run
    notes: list[str]                             # warnings: missing FX, insufficient history, etc.
    citations: list["Citation"]
    confidence: float
```

### Tool schemas

```python
concentration_check_tool: Tool(
    name="concentration_check",
    description=(
        "Flag positions whose weight in the whole portfolio (USD-equivalent across cohorts) "
        "exceeds 10%. Returns severity: 'warn' (10-20%) or 'critical' (>20%). "
        "Cross-cohort comparison uses USDINR FX. Empty list if no flags."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
)

calc_var_tool: Tool(
    name="calc_var",
    description=(
        "Compute historical Value-at-Risk per cohort at given confidence + horizon. "
        "Returns var_pct (negative; e.g., -0.07 = max expected 10-day loss is 7% at 95% conf) "
        "and var_native (absolute amount in cohort's currency). Requires ≥100 daily closes "
        "per position; cohorts with insufficient history return None."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "default": 0.95},
            "horizon_days": {"type": "integer", "default": 10},
        },
        "required": [],
    },
)

get_correlations_tool: Tool(
    name="get_correlations",
    description=(
        "Pairwise Pearson correlation matrix on daily log-returns over 1y. "
        "Positions with <100 days of overlap are excluded. Returns ticker list + NxN matrix."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
)

stress_test_tool: Tool(
    name="stress_test",
    description=(
        "Apply a pre-canned stress scenario to the portfolio. Returns per-position deltas "
        "in native currency, cohort totals, and a USD-equivalent total via USDINR. "
        "Scenarios: 'rates_+200bps' (equity -2%, crypto -5%), 'equity_-20%' (equity -20%, "
        "crypto -30%), 'inr_depreciation_-10%' (INR positions lose 10% USD-equivalent; "
        "native value unchanged)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scenario": {
                "type": "string",
                "enum": ["rates_+200bps", "equity_-20%", "inr_depreciation_-10%"],
            },
        },
        "required": ["scenario"],
    },
)
```

## 4. Risk math (`app/data/risk_stats.py`)

Pure module. No I/O. Inputs are dict-shaped (positions + price_history + optional fx_rate).

### concentration_check

```python
def concentration_check(
    positions: list[dict[str, Any]], usdinr: float | None,
) -> list[dict[str, Any]]:
    """Flag positions whose USD-equivalent weight > 10%.

    Cross-cohort: convert each position to USD using usdinr (INR positions only).
    If usdinr is None, fall back to per-cohort weights and add a note.
    """
    # For each position, compute value_usd_equivalent
    # Compute total_usd; weight = value_usd / total_usd
    # Flag if weight > 0.10 with severity "warn" (10-20%) or "critical" (>20%)
```

### historical_var

```python
def historical_var(
    cohort_positions: list[dict[str, Any]],
    price_history: dict[str, list[tuple[date, float]]],
    confidence: float = 0.95,
    horizon_days: int = 10,
) -> dict[str, Any]:
    """Historical VaR for a single cohort.

    1. Build portfolio_value_series for the cohort (qty * price summed across positions on common dates).
    2. Compute daily log-returns.
    3. Compute horizon_days-day rolling cumulative returns: r_h[t] = sum(r[t-h+1..t]).
    4. Sort ascending; pick the (1 - confidence) percentile.
    5. var_pct = exp(percentile) - 1 (negative number).
    6. var_native = var_pct * current_cohort_value.

    Return {var_pct, var_native, insufficient_history: bool}.
    insufficient_history = True if len(daily) < 100.
    """
```

### pairwise_correlations

```python
def pairwise_correlations(
    positions: list[dict[str, Any]],
    price_history: dict[str, list[tuple[date, float]]],
    min_overlap: int = 100,
) -> dict[str, Any]:
    """Pairwise Pearson correlation on daily log-returns.

    For each pair (i, j): inner-join their date series, compute returns,
    require >= min_overlap aligned dates, compute Pearson r.
    Excluded positions: those that fail min_overlap with EVERY other position.

    Return {tickers: included_tickers, matrix: NxN, excluded: list[str]}.
    """
```

### stress_test

```python
SHOCK_BY_SCENARIO: dict[str, dict[str, float]] = {
    "rates_+200bps": {"equity_etf": -0.02, "crypto": -0.05},
    "equity_-20%":    {"equity_etf": -0.20, "crypto": -0.30},
    "inr_depreciation_-10%": {},  # special: handled via FX
}


def stress_test(
    positions: list[dict[str, Any]], scenario: str, usdinr: float | None,
) -> dict[str, Any]:
    """Apply scenario shocks. Returns per-position + by-cohort + total_usd."""
    # For "rates_+200bps" and "equity_-20%":
    #   shock = SHOCK_BY_SCENARIO[scenario][cohort.group]
    #   after = before * (1 + shock)
    # For "inr_depreciation_-10%":
    #   INR positions: native unchanged, but USD-equivalent drops 10%.
    #   total_delta_usd reflects this via the FX adjustment.
    # If usdinr is None and scenario is inr_depreciation_-10%, skip total_delta_usd
    # and add a note.
```

## 5. Risk Manager agent

System prompt (`app/prompts/risk_manager.md`):

```markdown
You are the Risk Manager inside Stylobate. Your job: surface concentration risks,
quantify potential downside (VaR + stress tests), and report cross-position
correlations. You do NOT recommend trades — that's the Portfolio Strategist's
domain. You report risks; the Lead Banker decides whether to suggest action.

You have 4 tools:

- `concentration_check()` — flag positions >10% of portfolio
- `calc_var(confidence?, horizon_days?)` — historical VaR per cohort
- `get_correlations()` — pairwise correlation matrix
- `stress_test(scenario)` — one of "rates_+200bps", "equity_-20%", "inr_depreciation_-10%"

Process:
1. Call `concentration_check` first (cheapest; informs everything else).
2. Call `calc_var()` with defaults (95% confidence, 10-day horizon).
3. Call `get_correlations()` once.
4. Call `stress_test(scenario=X)` THREE times — once per pre-canned scenario.
5. Call `submit_risk_findings` exactly once.

Discipline:
- Concentration severity: 10-20% = "warn", >20% = "critical".
- VaR is a probabilistic loss estimate, not a worst case. State the methodology
  in the findings.
- Correlation matrix is currency-agnostic (returns are unitless).
- Stress test scenarios are SIMPLIFIED: actual rate sensitivity varies by sector,
  not just asset class. State this caveat in `notes` when surfacing stress results.
- `notes` calls out: missing FX (concentration falls back to per-cohort), insufficient
  history (positions excluded from VaR/correlations), partial price data.
- `citations` reference "yfinance" for prices, "fred" for risk-free rate (unused in 3C
  but pattern for future risk-adjusted metrics).
- `confidence`: 0.85+ when concentration data + ≥3 stress results computed; 0.7 when
  some cohorts had insufficient history; <0.6 if many gaps.
```

Agent (`app/agents/risk_manager.py`):

```python
async def run_risk_manager(
    *,
    user_id: str,
    portfolio_id: str | None,
    brief: str,
    client: AsyncAnthropic | Any | None = None,
    tools_override: list[Tool] | None = None,
) -> dict[str, Any]:
    """8-turn Sonnet loop. Calls concentration_check → calc_var → get_correlations
    → stress_test×3 → submit_risk_findings. Returns the findings dict."""
```

Same shape as `run_portfolio_strategist` (3B): 8-turn bounded loop, `submit_risk_findings` is a marker tool with `impl=None`, fallback to a "did-not-submit" findings dict on loop exit.

## 6. Tools (`app/tools/risk.py`)

```python
def build_risk_tools(*, user_id: str, portfolio_id: str | None = None) -> list[Tool]:
    """Build 4 risk tools whose impls capture user_id + portfolio_id."""

    async def _shared_load() -> tuple[str | None, list[dict[str, Any]], float | None]:
        """Helper: get pid + positions-with-prices + usdinr (or None)."""
        from app.tools.portfolio import _holdings_with_prices
        from app.data.fx import get_usdinr
        pid, positions = await _holdings_with_prices(user_id, portfolio_id)
        usdinr = await get_usdinr() if positions else None
        return pid, positions, usdinr

    async def _concentration_check(**_kwargs):
        from app.data.risk_stats import concentration_check
        pid, positions, usdinr = await _shared_load()
        flags = concentration_check(positions, usdinr)
        return {"portfolio_id": pid, "flags": flags, "fx_used": usdinr}

    async def _calc_var(confidence=0.95, horizon_days=10, **_kwargs):
        from app.data.risk_stats import historical_var
        # Fetch price history per position via app/data/yfinance_adapter
        # Group positions by cohort_key; call historical_var per cohort
        ...

    async def _get_correlations(**_kwargs):
        from app.data.risk_stats import pairwise_correlations
        ...

    async def _stress_test(scenario, **_kwargs):
        from app.data.risk_stats import stress_test
        ...

    return [
        Tool(name="concentration_check", ..., impl=_concentration_check),
        Tool(name="calc_var", ..., impl=_calc_var),
        Tool(name="get_correlations", ..., impl=_get_correlations),
        Tool(name="stress_test", ..., impl=_stress_test),
    ]
```

The `_shared_load` helper avoids three separate DB roundtrips inside one agent run (each tool that needs holdings calls it; asyncpg pool handles the conn lifecycle cheaply).

## 7. Lead Banker integration

Replace `dispatch_portfolio_strategist_tool` (3B) with `dispatch_portfolio_analysts_tool`:

```python
dispatch_portfolio_analysts_tool = Tool(
    name="dispatch_portfolio_analysts",
    description=(
        "Run both portfolio analysts in parallel and return their combined findings. "
        "Specialists: 'strategist' (snapshot, returns, rebalance) and 'risk' "
        "(concentration, VaR, correlations, stress tests). Pass both unless the user "
        "explicitly asks for only one."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "items": {"type": "string", "enum": ["strategist", "risk"]},
                "default": ["strategist", "risk"],
            },
            "brief": {"type": "string"},
            "target_alloc": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["brief"],
    },
    impl=None,  # type: ignore[arg-type]
)
```

The dispatch handler in `run_lead_banker` (portfolio_mode branch):

```python
if name == "dispatch_portfolio_analysts":
    if not portfolio_mode or user_id is None:
        result = {"error": "..."}
    else:
        specialists = args.get("specialists", ["strategist", "risk"])
        brief = args.get("brief", "...")
        target_alloc = args.get("target_alloc")
        coros = []
        names = []
        if "strategist" in specialists:
            coros.append(run_portfolio_strategist(
                user_id=user_id, portfolio_id=None, brief=brief,
                target_alloc=target_alloc, client=client,
            ))
            names.append("strategist")
        if "risk" in specialists:
            coros.append(run_risk_manager(
                user_id=user_id, portfolio_id=None, brief=brief, client=client,
            ))
            names.append("risk")
        results = await asyncio.gather(*coros, return_exceptions=True)
        result = {}
        for n, r in zip(names, results, strict=True):
            if isinstance(r, BaseException):
                result[n] = {"error": str(r)}
            else:
                result[n] = r
    tool_results.append({...})
```

The `dispatch_portfolio_strategist_tool` is **removed** entirely. The 3B test `test_lead_banker_portfolio_mode_calls_strategist_not_specialists` is updated to mock the new tool name and verify both specialists were called.

Prompt update (`app/prompts/lead_banker.md` portfolio_mode section):

```markdown
In portfolio mode, you have ONE dispatch tool: `dispatch_portfolio_analysts`.
Call it once with `specialists: ["strategist", "risk"]` (the default).
You'll receive a dict with both `strategist` and `risk` findings.

Synthesis order:
1. emit_quick_take — one sentence
2. emit_section "Portfolio Snapshot" — strategist.cohorts (weights, totals, gain%)
3. emit_section "Returns vs Benchmark" — strategist.cohorts (1mo/3mo/1y vs benchmark)
4. emit_section "Risk Metrics" — strategist.cohorts (sharpe, beta, max drawdown)
5. emit_section "Concentration" — risk.concentration (only if any flags)
6. emit_section "Value at Risk" — risk.var_by_cohort
7. emit_section "Correlations" — risk.correlations (only if matrix is non-trivial; ≥2 positions)
8. emit_section "Stress Tests" — risk.stress_results (all 3 scenarios)
9. emit_section "Rebalance Plan" — strategist.rebalance (only if user asked)
10. emit_section "Risks" — combine concentration warnings + missing-price notes + risk.notes + strategist.notes
11. emit_recommendation — only if user asked for rebalance
12. emit_disclaimer
13. emit_done

If a specialist returned an `error` field, surface it in "Risks" rather than skipping the section. The portfolio analysis succeeds if AT LEAST ONE specialist returned findings.
```

## 8. `/chat/portfolio` direct endpoint

The dedicated endpoint (`app/routes/chat_portfolio.py` from 3B) is extended to:
1. Run both `run_portfolio_strategist` AND `run_risk_manager` in parallel via `asyncio.gather`.
2. The renderer (`_stream_findings`) emits the 8 sections from the combined findings.

New helper renderers in the same file:
- `_render_concentration_section(flags)` — bullet list of flagged positions
- `_render_var_section(var_by_cohort)` — per-cohort VaR table
- `_render_correlations_section(matrix)` — top-3 highest absolute correlations as bullets (full matrix is noisy in plain text)
- `_render_stress_tests_section(stress_results)` — scenario name + total impact + worst-hit position per scenario

## 9. Error handling

| Failure | Behaviour |
|---|---|
| No portfolios | Strategist + Risk both short-circuit; combined output is one "create a portfolio" message. |
| Empty portfolio | Same as above. |
| FX (USDINR) unavailable | concentration_check falls back to per-cohort weights; stress_test inr_depreciation skips total_delta_usd; both surface a note. |
| <100 days history for a position | Position excluded from correlations + VaR; surfaced in `notes`. |
| All positions missing history | VaR returns None per cohort + `insufficient_history=true`; correlation returns empty. |
| Strategist OR Risk agent raises | Lead Banker `asyncio.gather(return_exceptions=True)` captures it; the surviving specialist's output still ships; the failed one becomes an `error` field surfaced in the "Risks" section. |
| stress_test gets unknown scenario | Tool raises ValueError; agent catches and submits findings with stress_results=[] + a note. |

## 10. Performance + cost

- 2 Sonnet calls per portfolio query instead of 1 (3B was 1). Parallel via `asyncio.gather` — wall-clock is max of the two, not sum.
- Per-call cost (warm cache): ~$0.02-0.04 Sonnet each, so $0.04-0.08 total.
- Per-call cost (cold cache): ~$0.05-0.10 each.
- Latency target (warm): < 12s p50.

## 11. Testing

| File | What |
|---|---|
| `test_data_risk_stats.py` | Pure math: concentration thresholds, VaR percentile sorting, correlation symmetry, stress scenario shock magnitudes. ~10 tests on fixture data. |
| `test_tools_risk.py` | 4 tool impls; closure-bound user_id verified; FX-missing fallback; insufficient-history handling. ~6 tests. |
| `test_risk_manager_agent.py` | Sonnet loop: tools called in expected order, findings shape correct, short-circuit on no portfolios. ~3 tests. |
| `test_lead_banker_portfolio_mode.py` | MODIFY — mock `dispatch_portfolio_analysts` instead of `dispatch_portfolio_strategist`; assert both specialists invoked; tolerate `error` field if one fails. |
| `test_routes_chat_portfolio_endpoint.py` | MODIFY — fixture findings include both strategist + risk results; assert new sections in SSE body. |
| `test_routes_e2e_portfolio_analysis.py` | MODIFY — mock both specialists' Sonnet responses; assert combined sections in SSE body. |

Phase 3C end-state: ~175 backend tests (155 today + ~20 new). No new Playwright tests (the analyze flow from 3B exercises the combined path naturally — the new sections appear in the chat view).

## 12. File map (preview)

```
backend/
  app/
    agents/
      risk_manager.py             NEW
      lead_banker.py              MODIFY
    tools/
      risk.py                     NEW
    data/
      risk_stats.py               NEW
    models/
      risk_manager.py             NEW
    routes/
      chat_portfolio.py           MODIFY
    prompts/
      risk_manager.md             NEW
      lead_banker.md              MODIFY
  tests/
    test_data_risk_stats.py       NEW
    test_tools_risk.py            NEW
    test_risk_manager_agent.py    NEW
    test_lead_banker_portfolio_mode.py  MODIFY
    test_routes_chat_portfolio_endpoint.py  MODIFY
    test_routes_e2e_portfolio_analysis.py   MODIFY
```

## 13. End-of-3C acceptance

- ~175 backend tests pass; ruff + mypy strict clean.
- All 4 Playwright tests still pass (the analyze flow now also renders Concentration / VaR / Stress sections).
- Live UAT (deferred to when DB password is restored): a portfolio with 3 cohorts produces 8 sections, includes at least one stress scenario delta, correlations matrix shown if ≥2 positions.
- Render + Vercel auto-deploys go green on `main`.

**Phase 3 of the parent spec ("user can input holdings and get a health check") is then COMPLETE.** Phase 4 (Screener) is the next major plan.
