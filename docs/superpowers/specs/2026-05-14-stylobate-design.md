# Stylobate — Design Spec

**Date:** 2026-05-14
**Status:** Approved for implementation planning
**Owner:** Rakhi Sinha (`github.com/RakhiIsTrying`)

## 1. Overview

Stylobate is a multi-agent, multi-function AI tool that gives an individual investor the analytical horsepower of a sell-side investment-banking team. The user interacts through a single web chat. Behind the chat, an orchestrator agent (Claude Opus) dispatches specialist agents (Claude Sonnet) in parallel across fundamentals, technicals, news, macro, portfolio, risk, and screening. Cheap utility agents (Claude Haiku) handle ticker resolution and short-circuit trivial questions.

The product covers US and Indian equities, ETFs, and major crypto. It is advisory-only — it never places trades.

The name *Stylobate* refers to the top step of a Greek temple on which the columns rest: the foundation that everything else stands on. The tool is meant to be the user's analytical foundation for investing decisions.

## 2. Goals and non-goals

### Goals (v1)

- Conversational chat interface that handles four user-facing workflows out of one input:
  1. **Stock deep-dive** — "Should I buy NVDA?" → multi-specialist research note with citations and a recommendation card.
  2. **Portfolio management** — track holdings, flag risks, propose rebalances.
  3. **Idea generation / screening** — "AI infrastructure plays in India" → curated candidate list.
  4. **Open-ended Q&A** — "Is now a good time for tech?", "Compare HDFC Bank vs ICICI", "What is a P/E ratio?".
- US + India market coverage as first-class peers, plus a `CRYPTO` market for major coins. Throughout the spec, "market" refers to one of these three values (`US`, `IN`, `CRYPTO`); `default_market` in `user_profiles` is constrained to (`US`,`IN`) only since crypto is treated as a parallel asset class, not a primary market.
- Multi-user with per-user portfolios, watchlists, chat history.
- Streaming responses with rich inline cards (stock card, recommendation card, charts).
- Every recommendation cited from tool results; educational-disclaimer on every recommendation.
- Per-user daily cost cap.

### Non-goals (v1)

- No trade execution. No brokerage integration (read or write).
- No leverage, options strategies, margin, day-trading signals, or penny stocks (mkt cap floor: $300M US / ₹2000Cr IN).
- No bonds, no options chains, no international equities beyond India.
- No mobile app. Web only, responsive desktop-first.
- No currency conversion: each ticker reports in its native currency.
- No SEBI/SEC registered-adviser features (KYC, suitability docs, etc.).
- No real-time streaming quotes — free-tier delayed data only (~15 min for US equities; mostly real-time for crypto/FX).

## 3. User experience

### 3.1 Layout

Single-page chat application with three regions:

- **Left sidebar** (collapsible, ~220px) — recent chats, portfolios, watchlists.
- **Main column** — the chat thread. Messages render as streaming text plus typed structured blocks (stock card, section, recommendation card, citation badges, "specialist trace" expander).
- **Header** — breadcrumb (chat title), market clocks for NYSE + NSE.
- **Input bar** — single text field with example placeholders that rotate (e.g., "Compare HDFC Bank vs ICICI", "Screen Indian midcaps with FCF growth > 20%").

Visual style: clinical white background, charcoal text, deep navy primary (`#1a2942`), gold accent (`#c79a4a`). Sans-serif typography (Inter or system font). No dark mode in v1.

### 3.2 Response anatomy

A "deep-dive" response streams in this order:

1. **Specialist progress bar** — shows live status of each specialist running (`Fundamental ✓ | Technical ✓ | News ⋯`).
2. **Quick take** — single line with a directional signal (Buy / Hold / Reduce, with confidence qualifier).
3. **Stock card** — ticker, name, exchange, current price + change, sparkline, six key stats (mkt cap, P/E, 1Y return, growth, margin, FCF yield).
4. **Research sections** — "Thesis", "Fundamentals", "Risks". Each section has clickable citation badges (`Fund · 1`, `News · 4`) that open the underlying tool result in a side panel.
5. **Recommendation card** — gold-bordered. Contains: signal, position-size range (% of portfolio, not dollars), entry zone, stop, 12-month target (base case). Always followed by the educational disclaimer.
6. **Specialist trace** (collapsed by default) — "Show what each specialist found", expands to show each specialist's raw structured output.

### 3.3 Streaming protocol

The backend emits typed JSON deltas over SSE. The frontend renders specialized React components for each type. Types include:

