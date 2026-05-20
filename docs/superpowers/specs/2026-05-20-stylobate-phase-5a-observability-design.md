# Stylobate — Phase 5A: Observability Design

> **For agentic workers:** This is a sub-phase spec. After approval, invoke `superpowers:writing-plans` to produce the implementation plan at `docs/superpowers/plans/`.

**Parent spec:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` (§15 Phase 5 — Ops & polish).

**Phase 5 decomposition:** Phase 5 was split into 5A (observability — this spec), 5B (evals + adversarial), and 5C (hardening + UAT + docs). Each sub-phase ships independently.

**Goal:** Make Stylobate observable in production. After 5A: every agent run is a traceable span in Honeycomb, every uncaught exception surfaces in Sentry, and an `/admin/metrics` dashboard summarizes cost / latency / error-rate from data the app already writes to `model_runs` + `tool_calls`.

**Architecture:** Three independent layers. OpenTelemetry with auto-instrumentation (FastAPI + httpx + asyncpg) plus manual spans on agents and Anthropic calls — exports via OTLP to Honeycomb when `HONEYCOMB_API_KEY` is set, no-op otherwise. Sentry SDK initialized when `SENTRY_DSN` is set, focused on errors only (traces handled by OTel). A new `/admin/metrics` endpoint reads existing `model_runs` + `tool_calls` rollups; admins gated by an email allowlist (`ADMIN_EMAILS` env var). All three layers degrade gracefully — code paths default to no-op when env vars are unset, so local dev and Render keep working without any external signup.

**Tech Stack:** New Python deps — `sentry-sdk[fastapi]`, `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-asyncpg`. New frontend: one Next.js page (`/admin/metrics`), table-only, no charts.

**Spec coverage:** This spec covers OpenTelemetry + Sentry + a metrics dashboard. The remaining Phase 5 items (evals, adversarial harness, UAT checklist, hardening, public-beta docs) are out of scope here — they ship in 5B and 5C.

**Out of scope (deferred to later sub-phases or post-v1):**
- Eval suite + adversarial harness → Phase 5B.
- Rate limiting + secret review + UAT checklist + public-beta docs → Phase 5C.
- Prometheus metrics endpoint + custom Grafana dashboards (overengineered for v1).
- Per-user cost caps (financial throttling). Phase 5C may add a soft warning; hard caps are post-v1.
- Real-time alerting (PagerDuty / OpsGenie). Sentry + Honeycomb email digests cover v1 needs.

---

## 1. Architecture overview

```
                   ┌───────────────────────┐
   FastAPI ───────▶│  Sentry SDK            │── HTTPS ──▶ Sentry (errors only)
                   │  init only if          │            Free tier ~5K errors/mo
                   │  SENTRY_DSN set        │
                   └───────────────────────┘

                   ┌───────────────────────┐
   FastAPI ───────▶│  OpenTelemetry         │── OTLP/HTTP ─▶ Honeycomb (traces)
   httpx ─────────▶│  + auto-instrument     │             Free tier, 60-day
   asyncpg ───────▶│  + manual spans        │             retention
   Anthropic ─────▶│  Exporter no-op when   │
                   │  HONEYCOMB_API_KEY     │
                   │  unset                  │
                   └───────────────────────┘

                   ┌───────────────────────┐
   model_runs ────▶│  GET /admin/metrics    │── JSON ────▶ Next.js admin page
   tool_calls ────▶│  Aggregate SQL queries │              tables only, no
                   │  ADMIN_EMAILS allowlist│              charts in v1
                   └───────────────────────┘
```

Three layers are independent — you can ship any one without the others. Order in the plan: dashboard first (no new deps), then Sentry (one dep, fast), then OTel (multiple deps, more wiring).

## 2. New environment variables

All three are surfaced in `render.yaml` with `sync: false` (set in Render dashboard):

| Var | Required? | Effect when unset |
|---|---|---|
| `SENTRY_DSN` | optional | Sentry init skipped; no error reporting |
| `HONEYCOMB_API_KEY` | optional | OTel exporter is no-op; spans created but discarded |
| `ADMIN_EMAILS` | required for `/admin/metrics` | Endpoint returns 403 for everyone |

For local dev, `backend/.env.example` documents all three with placeholder values + instructions.

Optional OTel tuning vars (defaults work):
- `OTEL_SERVICE_NAME` — defaults to `stylobate-backend`.
- `OTEL_TRACES_SAMPLER_ARG` — sample rate; defaults to `1.0` (sample everything). Lower in production if cost grows.
- `OTEL_RESOURCE_ATTRIBUTES` — defaults to `deployment.environment=<RENDER_SERVICE_NAME>`.

## 3. OpenTelemetry instrumentation

### New module `app/core/tracing.py`

```python
from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_HONEYCOMB_OTLP_ENDPOINT = "https://api.honeycomb.io/v1/traces"


