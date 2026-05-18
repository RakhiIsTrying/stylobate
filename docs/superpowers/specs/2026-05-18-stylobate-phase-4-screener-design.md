# Stylobate — Phase 4: Screener & Ideas Design

> **For agentic workers:** This is a phase spec. After approval, invoke `superpowers:writing-plans` to produce the implementation plan at `docs/superpowers/plans/`.

**Parent spec:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` (§4.3 decision specialists, §5.2 Screener tools, §15 Phase 4 rollout).

**Predecessor phases:** Phases 0/1/2 (deep-dive) + Phase 3A/3B/3C (portfolio + risk) shipped. Phase 4 adds idea generation as a parallel chat workflow.

**Goal:** Build the Screener — a Sonnet specialist that turns natural-language requests like "AI infrastructure plays in India" or "find me cheap US dividend stocks" into a typed shortlist of candidate tickers. Backed by curated universe constituent lists (S&P 500, Nifty 500, CoinGecko top 100) with grounded LLM theme-matching (the LLM picks from KNOWN tickers — no hallucinations).

**Architecture:** A new Sonnet agent (`run_screener`) parallel to `run_portfolio_strategist` (3B) and `run_risk_manager` (3C). Triggered by `/chat/stream` keyword detection ("screen", "find me", "best stocks for", "plays in", "ideas for") that routes to a dedicated `/chat/screen` SSE handler. Same event shape as `/chat/portfolio` so the existing chat UI renders without changes. No frontend additions.

**Tech Stack:** Existing — FastAPI, Pydantic v2, Anthropic Sonnet 4.6, yfinance, cache_kv. One new free external dependency: **CoinGecko public API** (no auth required, 30 req/min limit, used once per crypto query and cached 24h).

**Out of scope (deferred):**
- Real-time fundamentals refresh job (cron / Render Worker pre-fetching SP500 fundamentals nightly). Phase 4 fetches yfinance live on the post-theme shortlist only.
- Custom user-defined universes ("my watchlist as a universe"). Reuse the Phase 3A watchlist for that later.
- Cross-universe filtering by language/locale, geographic exposure, ESG scores. Phase 4 covers market_cap / PE / ROE / sector.
- Smaller / mid / micro-cap universes (Russell 3000, BSE 500). Three universes in MVP.
- Dedicated `/screener` page with filter form. Pure chat-first.

---

## 1. Architecture overview

```
/chat/stream (user message)
  → is_portfolio_query? → existing portfolio path (Phase 3B/3C)
  → is_screener_query?  → NEW screener path
  → else                → ticker deep-dive (Phase 2)


Screener path:
  → run_screener(user_message, user_id?, client)
    → Sonnet 8-turn loop:
        1. resolve_universe()       — agent picks from {sp500, nifty500, crypto}
        2. theme_to_universe()      — agent narrows to ~20-50 candidates
        3. screen_stocks()          — optional structured filter (live yfinance)
        4. submit_screener_findings — terminal
    → returns ScreenerFindings dict
  → SSE stream: progress → quick_take → section(Theme) → section(Candidates)
                → section(Filters Applied) → section(Notes) → disclaimer → done
```

The agent runs WITHOUT Lead Banker — its output is self-contained (no need to mix with Phase 2/3 specialists). user_id is closure-passed (optional — the Screener doesn't read the user's portfolio in MVP, but Phase 4+ could add "compare candidates to my holdings").

## 2. Backend surface

### New files

```
backend/data/universes/sp500.json              NEW — committed constituent list
backend/data/universes/nifty500.json           NEW — committed constituent list
backend/scripts/build_universes.py             NEW — one-time bootstrap to fetch initial JSONs

backend/app/data/universes.py                  NEW — load JSONs + CoinGecko fetcher (cache_kv 24h)
backend/app/agents/screener.py                 NEW — Sonnet agent
backend/app/tools/screener.py                  NEW — 4 closure-bound Tool wrappers
backend/app/models/screener.py                 NEW — Pydantic models
backend/app/prompts/screener.md                NEW — agent system prompt

backend/app/routes/chat_screen.py              NEW — /chat/screen direct SSE endpoint
backend/app/routes/chat.py                     MODIFY — add screener-keyword detection that
                                                routes to a _screener_stream() helper