- `progress` — `{ specialist, status }`
- `quick_take` — `{ signal, qualifier }`
- `stock_card` — `{ ticker, market, price, stats }`
- `section` — `{ title, markdown, citations }`
- `recommendation` — `{ signal, position_size_range, entry, stop, target_12mo }`
- `disclaimer` — boilerplate text
- `specialist_trace` — `{ agent, raw_findings }`
- `error` — `{ specialist, message }`

Markdown is parsed for sections only; citations are inline JSX components, not raw markdown.

## 4. Agent architecture

Orchestrator + parallel specialists pattern. Ten agents in total.

### 4.1 Tier 1 — Orchestrator (Opus 4.7)

**Lead Banker.** The voice the user hears. Reads the user message + chat history + retrieved memory. Plans which specialists to dispatch via a `dispatch(specialists, brief)` tool. Synthesizes their structured findings into the final streamed response. Owns the final tone and citation discipline.

System prompt is the longest of all agents (~3K tokens) and is fully cached. Always reads its memory store (`agent_memory` table) at the start of a session to recall user preferences and prior facts.

### 4.2 Tier 2 — Research specialists (Sonnet 4.6)

Each runs in its own Anthropic conversation, in parallel via `asyncio.gather`. Each has a focused system prompt (~800 tokens, cached) and a small tool set.

- **Fundamental Analyst** — financial statements, valuation, peer comparables. Tools: `get_financials`, `get_filings`, `get_key_ratios`, `run_dcf`, `get_comparables`. Market-aware data path: US → EDGAR + yfinance + Alpha Vantage; IN → screener.in + BSE/NSE + MCA21. DCF uses Damodaran ERPs.
- **Technical Analyst** — price action, indicators, levels, patterns. Tools: `get_price_history`, `calc_indicators`, `detect_patterns`, `get_volume_profile`.
- **News & Sentiment** — recent news, earnings transcripts, sentiment, insider trades. Tools: `search_news`, `get_transcript`, `score_sentiment`, `get_insider_trades`. Routes Indian news through BSE/NSE corporate announcements + Moneycontrol/Trendlyne enrichment; US through Tiingo + EDGAR 8-Ks.
- **Macro Strategist** — rates, FX, sector rotation, macro regime. Tools: `get_rates`, `get_sector_perf`, `get_fred`, `get_fx`, `get_rbi`. Uses FRED for US, RBI for India.

### 4.3 Tier 2 — Decision specialists (Sonnet 4.6)

- **Portfolio Strategist** — allocation, sizing, rebalancing, tax-lot awareness. Tools: `get_holdings`, `calc_portfolio_stats`, `suggest_rebalance`, `tax_lot_view`. Reads user's portfolio from Supabase.
- **Risk Manager** — concentration, correlation, drawdown, stress. Tools: `calc_var`, `get_correlations`, `stress_test`, `concentration_check`.
- **Screener** — universe filtering for idea generation. Tools: `screen_stocks`, `screen_etfs`, `screen_crypto`, `theme_to_universe`. Universes: SP500, SP1500, Russell3000 (US); Nifty 50, Nifty 500, BSE 500 (IN); CoinGecko top 200 (crypto).

### 4.4 Tier 3 — Utility agents (Haiku 4.5)

- **Ticker Resolver** — turns natural-language references into typed tickers ("Apple" → `AAPL`; "Reliance" → asks NSE vs BSE, defaults `RELIANCE.NS`). Reads/writes `agent_memory` key `recent_markets` (list of markets the user has been working in this session) for stickiness on ambiguous names.
- **Query Classifier** — first thing every message hits. Tags the query (price-lookup / deep-analysis / portfolio / chitchat / screener). For trivial questions ("price of TSLA?"), short-circuits to a single tool call without invoking the orchestrator. Saves cost on the common case.

### 4.5 Coordination details

- **Dispatching.** Lead Banker calls a `dispatch` tool. The tool's implementation runs the named specialists in parallel and returns structured results to the Lead Banker as the tool's response. The Lead Banker doesn't have to be aware of asyncio — it just sees one tool call and one tool result.
- **Failure handling.** If a specialist times out or errors, the orchestrator is told `"<Specialist> unavailable: <reason>"` rather than seeing an exception. It continues with reduced inputs and notes the limitation in the response.
- **Output shape.** Every specialist returns a strict pydantic model. Output is validated before being passed to Lead Banker. Invalid output triggers one retry; persistent failure becomes an "unavailable" signal.
- **Memory.** Lead Banker can call `save_to_memory(key, value)` to persist cross-session facts ("user is bullish on India consumer", "user prefers position sizes 1–3%"). Stored in `agent_memory` table per user.

## 5. Tools and data sources

### 5.1 External sources

**Global / shared**