def init_tracing() -> None:
    """Configure OpenTelemetry tracing. Idempotent. Safe to call when API key absent."""
    service_name = os.environ.get("OTEL_SERVICE_NAME", "stylobate-backend")
    resource = Resource.create({
        "service.name": service_name,
        "deployment.environment": os.environ.get("RENDER_SERVICE_NAME", "local"),
    })
    provider = TracerProvider(resource=resource)

    honeycomb_key = os.environ.get("HONEYCOMB_API_KEY")
    if honeycomb_key:
        exporter = OTLPSpanExporter(
            endpoint=_HONEYCOMB_OTLP_ENDPOINT,
            headers={"x-honeycomb-team": honeycomb_key},
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    # else: spans are created but the provider has no exporter -> dropped.

    trace.set_tracer_provider(provider)


def get_tracer(name: str = "stylobate") -> Any:
    return trace.get_tracer(name)
```

Called once from `app/main.py`'s `create_app()` BEFORE any FastAPI router is mounted.

### Auto-instrumentation (`app/main.py`)

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

def create_app() -> FastAPI:
    init_tracing()           # OTel provider + Honeycomb exporter
    init_sentry()            # Sentry SDK (no-op when DSN missing)
    ...
    app = FastAPI(...)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()
    ...
```

These three instrumentors generate spans for every HTTP request, every outbound httpx call (yfinance, CoinGecko, Anthropic, Supabase), and every asyncpg query.

### Manual spans on agents

Each agent (`lead_banker`, `fundamental`, `technical`, `news_sentiment`, `macro`, `portfolio_strategist`, `risk_manager`, `screener`) wraps its `run_*` async function body in a span:

```python
# Pattern, applied identically in each agent:
from app.core.tracing import get_tracer
tracer = get_tracer()

async def run_lead_banker(...):
    with tracer.start_as_current_span("lead_banker.run") as span:
        span.set_attribute("user_id", user_id or "anon")
        span.set_attribute("portfolio_mode", portfolio_mode)
        # existing body
```

Span attributes vary per agent — see the file map in §10.

### Anthropic call wrapper

`app/core/anthropic_client.py` already wraps the SDK. We add a per-call span:

```python
async def messages_create_traced(client, **kwargs):
    with tracer.start_as_current_span("anthropic.messages.create") as span:
        span.set_attribute("model", kwargs.get("model", "?"))
        resp = await client.messages.create(**kwargs)
        usage = getattr(resp, "usage", None)
        if usage:
            span.set_attribute("input_tokens", getattr(usage, "input_tokens", 0))
            span.set_attribute("output_tokens", getattr(usage, "output_tokens", 0))
            span.set_attribute(
                "cache_read_tokens", getattr(usage, "cache_read_input_tokens", 0)
            )
        return resp
```

Agents continue to call `client.messages.create(...)` — but `get_client()` returns a thin wrapper whose `messages.create` is `messages_create_traced`. No call-site changes needed; the wrap lives in one place.

## 4. Sentry integration

### New module `app/core/sentry.py`

```python
from __future__ import annotations

import os

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration


def init_sentry() -> None:
    """Init Sentry. No-op when SENTRY_DSN is unset."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.0,            # OTel handles traces; Sentry handles errors
        send_default_pii=False,
        environment=os.environ.get("RENDER_SERVICE_NAME", "local"),
        integrations=[FastApiIntegration()],
    )
```

Called from `create_app()` next to `init_tracing()`. Sentry's FastAPI integration captures unhandled exceptions and 500-class responses without any additional wiring.

### What gets reported

- Any uncaught Python exception (the FastAPI middleware catches them).
- Any explicit `sentry_sdk.capture_exception(e)` call inside `try/except` blocks where we WANT visibility (e.g., yfinance fetch failures inside `prices_cache`).
- Sensitive PII is not sent (`send_default_pii=False`); request bodies are scrubbed by default.

### What does NOT get reported

- Expected 4xx (auth failures, validation errors). Sentry's FastAPI integration ignores these by default.
- Tool-result `{"error": "..."}` payloads inside agent loops — they're domain errors, not exceptions.
- yfinance rate-limits and graceful-fallback paths (already logged via `logger.warning`).

## 5. `/admin/metrics` dashboard

### Backend route

`backend/app/routes/admin_metrics.py` (NEW):

```python
@router.get("/admin/metrics")
async def get_metrics(
    window: str = "24h",
    user: dict[str, Any] = Depends(get_current_user),
) -> MetricsResponse:
    if not _is_admin(user.get("email", "")):
        raise HTTPException(403, "admin only")
    delta = _parse_window(window)  # "1h" / "24h" / "7d" / "30d" → timedelta
    since = datetime.now(UTC) - delta
    return await _build_metrics(since)
```

Window parser supports `1h`, `24h`, `7d`, `30d` (raises 422 for other values).

Admin check (`_is_admin(email)`):

```python
def _is_admin(email: str) -> bool:
    allowlist = os.environ.get("ADMIN_EMAILS", "")
    if not allowlist:
        return False
    return email.strip().lower() in {
        e.strip().lower() for e in allowlist.split(",") if e.strip()
    }
```

### SQL queries

All aggregates run against `model_runs` + `tool_calls`:

**Totals:**
```sql
SELECT COUNT(DISTINCT message_id) AS queries,
       SUM(cost_usd) AS cost_usd,
       COUNT(*) AS anthropic_calls
FROM model_runs
WHERE created_at >= $1
```

**Per-agent (cost + p50/p99 latency):**
```sql
-- model_runs doesn't store latency; we derive per-agent latency from tool_calls.
WITH agent_costs AS (
    SELECT agent, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd
    FROM model_runs
    WHERE created_at >= $1
    GROUP BY agent
),
agent_latencies AS (
    SELECT agent,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
           AVG(CASE WHEN error IS NOT NULL THEN 1.0 ELSE 0.0 END) AS error_rate
    FROM tool_calls
    WHERE created_at >= $1
    GROUP BY agent
)
SELECT c.agent, c.calls, c.cost_usd,
       l.p50_ms, l.p99_ms, l.error_rate
FROM agent_costs c
LEFT JOIN agent_latencies l USING (agent)
ORDER BY c.cost_usd DESC NULLS LAST
```

**Per-tool (p50/p99 + error rate):**
```sql
SELECT tool, COUNT(*) AS calls,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
       AVG(CASE WHEN error IS NOT NULL THEN 1.0 ELSE 0.0 END) AS error_rate
FROM tool_calls
WHERE created_at >= $1
GROUP BY tool
ORDER BY calls DESC
LIMIT 20
```

**Total errors (Sentry shows the same, but we surface a count for the dashboard):**
```sql
SELECT COUNT(*) AS error_count FROM tool_calls
WHERE created_at >= $1 AND error IS NOT NULL
```

### Response shape

```python
class AgentMetric(BaseModel):
    agent: str
    calls: int
    cost_usd: float
    p50_ms: float | None
    p99_ms: float | None
    error_rate: float


class ToolMetric(BaseModel):
    tool: str
    calls: int
    p50_ms: float | None
    p99_ms: float | None
    error_rate: float


class TotalsMetric(BaseModel):
    queries: int
    cost_usd: float
    anthropic_calls: int
    tool_calls: int
    errors: int


class MetricsResponse(BaseModel):
    window: str
    since: datetime
    totals: TotalsMetric
    by_agent: list[AgentMetric]
    by_tool: list[ToolMetric]
```

### Frontend `/admin/metrics` page

`frontend/app/admin/metrics/page.tsx` (NEW):

- Top bar: window selector (`1h` / `24h` / `7d` / `30d`).
- Totals card: queries, cost, anthropic calls, tool calls, errors.
- Per-agent table: agent, calls, cost, p50, p99, error rate.
- Per-tool table: tool, calls, p50, p99, error rate.
- 403 / "Admin only" message if the API returns 403.

The admin link only renders in the sidebar when the current user's email is in `ADMIN_EMAILS` (frontend reads the list from a new public env var `NEXT_PUBLIC_ADMIN_EMAILS` — yes the list is technically public, but it's just an allowlist of who SEES the link; the backend enforces real auth).