backend/app/main.py                            MODIFY — register chat_screen router
```

### Modified files (testing)

```
backend/tests/test_data_universes.py                  NEW
backend/tests/test_tools_screener.py                  NEW
backend/tests/test_screener_agent.py                  NEW
backend/tests/test_routes_chat_screen_endpoint.py     NEW
backend/tests/test_routes_chat_stream_screener.py     NEW — chat.py routing
```

### Routes

| Method | Path | Behaviour |
|---|---|---|
| POST | `/chat/stream` | unchanged shape; new screener keyword detection routes via `_screener_stream()` |
| POST | `/chat/screen` | NEW. Body `{message: str}`. Goes direct to `run_screener`. Same SSE shape as `/chat/portfolio`. |

Auth on both via existing `Depends(get_current_user)`. user_id is read but Phase 4 doesn't gate behavior on it (anyone with valid auth can run a screener).

## 3. Universe data shape

### Hardcoded JSONs

```json
// backend/data/universes/sp500.json
[
  {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology", "currency": "USD", "asset_class": "equity", "market": "US"},
  {"ticker": "MSFT", "name": "Microsoft Corporation", "sector": "Information Technology", "currency": "USD", "asset_class": "equity", "market": "US"},
  ...
]
```

### CoinGecko (live)

Endpoint: `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1`. Free, no auth, 30 req/min.

Cached in `cache_kv` with key `universe:crypto:top100` and 24h TTL. Result shape normalized to match the JSON:

```python
[
  {"ticker": "BTC", "name": "Bitcoin", "sector": null, "currency": "USD", "asset_class": "crypto", "market": "CRYPTO", "market_cap": 1500000000000},
  ...
]
```

(`market_cap` only present for crypto since CoinGecko returns it; not present in the hardcoded JSONs to avoid drift.)

### Bootstrapping JSONs

A one-time script `backend/scripts/build_universes.py` fetches Wikipedia tables via `pandas.read_html`:

- S&P 500: `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` — first table.
- Nifty 500: `https://en.wikipedia.org/wiki/NIFTY_500` — first table.

Output: writes `backend/data/universes/sp500.json` + `nifty500.json` and exits. Engineer runs this manually each quarter. The script is NOT part of the runtime path; production reads the committed JSONs.

The script is a "shipping artifact" — checked in but never imported by the app. `pandas` is a one-shot dev dep, NOT added to `pyproject.toml` runtime deps. Engineers install via `uv pip install pandas lxml` ad-hoc when refreshing.

## 4. Tools

```python
class Constituent(BaseModel):
    ticker: str
    name: str
    sector: str | None
    currency: str
    asset_class: Literal["equity", "etf", "crypto"]
    market: Literal["US", "IN", "CRYPTO"]


class Candidate(BaseModel):
    ticker: str
    name: str
    market: Literal["US", "IN", "CRYPTO"]
    currency: str
    sector: str | None
    current_price: float | None
    market_cap: float | None
    pe_ttm: float | None
    roe_pct: float | None
    revenue_growth_yoy: float | None


class ScreenerFindings(BaseModel):
    theme: str                                    # parsed from user message
    universes_used: list[str]                     # ["sp500", "nifty500"]
    candidates: list[Candidate]
    filters_applied: dict[str, Any]               # {"max_pe": 20, "min_roe": 15}
    notes: list[str]                              # dropped tickers, missing data, etc.
    citations: list["Citation"]                   # yfinance + CoinGecko
    confidence: float
```

### Tool schemas

```python
resolve_universe_tool: Tool(
    name="resolve_universe",
    description=(
        "Given the user's query, return which universe(s) to search and their "
        "constituent lists. Universes: 'sp500' (US large-caps), 'nifty500' (Indian "
        "equities), 'crypto' (top 100 by market cap). Pass an array to search multiple."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "universes": {
                "type": "array",
                "items": {"type": "string", "enum": ["sp500", "nifty500", "crypto"]},
                "minItems": 1,
            },
        },
        "required": ["universes"],
    },
)
# Returns: {universes: ["sp500"], constituents: [Constituent, ...]} — flat list across requested universes.

theme_to_universe_tool: Tool(
    name="theme_to_universe",
    description=(
        "Filter a candidate ticker list by theme. The LLM must select tickers ONLY "
        "from the provided list — invented tickers are dropped with a note. Aim for "
        "10-30 candidates that best match the theme."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "User's theme in their own words"},
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tickers the LLM picked from the constituent list",
            },
        },
        "required": ["theme", "tickers"],
    },
)
# Tool impl validates each ticker exists in the recently-loaded universe(s).
# Returns: {tickers: [valid_subset], dropped: [invented_tickers], notes: [...]}

screen_stocks_tool: Tool(
    name="screen_stocks",
    description=(
        "Apply structured filters to a ticker shortlist. Fetches live fundamentals "
        "from yfinance for each ticker. Filters: min_market_cap (USD-equivalent for crypto), "
        "max_pe (PE TTM), min_roe (percent), sectors (allowlist of sector strings)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}},
            "min_market_cap": {"type": ["number", "null"]},
            "max_pe": {"type": ["number", "null"]},
            "min_roe": {"type": ["number", "null"]},
            "sectors": {"type": ["array", "null"], "items": {"type": "string"}},
        },
        "required": ["tickers"],
    },
)
# Returns: {candidates: [Candidate], dropped: [{ticker, reason}], notes: [...]}

submit_screener_findings_tool: Tool(
    name="submit_screener_findings",
    input_schema={
        "type": "object",
        "properties": {
            "theme": {"type": "string"},
            "universes_used": {"type": "array", "items": {"type": "string"}},
            "candidates": {"type": "array"},
            "filters_applied": {"type": "object"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array"},
            "confidence": {"type": "number"},
        },
        "required": ["theme", "universes_used", "candidates", "notes", "confidence"],
    },
    impl=None,  # type: ignore[arg-type]  # marker
)
```

### Tool factory

```python
def build_screener_tools(*, user_id: str | None = None) -> list[Tool]:
    """Phase 4 doesn't read user-specific data, but the factory matches the
    Phase 3B/3C pattern for future portfolio-aware screening."""
```

`user_id` is optional in v1 — Phase 4 doesn't read holdings. Pass `None` from the route handler.

## 5. Agent (`app/agents/screener.py`)

System prompt (`app/prompts/screener.md`):

```markdown
You are the Screener inside Stylobate. Your job: turn a user's natural-language idea ("AI infrastructure plays in India", "cheap US dividend stocks", "blue-chip Indian tech") into a typed shortlist of candidate tickers. You do NOT recommend trades — you surface candidates. The user decides whether to deep-dive any of them via "tell me about <ticker>".

Tools:
- `resolve_universe(universes: list[str])` — load constituent lists
- `theme_to_universe(theme, tickers)` — narrow by theme (you pick the tickers from the loaded constituent list)
- `screen_stocks(tickers, filters)` — apply numeric/sector filters with live yfinance fundamentals
- `submit_screener_findings(...)` — terminal

Process:
1. Read the user's query. Identify:
   - Universes: US-only / Indian-only / crypto / multi.
   - Theme: the topical filter (sectors, business model, exposure).
   - Numeric/sector filters: "cheap" → max_pe, "large-cap" → min_market_cap, "growth" → ignore P/E.
2. Call `resolve_universe(universes=[...])` ONCE — get the constituents.
3. Call `theme_to_universe(theme, tickers=[...])` — pass tickers YOU picked from the constituents list that match the theme. Aim for 15-30 candidates.
4. Optionally call `screen_stocks(...)` if the user mentioned numeric criteria. Skip if the query is pure theme matching.
5. Call `submit_screener_findings` once with the final candidate list, filters_applied, theme summary, and notes.

Discipline:
- NEVER invent tickers. Only use tickers returned by `resolve_universe`.
- Cap output at 20 candidates. Reduce further if the user asked for "top 5" / "best 10".
- For Indian themes ("plays in India", "Indian X"), default universe is `nifty500`.
- For global themes, search both `sp500` and `nifty500`.
- For crypto themes, use `crypto`.
- If the theme is too broad (e.g., "stocks"), surface a polite note and ask the user to narrow.
- `confidence`: 0.85+ when both universe + theme are clear; 0.6-0.8 when theme is fuzzy ("growthy"); <0.5 if you couldn't find ≥3 candidates.
```

Agent (`run_screener`) mirrors `run_portfolio_strategist`: 8-turn bounded loop, submit-marker pattern, fallback findings dict on loop exhaustion. user_id is captured in closure (passed to tools as `**_kwargs`-absorbed arg; v1 ignores it). Test pattern matches Phase 3B/3C.

## 6. Universes data layer (`app/data/universes.py`)

```python
async def load_universe(name: str) -> list[Constituent]:
    """name ∈ {'sp500', 'nifty500', 'crypto'}. Raises ValueError on unknown."""
    if name == "crypto":
        return await _load_crypto_top100()
    if name == "sp500":
        return _load_static_universe("sp500.json")
    if name == "nifty500":
        return _load_static_universe("nifty500.json")
    raise ValueError(f"unknown universe: {name}")


def _load_static_universe(filename: str) -> list[Constituent]:
    path = Path(__file__).parent.parent.parent / "data" / "universes" / filename
    with path.open() as f:
        rows = json.load(f)
    return [Constituent(**r) for r in rows]


async def _load_crypto_top100() -> list[Constituent]:
    """Fetch from CoinGecko, cache 24h in cache_kv."""
    # cache_kv key: 'universe:crypto:top100'
    # on miss, hit https://api.coingecko.com/api/v3/coins/markets?...
    # normalize to Constituent shape (symbol.upper() -> ticker; name as-is;
    # currency='USD'; asset_class='crypto'; market='CRYPTO')
```

The static JSONs ship in `backend/data/universes/`. They are READ at runtime (committed assets) but NEVER WRITTEN by the running app — the bootstrap script is dev-time only.

## 7. Keyword detection in `/chat/stream`

Extend the existing helper:

```python
SCREENER_KEYWORDS = (
    "screen for", "screen me", "find me", "find some",
    "best stocks", "best names", "ideas for",
    "plays in", "plays for", "plays on",
    "what are good", "show me some", "top picks",
    "candidates for", "names in",
)


def is_screener_query(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SCREENER_KEYWORDS)
```

The chat route checks `is_screener_query` BEFORE `is_portfolio_query` BEFORE the ticker deep-dive path. This ordering matters because "screen my portfolio for high-PE names" would match both — screener wins (more specific).

Actually, the simpler rule: **screener queries don't contain "my" + "portfolio"**. So `is_portfolio_query` should win when both match. We order checks as: portfolio → screener → deep-dive.

```python
if is_portfolio_query(req.content):
    return _portfolio_stream(...)
if is_screener_query(req.content):
    return _screener_stream(...)
# else ticker resolve path
```

## 8. `/chat/screen` direct endpoint

Mirrors `/chat/portfolio` (3B). Body: `{message: str}`. Calls `run_screener` directly, streams findings as SSE sections via a renderer file:

```python
def _render_theme_section(theme: str, universes: list[str]) -> str
def _render_candidates_section(candidates: list[Candidate]) -> str  # markdown table
def _render_filters_section(filters: dict[str, Any]) -> str
def _render_notes_section(notes: list[str]) -> str
```

The candidates table:

```markdown
| Ticker | Name | Sector | Price | Market Cap | P/E | ROE |
|---|---|---|---|---|---|---|
| TATAELXSI.NS | Tata Elxsi | IT Services | ₹7,150 | ₹450 Cr | 62.1 | 38.4% |
| PERSISTENT.NS | Persistent Systems | IT Services | ₹5,820 | ₹880 Cr | 51.8 | 28.7% |
...
```

For crypto, the table omits PE/ROE columns and shows market cap in USD.

## 9. Error handling

| Failure | Behaviour |
|---|---|
| User's query has no matching keywords for portfolio OR screener | Falls through to ticker deep-dive path (existing). Agent never invoked. |
| `resolve_universe` called with unknown universe name | Tool returns `{"error": "unknown universe: X", "valid": ["sp500", "nifty500", "crypto"]}`. Agent re-tries with a valid one. |
| LLM returns tickers in `theme_to_universe` that aren't in the loaded universes | Tool drops them, includes them in `dropped` list + a note. |
| yfinance fundamentals fetch fails for a ticker | Candidate still appears in the output with `market_cap/pe_ttm/roe_pct = None`. Marked in `notes` if many failed. |
| CoinGecko API returns 429 (rate limit) | Cache miss falls back to a hardcoded BACKUP_CRYPTO_TICKERS list (BTC, ETH, SOL, BNB, XRP, ...) — sized at ~20. Note surfaced. |
| All filters eliminate every candidate | Agent submits findings with `candidates=[]` and a note: "0 candidates matched. Try loosening filters (e.g., max_pe higher, or remove sector filter)." |
| Universe JSON file missing | Tool raises a clear error → agent submits with `error` field; route surfaces it as a "Notes" section. |

## 10. Performance + cost

- 3-4 Sonnet calls per query (turn 1 universe resolve, turn 2 theme matching, optional turn 3 filter, turn 4 submit).
- Universe load cost: SP500 JSON ~50KB → no measurable latency; CoinGecko cache hit <50ms, cold ~1s.
- `theme_to_universe` is the heaviest LLM call — passing the constituent list to the model (~10-30K input tokens for SP500 alone). Mitigation: prompt cache the constituent list (cache_control: ephemeral on the constituents block).
- `screen_stocks`: live yfinance fetch for the shortlist. 20-30 tickers in parallel ≈ 3-5s.
- Total wall-clock: ~15-25s warm cache; ~30-50s cold.
- Per-query cost: ~$0.05-0.15 Sonnet (with prompt cache amortizing constituent lists).

## 11. Testing

| File | What |
|---|---|
| `test_data_universes.py` | Load JSON; CoinGecko cache hit/miss/fail; normalization of CoinGecko response to Constituent shape; backup list fallback. ~5 tests. |
| `test_tools_screener.py` | 4 tool impls. resolve_universe with valid + invalid names. theme_to_universe drops invented tickers. screen_stocks applies filters correctly with mocked yfinance. ~6 tests. |
| `test_screener_agent.py` | Sonnet loop: tools called in expected order, findings shape correct, short-circuit on empty universe. ~3 tests. |
| `test_routes_chat_screen_endpoint.py` | Direct POST /chat/screen SSE shape. ~2 tests. |
| `test_routes_chat_stream_screener.py` | `is_screener_query` matches expected phrasings; screener path bypasses ticker resolve; ordering with portfolio path. ~3 tests. |

Phase 4 end-state target: ~196 backend tests (178 today + ~19 new). No frontend tests — no frontend changes.

Live UAT (run after merge): hit `/chat/stream` with "AI infrastructure plays in India" — expect 10-20 candidates including names like TATAELXSI.NS, PERSISTENT.NS, KPITTECH.NS, MPHASIS.NS, COFORGE.NS within ~30s.

## 12. File map

```
backend/
  data/
    universes/
      sp500.json                       NEW (committed asset)
      nifty500.json                    NEW (committed asset)
  scripts/
    build_universes.py                 NEW (dev-time bootstrap; not imported by app)
  app/
    data/
      universes.py                     NEW (runtime loader + CoinGecko fetcher)
    models/
      screener.py                      NEW (Constituent, Candidate, ScreenerFindings)
    tools/
      screener.py                      NEW (4 closure-bound tools)
    agents/
      screener.py                      NEW (Sonnet agent)
    routes/
      chat.py                          MODIFY (add is_screener_query + ordering)
      chat_screen.py                   NEW (direct endpoint + renderers)
      __init__ or main.py              MODIFY (register chat_screen router)
    prompts/
      screener.md                      NEW (agent system prompt)
  tests/
    test_data_universes.py             NEW
    test_tools_screener.py             NEW
    test_screener_agent.py             NEW
    test_routes_chat_screen_endpoint.py        NEW
    test_routes_chat_stream_screener.py        NEW
```

## 13. End-of-Phase-4 acceptance

- ~196 backend tests pass; ruff + mypy strict clean.
- Existing 4 Playwright tests still pass (no frontend changes).
- Live UAT: chat query "AI infrastructure plays in India" returns ≥5 valid Indian tech tickers with names, sectors, prices populated; "find me cheap US dividend stocks with PE under 15" returns SP500 candidates with PE < 15 from sectors like Financials / Consumer Staples / Utilities.
- Render + Vercel auto-deploys green on `main`.
- All task commits on `main`.

**Phase 4 of the parent spec is then COMPLETE.** Phase 5 (Ops & polish: OpenTelemetry, Sentry, dashboards, full eval suite) is the final pre-v1 plan.