- `yfinance` (pip, unofficial Yahoo scraper) — OHLCV, basic fundamentals, dividends, options chains. Works for US (`AAPL`), India NSE (`.NS`), BSE (`.BO`), many intl. Flaky; wrap in retries.
- **Damodaran datasets** (NYU Stern, free, official) — industry betas, equity risk premiums by country, country risk premiums, default spreads, growth rates. Updated annually. CSV downloads cached locally; refreshed via cron.
- **CoinGecko** (free, rate-limited 30 rpm) — crypto prices, market cap, supply, dominance.
- **Anthropic API** — LLM brains for every agent (usage-based pricing).
- **Supabase** — auth, portfolios, chat history, watchlists, agent memory, tool-call observability, cache (free tier).

**United States**

- **SEC EDGAR** (`sec-api`, free, official) — 10-K/Q/8-K filings; XBRL financials; insider transactions; 13F holdings.
- **Alpha Vantage** (free, 25 req/day) — earnings transcripts, statements, technical indicators.
- **FRED** (`fredapi`, free, official) — Fed funds, treasuries, CPI, unemployment, GDP — all US macro series.
- **Tiingo News** (free, 500 req/day) — financial news headlines, tagged by ticker.

**India**

- **screener.in** — best free aggregator for Indian listed companies: financials, ratios, peers, shareholding, concall summaries. HTML scrape on free tier; clean API on paid tier (~₹500/mo). Plan: free in v1, upgrade if scraper drift becomes a daily problem.
- **BSE / NSE corporate filings** (free, official, scraping via `nsepython` / `jugaad-data`) — annual reports, quarterly results, shareholding patterns, corporate announcements.
- **MCA21** (Ministry of Corporate Affairs) — official company filings, registered financials, board info. Free for filings list; paid for downloads. Used for non-listed entities and authoritative cross-checks.
- **RBI** (free, official) — Indian risk-free rate, repo rate, GDP, CPI, FX reserves, all Indian macro series.
- **Trendlyne / Tickertape / Moneycontrol** — additional ratios, analyst forecasts, news. HTML scraping, fragile, TOS-grey. Used as enrichment only, never as source of truth. Failure must not break a query.

### 5.2 Tool function inventory

Each function is a typed Python function. Agents see them as Anthropic tool schemas. All wrapped in a common `tools/base.py` layer that handles retries (exponential backoff), caching (two-tier), rate-limit awareness per source, and structured logging.

**Fundamental Analyst**

- `get_financials(ticker, periods=4)` → `{income_stmt, balance_sheet, cash_flow}` for last N quarters in native currency
- `get_filings(ticker, types=[10-K, 10-Q, 8-K, annual_report, quarterly], limit=5)` → list of `FilingRef{type, date, url, summary}`
- `get_key_ratios(ticker)` → `{PE, PB, PS, ROE, ROIC, FCF_yield, debt_to_equity, current_ratio, …}`
- `run_dcf(ticker, growth_rate, discount_rate, terminal_g)` → `DCFResult{fair_value_per_share, currency, sensitivity_table, assumptions_used}`. Pulls beta + ERP from Damodaran by market.
- `get_comparables(ticker, n=5)` → list of `Peer{ticker, P/E, EV/EBITDA, growth, margin, mkt_cap}`

**Technical Analyst**

- `get_price_history(ticker, period="1y", interval="1d")` → OHLCV DataFrame
- `calc_indicators(ticker, indicators=[rsi, macd, sma200, ema50, bbands])` → `{series_by_indicator, latest_values}`
- `detect_patterns(ticker)` → list of `Pattern{kind, levels, confidence}` (support/resistance, head&shoulders, double-bottom, etc.)
- `get_volume_profile(ticker, period)` → `{high_volume_levels, value_area}`

**News & Sentiment**

- `search_news(query, lookback_days=14, limit=20, market=auto)` → list of `NewsItem{title, url, source, summary, tickers, published_at}`
- `get_transcript(ticker, quarter)` → `EarningsTranscript{date, text, segments, sentiment_score}`
- `score_sentiment(text)` → float in `[-1, +1]`
- `get_insider_trades(ticker, lookback_days=90)` → list of `InsiderTx{insider, role, type, qty, price, date}`

**Macro Strategist**

- `get_rates(market)` → `Rates{policy_rate, 2y, 10y, 30y, real_10y, currency}` — Fed/RBI policy + yield curve.
- `get_sector_perf(market, period="1m")` → `dict[sector_name → return]`
- `get_fred(series_id)` → `TimeSeries` for any FRED series
- `get_rbi(series_id)` → `TimeSeries` for any RBI series
- `get_fx(pair, period)` → OHLCV for currency pair (e.g., `USDINR=X`)