## 6. Frontend admin gating

`NEXT_PUBLIC_ADMIN_EMAILS` (Vercel env var, comma-separated). On render of the sidebar nav, compare the Supabase user's email against this list — if matched, show "Admin". Otherwise hide.

Public visibility of the admin email list is acceptable because:
1. It's just an allowlist for showing a link — the link goes to `/admin/metrics` which the backend gates independently.
2. Anyone curious can find the email in the page source; that's just informational.
3. If we hide the link from non-admins, we don't leak who's an admin to logged-in users.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Honeycomb unreachable | OTel's BatchSpanProcessor drops batches after retries; FastAPI continues responding normally. Logged as warning. |
| Sentry unreachable | Sentry SDK retries in background, drops after a backoff window. Doesn't block requests. |
| `/admin/metrics` query times out | Endpoint returns 504 with a "metrics aggregation timeout" message. SQL has no LIMIT on top tools etc.; the per-tool LIMIT 20 keeps it bounded. |
| Non-admin hits `/admin/metrics` | 403 with `{"detail": "admin only"}`. No data leaked. |
| `ADMIN_EMAILS` env empty | Every request 403 — endpoint is effectively disabled. |
| Honeycomb API key wrong format | OTel exporter sends spans; Honeycomb returns 401; spans dropped silently. Logged. |
| Sentry DSN malformed | `sentry_sdk.init` raises at startup. Currently caught and logged so the app continues — explicit `try/except` in `init_sentry()`. |

## 8. Performance + cost

- OTel overhead: ~1-3% wall-clock per request (BatchSpanProcessor exports asynchronously).
- Sentry overhead: negligible when no errors occur; trace_sample_rate=0 keeps it cheap.
- `/admin/metrics` query: SQL uses indexed `created_at` filter; <500ms even with 100K rows.
- Honeycomb free tier: 60-day retention, no card. Easily fits v1 traffic.
- Sentry free tier: 5K errors/month. Stylobate's error budget should be << that.
- Render free tier: no impact on resource usage; OTel batches are small.

## 9. Testing

| File | What |
|---|---|
| `test_core_tracing.py` | `init_tracing` is idempotent; provider configured even without API key; exporter present when key set. ~3 tests. |
| `test_core_sentry.py` | `init_sentry` no-ops on missing DSN; init succeeds on valid DSN (mock SDK). ~2 tests. |
| `test_anthropic_client_traced.py` | Wrapped `messages.create` sets span attributes (model, tokens). ~2 tests. |
| `test_routes_admin_metrics.py` | Admin gate: non-admin → 403; admin → 200 with shape. Window parser. ~6 tests. |
| `test_admin_metrics_sql.py` | SQL aggregates return expected shape on a fixture rowset (asyncpg mocked). ~3 tests. |

Frontend: one Vitest test (or just `npm run build` smoke) verifying the page renders + 403 message shows. No Playwright (the existing 4 stay green).

Phase 5A end-state: ~218 backend tests (202 today + ~16 new). ruff + mypy strict clean.

## 10. File map

```
backend/
  app/
    core/
      tracing.py                  NEW — OTel init + tracer getter
      sentry.py                   NEW — Sentry init
      anthropic_client.py         MODIFY — wrap messages.create with span
    agents/
      lead_banker.py              MODIFY — wrap run_lead_banker in span
      fundamental.py              MODIFY — wrap run_fundamental_analysis in span
      technical.py                MODIFY — wrap run_technical_analysis in span
      news_sentiment.py           MODIFY — wrap run_news_analysis in span
      macro.py                    MODIFY — wrap run_macro_analysis in span
      portfolio_strategist.py     MODIFY — wrap run_portfolio_strategist in span
      risk_manager.py             MODIFY — wrap run_risk_manager in span
      screener.py                 MODIFY — wrap run_screener in span
    routes/
      admin_metrics.py            NEW — GET /admin/metrics
    db/
      metrics.py                  NEW — SQL helpers
    models/
      admin_metrics.py            NEW — Pydantic response models
    main.py                       MODIFY — init_tracing + init_sentry + auto-instrument
  pyproject.toml                  MODIFY — add 7 OTel deps + sentry-sdk
  .env.example                    MODIFY — document SENTRY_DSN + HONEYCOMB_API_KEY + ADMIN_EMAILS
  tests/
    test_core_tracing.py          NEW
    test_core_sentry.py           NEW
    test_anthropic_client_traced.py NEW
    test_routes_admin_metrics.py  NEW
    test_admin_metrics_sql.py     NEW

frontend/
  app/admin/metrics/page.tsx      NEW
  lib/api-admin-metrics.ts        NEW
  components/admin-nav-link.tsx   NEW — conditional sidebar link
  components/sidebar-or-header.tsx (or chat-page) MODIFY — include admin-nav-link
  .env.local.example              MODIFY — document NEXT_PUBLIC_ADMIN_EMAILS

infra/
  render.yaml                     MODIFY — add SENTRY_DSN, HONEYCOMB_API_KEY,
                                   ADMIN_EMAILS as sync: false
```

## 11. End-of-Phase-5A acceptance

- ~218 backend tests pass; ruff + mypy strict clean.
- 4 Playwright tests still pass.
- Deployed: `https://stylobate-backend.onrender.com/admin/metrics?window=24h` returns 200 with rollup data for the admin user; 403 for non-admins.
- Honeycomb shows traces for live requests (each chat request appears as a span tree: FastAPI → Lead Banker → specialists → Anthropic calls → tool calls → asyncpg).
- Sentry receives a test exception when one is deliberately raised (via a `/admin/_test_error` endpoint scaffolded for verification then removed, or by triggering a known error path).
- All three providers degrade gracefully when env vars unset — local dev still works.

**Phase 5A complete.** Phase 5B (evals + adversarial harness) is the next sub-phase.