**Portfolio Strategist**

- `get_holdings(user_id, portfolio_id=None)` → list of `Position{ticker, market, qty, cost_basis, currency, asset_class}`
- `calc_portfolio_stats(holdings)` → `Stats{totals_by_currency, weights_by_currency, returns_by_period_by_currency, sharpe_by_currency, beta_by_currency, expense_ratio_weighted_by_currency}`. No cross-currency aggregation — every aggregate is computed per currency cohort. Each cohort uses its native benchmark (S&P 500 for USD positions, Nifty 50 for INR, BTC for crypto).
- `suggest_rebalance(holdings, target_alloc)` → list of `Trade{ticker, action, qty, est_value}`
- `tax_lot_view(user_id, ticker)` → list of `TaxLot{purchased_at, qty, basis, current_value, gain, holding_period}`

**Risk Manager**

- `calc_var(holdings, confidence=0.95, horizon_days=10)` → `{var_by_currency}` — same per-currency policy
- `get_correlations(tickers, period="1y")` → correlation matrix (numeric, currency-agnostic)
- `stress_test(holdings, scenario)` → `Scenario{p_l_by_position, totals_by_currency}`. Pre-canned scenarios: `rates_+200bps`, `equity_-20%`, `inr_depreciation_-10%`.
- `concentration_check(holdings)` → list of `Concern{kind, weight, threshold}` — flags single-position > 10%, sector > 30%, single-issuer credit > 5%, etc.

**Screener**

- `screen_stocks(criteria, universe="sp1500", limit=50)` → list of `Candidate{ticker, market, current_price, key_stats}`. Universes: `sp500`, `sp1500`, `russell3000`, `nifty50`, `nifty500`, `bse500`.
- `screen_etfs(criteria, limit=30)` → list of `Candidate`
- `screen_crypto(criteria, limit=20)` → list of `Candidate`
- `theme_to_universe(theme_text)` → list of tickers ("AI infrastructure in India" → curated list via LLM)

**Utilities**

- `resolve_symbol(text)` → `{ticker, market, asset_class, name, confidence}`. Returns ambiguous candidates if confidence < threshold; the orchestrator then asks the user.
- `classify_intent(message)` → `{complexity: simple|complex, suggested_specialists, short_circuit_tool?}`

## 6. Backend architecture

### 6.1 Stack

- Python 3.11
- `uv` for dependency management (project + lockfile)
- FastAPI + uvicorn (async)
- `anthropic` SDK with prompt caching enabled
- `httpx` (async) for external HTTP
- `pandas`, `numpy`, `pandas-ta` for analytics
- `pydantic` v2 for all DTOs and tool I/O contracts
- `supabase-py` for DB
- `structlog` for logs, OpenTelemetry for traces, Sentry for errors
- `pytest` + `vcr.py` + `respx` for tests

### 6.2 Request flow

A single user message moves through this pipeline:

1. **Frontend** posts the message to `/chat/stream` with the Supabase JWT in the `Authorization` header. Body: `{chat_id, content}`.
2. **FastAPI** verifies the JWT (via Supabase JWKS), loads chat history (last N messages from `messages` table), and opens an SSE stream.
3. **Query Classifier (Haiku)** runs first. Returns `{complexity, suggested_specialists, short_circuit_tool?}`.
4. If `simple` (price lookup, basic definition, casual reply): call the suggested tool or short-circuit Haiku response directly, stream the result, persist, done. p50 latency target: < 1.5s. Cost: ~$0.001.
5. If `complex`: invoke **Lead Banker (Opus)** with the full context (chat history + retrieved memory + classifier hint). Lead Banker calls `dispatch(specialists, brief)` as a tool.
6. The `dispatch` tool's implementation runs the requested specialists in parallel via `asyncio.gather`. Each specialist:
   - Starts a fresh Anthropic conversation
   - Uses its own system prompt (cached)
   - Has access only to its own tools
   - Returns a strict pydantic model
   - Has a per-call timeout (default 20s)
7. Specialist outputs are aggregated and returned to Lead Banker as the `dispatch` tool's response. Failed specialists return `{available: false, reason}`.
8. **Lead Banker synthesizes** the final response, streaming SSE deltas as it generates. The synthesis prompt enforces citation discipline and the disclaimer.
9. **Background** task writes the user message + assistant message + every `tool_calls` row + `model_runs` row to Supabase. The user response doesn't wait on this.

### 6.3 Streaming SSE

The backend emits these event types over `/chat/stream`:

- `event: progress` — `{specialist, status}` — periodically as specialists complete
- `event: delta` — incremental typed JSON deltas as Lead Banker streams (see §3.3)
- `event: done` — final assistant message ID
- `event: error` — fatal error before completion

Frontend subscribes via `EventSource` (with re-auth-on-401 retry).

### 6.4 HTTP endpoints

| Method | Path | Description |
|---|---|---|
| POST (SSE) | `/chat/stream` | Main agent endpoint, returns SSE stream |
| GET | `/chats` | List user's chats |
| GET | `/chats/{id}` | Load full thread |
| POST | `/chats` | Create new chat |
| POST | `/portfolios` | Create portfolio |
| GET | `/portfolios` | List portfolios |
| POST | `/portfolios/{id}/positions` | Add holding |
| DELETE | `/positions/{id}` | Remove holding |
| POST | `/watchlists` | Create watchlist |
| POST | `/watchlists/{id}/items` | Add ticker |
| GET | `/tickers/resolve?q=...&market=auto` | Disambiguate symbol (used by frontend autocomplete) |
| GET | `/healthz` | Liveness probe |

All routes (except `/healthz`) require a valid Supabase JWT.

### 6.5 Caching

**Prompt cache** (Anthropic, 5-min TTL).
- Every agent's system prompt and tool definitions are cached.
- Each agent's prompt is structured: `[stable system prompt | stable tool defs | per-request context]`. The first two blocks are cache-control marked. Goal: 70–90% cache reads on input tokens in steady state.
- Cache survives across parallel specialist calls in the same query (same orchestrator → multiple agents in same 5-min window).

**Tool-result cache** (two-tier).
- L1: per-worker in-memory LRU, 1000 entries.
- L2: Supabase `cache_kv` table, keyed on `tool_name + canonical_args + market_date`.
- TTL by data type: filings → 7 days; macro series → 1 day; price quotes → 5 min; Damodaran tables → 30 days; news searches → 1 hour.

### 6.6 Repo layout

```
stylobate/
├── frontend/                      # Next.js 15 (App Router, TypeScript)
│   ├── app/
│   │   ├── (auth)/sign-in/
│   │   ├── chat/[id]/page.tsx
│   │   ├── portfolios/
│   │   ├── watchlists/
│   │   └── api/auth/[...nextauth]/
│   ├── components/                # ChatThread, StockCard, RecoCard, …
│   ├── lib/                       # supabase client, SSE helpers, types
│   ├── package.json
│   └── tailwind.config.ts
│
├── backend/                       # Python FastAPI
│   ├── app/
│   │   ├── main.py
│   │   ├── routes/                # chat.py, portfolios.py, watchlists.py, tickers.py
│   │   ├── agents/                # orchestrator.py, fundamental.py, technical.py,
│   │   │                          # news.py, macro.py, portfolio_agent.py,
│   │   │                          # risk.py, screener.py, utilities.py
│   │   ├── prompts/               # one .md per agent (cache-friendly)
│   │   ├── tools/                 # typed tool functions
│   │   ├── data/                  # source adapters (one .py per API)
│   │   ├── core/                  # anthropic_client, cache, retry, markets, config
│   │   └── db/                    # client.py, models.py
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
│
├── supabase/
│   └── migrations/                # 0001_init.sql, 0002_rls.sql, ...
│
├── docs/
│   └── superpowers/specs/         # this file
│
├── .github/workflows/             # ci.yml
├── .env.example
├── Makefile
└── README.md
```

## 7. Database schema

All tables live in Supabase Postgres. RLS is on for every table; policies scope reads/writes to `auth.uid() = user_id` (directly or through a join).

### 7.1 Core tables

```sql
-- managed by Supabase Auth: auth.users(id, email, ...)

create table user_profiles (
  id              uuid primary key references auth.users(id) on delete cascade,
  default_market  text not null check (default_market in ('US','IN')) default 'US',
  default_currency char(3) not null default 'USD',
  risk_tolerance  text check (risk_tolerance in ('conservative','balanced','aggressive')),
  created_at      timestamptz default now()
);

create table portfolios (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references user_profiles(id) on delete cascade,
  name          text not null,
  base_currency char(3) not null default 'USD',
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

create table positions (
  id            uuid primary key default gen_random_uuid(),
  portfolio_id  uuid not null references portfolios(id) on delete cascade,
  ticker        text not null,
  market        text not null check (market in ('US','IN','CRYPTO')),
  asset_class   text not null check (asset_class in ('equity','etf','crypto')),
  quantity      numeric(20, 8) not null,
  cost_basis    numeric(20, 4),
  currency      char(3) not null,
  opened_at     date,
  created_at    timestamptz default now()
);

create table tax_lots (
  id            uuid primary key default gen_random_uuid(),
  position_id   uuid not null references positions(id) on delete cascade,
  qty           numeric(20, 8) not null,
  price         numeric(20, 4) not null,
  currency      char(3) not null,
  acquired_at   date not null
);

create table watchlists (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references user_profiles(id) on delete cascade,
  name        text not null,
  created_at  timestamptz default now()
);

create table watchlist_items (
  watchlist_id uuid not null references watchlists(id) on delete cascade,
  ticker       text not null,
  market       text not null,
  added_at     timestamptz default now(),
  notes        text,
  primary key (watchlist_id, ticker, market)
);

create table chats (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references user_profiles(id) on delete cascade,
  title           text,
  model           text default 'claude-opus-4-7',
  created_at      timestamptz default now(),
  last_message_at timestamptz default now()
);

create table messages (
  id          uuid primary key default gen_random_uuid(),
  chat_id     uuid not null references chats(id) on delete cascade,
  role        text not null check (role in ('user','assistant','system')),
  content     jsonb not null,            -- assistant: ordered array of typed blocks
                                          --            ({type:'quick_take'|'stock_card'|'section'|
                                          --              'recommendation'|'disclaimer'|'specialist_trace',...})
                                          -- user:      {type:'text', text:'...'}
                                          -- the streaming SSE deltas (§3.3) are recombined into
                                          -- this final shape on done before persistence
  created_at  timestamptz default now()
);
```

### 7.2 Derived / operational tables

```sql
create table agent_memory (
  user_id     uuid not null references user_profiles(id) on delete cascade,
  key         text not null,
  value       jsonb not null,
  updated_at  timestamptz default now(),
  primary key (user_id, key)
);

create table tool_calls (
  id          uuid primary key default gen_random_uuid(),
  message_id  uuid not null references messages(id) on delete cascade,
  agent       text not null,
  tool        text not null,
  input       jsonb,
  output      jsonb,
  latency_ms  integer,
  error       text,
  created_at  timestamptz default now()
);

create table model_runs (
  id              uuid primary key default gen_random_uuid(),
  message_id      uuid references messages(id) on delete cascade,
  agent           text not null,
  model           text not null,
  input_tokens    integer not null,
  output_tokens   integer not null,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  cost_usd        numeric(10, 6) not null,
  created_at      timestamptz default now()
);

create table cache_kv (
  key         text primary key,
  value       jsonb not null,
  expires_at  timestamptz not null
);
create index cache_kv_expires on cache_kv (expires_at);
```

### 7.3 RLS policies (sketch)

- `user_profiles`: read/write only when `id = auth.uid()`.
- `portfolios`, `watchlists`, `chats`, `agent_memory`: read/write only when `user_id = auth.uid()`.
- `positions`, `tax_lots`, `watchlist_items`, `messages`, `tool_calls`, `model_runs`: scoped through the join to their parent's `user_id`.
- `cache_kv`: service-role only (no end-user access).

All tool calls happen server-side using a service-role key; the backend re-issues a per-user-scoped client when reading/writing user-owned data (so RLS enforces even if backend code has bugs).

## 8. Frontend

Next.js 15 (App Router) + TypeScript. Tailwind CSS + shadcn/ui for components. `@supabase/ssr` for auth. `EventSource` for SSE. `react-markdown` + `rehype-sanitize` for the markdown sections inside responses. `recharts` (or `visx` if perf becomes an issue) for charts.

Key components:

- `<ChatThread>` — renders an ordered list of messages.
- `<UserMessage>` / `<AssistantMessage>` — wrappers per role.
- `<DeltaRenderer>` — switches on delta type and renders the right block component.
- `<StockCard>`, `<RecommendationCard>`, `<Section>`, `<CitationBadge>`, `<SpecialistTrace>`.
- `<MarketClock>` — header component, ticks every minute, knows NYSE/NSE hours.
- `<Sidebar>` — recents/portfolios/watchlists; collapsible.
- `<Composer>` — input bar with autocomplete via `/tickers/resolve`.

Authentication: middleware redirects unauthenticated users to `/sign-in`; signed-in users have a `supabase-auth` cookie. SSR pages read the user from the cookie. The chat page passes the user's JWT to the SSE call via header.

## 9. Cost and caching strategy

### 9.1 Per-query cost targets (after caching warms up)

| Workflow | Agents | First call | Warm cache |
|---|---|---|---|
| Price-lookup (Haiku short-circuit) | 1 | $0.001 | $0.001 |
| Stock deep-dive | 4–5 specialists | ~$0.18 | ~$0.05 |
| Portfolio health check (10 holdings) | 3–4 | ~$0.22 | ~$0.07 |
| Idea screen | 2–3 | ~$0.12 | ~$0.04 |
| Avg / active user / day at 10 queries | — | ~$1.30 | ~$0.40 |

### 9.2 Budget controls

- **Per-user daily cap** — default $1/day per user. Server tracks usage in `model_runs` (sum `cost_usd` for the calendar day in user's local timezone). Past cap → backend refuses with a friendly message that explains the cap and when it resets.
- **Per-message hard ceiling** — $0.50. If a single query exceeds this (e.g., runaway agent), backend aborts gracefully.
- **Graceful degradation** — when user is at >80% of daily cap, the orchestrator is told to prefer Sonnet for synthesis and skip optional specialists.

## 10. Safety and disclosures

- **Disclaimer** on every recommendation, immutable: *"Educational analysis, not personalized investment advice. Do your own diligence and consider your tax situation."*
- **Citations enforced.** Every numeric claim and every directional recommendation must reference a tool result. The Lead Banker's system prompt makes this a hard requirement. An output validator (server-side) checks that each `recommendation` and each numeric in a `section` delta has at least one accompanying citation. Failures trigger one retry. If retry fails, the response is sent without the failing numeric and a structured `error` event is emitted to the user.
- **Calibration.** Recommendation cards use directional language ("tactical buy / hold / reduce"), position-size *ranges* (e.g., "2–4% of portfolio"), and a 12-month *base-case* target with explicit risk-adjusted commentary. Never absolute claims, never specific dollar amounts.
- **Hard exclusions in v1.** No leverage, no options strategies, no margin recommendations, no day-trading signals, no penny stocks (mkt cap floor: $300M US / ₹2000Cr IN). These are blocked at the agent level (system prompts) and at the output validator.
- **Regulatory posture.** Advisory-only, educational framing, individual user. Not a registered investment adviser under SEC IA or SEBI IA rules. If the product is later monetized or made available beyond personal use, this requires fresh review.
- **PII.** Holdings are sensitive. RLS on every Supabase table. Holdings never appear in logs (structlog filters `cost_basis`, `quantity`, `tax_lots.*`). Holdings appear in LLM prompts only when bound to a user-scoped tool call, never in shared cache keys.

## 11. Testing and evaluation

- **Unit tests** for every tool function. HTTP calls are recorded with `vcr.py` cassettes that are checked in; tests are deterministic. `pytest -q` in CI.
- **Agent eval suite** — one YAML file per agent (e.g., `evals/fundamental/aapl_q3_2024.yaml`) that declares: expected sections, expected key numbers within tolerance, expected citation count, and a banned-claim list. Eval runs against frozen tool outputs, no live APIs. Pass-rate gate is 95% before merge to `main`.
- **End-to-end smoke tests** — daily GitHub Actions job hits 6 canary queries (US deep-dive, India deep-dive, mixed portfolio check, crypto question, idea screen, ambiguous-ticker disambiguation) against live APIs. Failures notify Slack.
- **Adversarial hallucination harness** — set of 20 prompts about non-existent tickers, fabricated earnings, contradictory data. Agent must refuse / say "I don't have that". Runs nightly.
- **Manual UAT checklist** in `docs/uat.md`, runs before each tagged release.

## 12. Observability

- **Structlog** → JSON logs everywhere. Each chat message gets a correlation ID that propagates through every agent and tool call.
- **OpenTelemetry** spans for each agent invocation and each tool call. Exported to Honeycomb (free tier) in prod, Tempo locally.
- **Sentry** for unhandled exceptions, PII scrubbed.
- **Dashboards** (Grafana-style on Honeycomb): cost per chat, p50/p95 latency per agent, cache hit rate, tool error rate by source, daily active users, daily cost.
- **Cost alerts** — per-user daily cap; global daily cap; Slack notification when hit.

## 13. Deployment

- **Frontend:** Vercel. Auto-deploy on push to `main`. Preview deploys per PR.
- **Backend:** Fly.io, 2 regions (`iad`, `bom`) for latency. Small VMs, scale-to-zero when idle.
- **Database:** Supabase managed (free tier in v1).
- **Secrets:** Vercel + Fly env vars. Local `.env.local` (gitignored), `.env.example` template checked in.
- **CI:** GitHub Actions. Lint (ruff, mypy, eslint, tsc) → tests (pytest, vitest) → build → deploy on green main.
- **Migrations:** SQL files in `supabase/migrations/`, applied via `supabase db push` on deploy.

## 14. Open risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| `yfinance` flakes (rate limits, empty responses) | High | Retry w/ exp backoff; if persistent, agent reports "live price unavailable" rather than fabricate. |
| Indian scrapers (screener.in, Trendlyne) HTML drift | High | One adapter per source so failures are isolated. Daily smoke test catches drift same-day. Upgrade screener.in to paid API if drift becomes frequent. |
| LLM hallucinated financials despite citations | Medium | Output validator checks numeric outputs against tool results; mismatches → 1 retry; eval suite catches regressions. |
| Costs scale super-linearly | Medium | Per-user daily caps; aggressive prompt caching; degrade Sonnet→Haiku near cap. |
| Supabase free-tier limits (500MB db, 2GB egress) | Low | Archive old chats / tool_calls; upgrade tier when crossed. |
| Free-tier API quotas (Alpha Vantage 25/day, Tiingo 500/day) | Medium | Aggressive caching; spread calls across sources where possible; fall back gracefully. |
| Currency confusion if user has mixed-market portfolio | Medium | Strict per-currency reporting; UI labels every monetary value with its currency. No implicit conversions. |
| TOS / scraping issues with Trendlyne/Moneycontrol | Medium | Use as enrichment only; never as source of truth; can be disabled without breaking core flows. |

## 15. Phased rollout (suggested for implementation planning)

The spec describes the v1 target. Implementation is best phased:

- **Phase 0 — Foundation (~2 weeks).** Repos, CI, Supabase project, Auth, basic Next.js shell, basic FastAPI shell. End: a user can sign up, see an empty chat, type a message, get an echo back.
- **Phase 1 — Single specialist end-to-end (~2 weeks).** Lead Banker + Fundamental Analyst + Ticker Resolver. yfinance + EDGAR data adapters. Prompt caching. Output validator. End: deep-dive on AAPL produces a real research note with citations.
- **Phase 2 — Full research team (~3 weeks).** Add Technical, News, Macro specialists. Full deep-dive workflow. India data path (screener.in, BSE/NSE, RBI, Damodaran) for Indian tickers. End: deep-dive works for AAPL, RELIANCE.NS, BTC.
- **Phase 3 — Portfolio & risk (~3 weeks).** Portfolio CRUD, Portfolio Strategist, Risk Manager agents. tax_lots, rebalance suggestions, concentration checks. End: user can input holdings and get a health check.
- **Phase 4 — Screener & ideas (~2 weeks).** Screener agent + universes + `theme_to_universe`. End: "AI infrastructure plays in India" returns a curated list.
- **Phase 5 — Ops & polish (~2 weeks).** OpenTelemetry, Sentry, dashboards, full eval suite, adversarial harness, UAT checklist, hardening, public beta readiness.

Total: ~14 weeks for v1 (compressible with parallelism; this is a rough single-developer estimate).

## 16. Future (post-v1, not in scope here)

- Real-time / paid data (Polygon, Alpaca, FMP) — when free tier quotas are the bottleneck.
- Paper trading (Alpaca paper) — track recommendations against live prices.
- Options chains and basic options analytics.
- Bonds + fixed income agent.
- International equities beyond India (UK, Japan, Hong Kong).
- Read-only brokerage sync (Plaid, Alpaca) — auto-update portfolios.
- Mobile app.
- Multi-language UI (Hindi).
- Newsletter / scheduled briefings.

## Appendix A — Anthropic prompt-cache layout

Each agent's system prompt is structured as four blocks. The first three are cache-control marked.

```
[1] Role & persona      (~200 tokens, never changes)              cache_control: ephemeral
[2] Tool definitions     (~600–1500 tokens, changes only on tool-set change)  cache_control: ephemeral
[3] Domain guidelines   (~500–1000 tokens, changes on prompt refinement)     cache_control: ephemeral
[4] Per-request context (~500–2000 tokens, fresh each call)        not cached
```

The Lead Banker also has a [5] retrieved memory block before [4], cached per-user with a short TTL.

## Appendix B — Glossary

- **Orchestrator** — Lead Banker agent that plans + synthesizes; runs on Opus.
- **Specialist** — domain agent that does one thing well; runs on Sonnet.
- **Utility** — fast, cheap agent for trivial classification/resolution; runs on Haiku.
- **Market** — geographic/regulatory market (US, IN, CRYPTO).
- **Native currency** — currency in which the asset trades (USD for US equities, INR for Indian, USD for crypto by convention).
- **Specialist trace** — the structured output of each specialist, surfaced in the UI behind an expander.
- **Output validator** — server-side function that checks Lead Banker's output for citation discipline before streaming.
