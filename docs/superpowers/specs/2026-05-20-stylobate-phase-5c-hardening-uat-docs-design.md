# Stylobate — Phase 5C: Hardening + UAT + Docs Design

> **For agentic workers:** This is a sub-phase spec. After approval, invoke `superpowers:writing-plans` to produce the implementation plan at `docs/superpowers/plans/`.

**Parent spec:** `docs/superpowers/specs/2026-05-14-stylobate-design.md` (§15 Phase 5 — Ops & polish).

**Phase 5 decomposition:** Phase 5 was split into 5A (observability), 5B (evals + adversarial), and 5C (this — hardening + UAT + docs). 5C is independent of 5A and 5B and ships in parallel.

**Goal:** Get Stylobate to "public-beta ready" — runaway-cost protection, scrubbed error surfaces, a documented manual UAT checklist, secret rotation procedures, and the legal/about pages a public site needs. After 5C, you can confidently invite outside testers.

**Architecture:** Four loosely-coupled deliverables: (1) per-user rate limiting on LLM-costing routes (in-memory sliding window, single-instance — Stylobate is on Render's free tier), (2) tightened Pydantic input validation + a global exception handler that scrubs stack traces before they hit clients, (3) `docs/UAT.md` checklist + `docs/SECRETS.md` rotation inventory + three new Next.js pages (`/about`, `/terms`, `/privacy`), (4) a "hardening sweep" — automated grep against unsafe SQL patterns plus a manual security checklist.

**Tech Stack:** Existing — FastAPI, Pydantic v2, Next.js 16 + React 19. No new external dependencies. In-memory rate limit uses `asyncio.Lock` + a per-user `collections.deque`.

**Out of scope:**
- Email waitlist / signup form on landing page (post-v1 marketing).
- Custom domain wiring (use the existing `stylobate.vercel.app`).
- Analytics integration (Plausible/Umami). Add when public traffic justifies it.
- Cookie consent banner. Static legal pages without cookies don't require one in MVP scope.
- Marketing/screenshot-laden landing page. `/about` is plain text in v1.
- Distributed rate limiting (Redis / Postgres-backed). Documented as the upgrade path for multi-instance scale-out.
- A `/changelog` page. Deferred.

---

## 1. Architecture overview

```
1. Rate limiting (backend)
   - Per-user chat-query cap: 60 req/hr, sliding window
   - In-memory (single Render instance) — asyncio.Lock + deque
   - Returns 429 with X-RateLimit-* + Retry-After headers
   - Applied to /chat/stream, /chat/portfolio, /chat/screen, /resolve
   - NOT applied to portfolio/positions/watchlists CRUD (cheap)
   - Admin emails bypass the limit (unlimited for ops use)

2. Input validation + error scrubbing (backend)
   - Pydantic Field constraints tightened on user-supplied strings
   - Global FastAPI exception handler scrubs uncaught exceptions
     to "Internal error. The team has been notified." (Sentry has the trace)

3. Docs + landing pages
   - docs/UAT.md  - pre-launch manual test checklist
   - docs/SECRETS.md - secret rotation inventory + procedure
   - frontend/app/about/page.tsx - "What is Stylobate"
   - frontend/app/(legal)/terms/page.tsx - Terms of Service template
   - frontend/app/(legal)/privacy/page.tsx - Privacy Policy template
   - Footer linking the three pages

4. Hardening sweep
   - tests/test_security_grep.py — asserts no f-string SQL in app/db/
   - Manual checklist in docs/UAT.md: CORS allowlist, auth on every route,
     no committed passwords/secrets
   - render.yaml audit: no wildcard CORS in production
```

**Order in the plan:** rate limiting (smallest code change, biggest cost-control win) → validation + scrubbing → docs → landing pages → hardening sweep.

**No new infra dependencies.** In-memory rate limiting works because we're single-instance on Render free tier. If scale-out becomes necessary, swap the storage layer for `cache_kv` or Redis — the abstraction lives behind `rate_limit_check`.

## 2. Rate limiting

### New module `app/core/rate_limit.py`

```python
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, HTTPException

from app.core.auth import get_current_user

_WINDOW_SECONDS = 3600          # 1 hour
_DEFAULT_LIMIT = 60             # 60 requests/hour per user
_lock = asyncio.Lock()
_user_history: dict[str, deque[float]] = defaultdict(deque)


def _admin_emails() -> set[str]:
    return {
        e.strip().lower()
        for e in os.environ.get("ADMIN_EMAILS", "").split(",")
        if e.strip()
    }


def _per_user_limit(user: dict[str, Any]) -> int:
    """Admins are unlimited; everyone else gets the default."""
    email = (user.get("email") or "").strip().lower()
    if email and email in _admin_emails():
        return 10**9  # effectively unlimited
    return _DEFAULT_LIMIT


async def rate_limit_check(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """FastAPI dependency. Raises 429 when the user exceeds their hourly cap.
    Returns the user dict so downstream dependencies can keep using it."""
    user_id = user["sub"]
    now = time.monotonic()
    limit = _per_user_limit(user)
    async with _lock:
        history = _user_history[user_id]
        while history and history[0] < now - _WINDOW_SECONDS:
            history.popleft()
        if len(history) >= limit:
            oldest = history[0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded ({limit} requests/hour). "
                    f"Retry in {retry_after}s."
                ),
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(retry_after),
                },
            )
        history.append(now)
    return user
```

### Apply to LLM-costing routes

```python
# backend/app/routes/chat.py
# Change:
#   user: dict[str, Any] = Depends(get_current_user),
# To:
#   user: dict[str, Any] = Depends(rate_limit_check),
```

Same change in `chat_portfolio.py`, `chat_screen.py`, `resolve.py`. The dependency chains: `rate_limit_check` itself calls `Depends(get_current_user)`, so auth still runs first.

**NOT applied to:** `/portfolios/*`, `/positions/*`, `/watchlists/*`, `/prices*`, `/admin/*`, `/healthz`. These are either cheap reads or already gated separately.

### Reset behavior

The in-memory dict is process-local. On Render restart (deploys), all counters reset — acceptable for v1 (users can't game it because the restart is rare).

### Frontend behavior

When the client receives a 429 from `/chat/stream`, the existing chat error path renders the error message. No new frontend code needed — `apiFetch` already throws on non-2xx and the chat UI shows the error.

## 3. Input validation tightening

| File | Field | Old | New |
|---|---|---|---|
| `app/routes/chat.py` (`ChatRequest`) | `content` | `str` | `str = Field(min_length=1, max_length=4000)` |
| `app/routes/chat_portfolio.py` (`ChatPortfolioRequest`) | `message` | `str | None = None` | `str | None = Field(default=None, max_length=2000)` |
| `app/routes/chat_screen.py` (`ChatScreenRequest`) | `message` | `str` | `str = Field(min_length=1, max_length=500)` |
| `app/models/portfolio.py` (`PositionIn`) | `ticker` | `Field(min_length=1, max_length=20)` | `+ pattern=r"^[A-Z0-9.\-=^]+$"` |
| `app/models/watchlist.py` (`WatchlistItemIn`) | `ticker` | same | same `pattern` |

The ticker pattern allows the alphanumeric + `. - = ^` characters used by yfinance (`BRK.B`, `^GSPC`, `USDINR=X`, `BTC-USD`).

These changes don't break existing tests (existing fixtures use already-valid values) but reject obvious garbage and prompt-injection attempts at the API surface.

## 4. Error scrubbing

```python
# backend/app/main.py — add inside create_app()
import logging
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.main")


@app.exception_handler(Exception)
async def _scrubbed_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Sentry already has the full context;
    the client gets a generic message — no stack trace, no SQL, no secrets."""
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal error. The team has been notified."},
    )
```

This catches every exception FastAPI doesn't already convert to a structured error (validation → 422, HTTPException → its status). Sentry still captures the full trace; the client just sees the generic message.

## 5. Docs

### `docs/UAT.md` — pre-launch manual test checklist

A markdown checklist covering: auth flows, deep-dive (US/IN/crypto), portfolio CRUD + analyze, screener, RLS sanity, rate limiting trigger, documentation page resolution, deployment status, observability sanity (Honeycomb/Sentry if configured). Estimated 20-30 minutes to run end-to-end. Living document — each phase adds rows.

Example structure (full content in the plan):

```markdown
# Stylobate UAT Checklist

## Auth
- [ ] Sign up → user_profiles row created
- [ ] Sign in / sign out
- [ ] Forgotten-password (if implemented)

## Deep-dive
- [ ] "deep dive on AAPL" — 6 sections, citations present
- [ ] "deep dive on Reliance Industries" — INR currency in stock card, RBI in Macro
- [ ] "deep dive on BTC" — no Fundamentals section

## Portfolio
- [ ] Create portfolio, add positions (USD/INR/CRYPTO)
- [ ] Analyze portfolio → 8 sections including Concentration, VaR, Stress Tests

## Screener
- [ ] "AI infrastructure plays in India" → ≥5 .NS tickers
- [ ] "cheap US dividend stocks with PE < 20" → all candidates PE < 20

## Auth + RLS sanity
- [ ] User A cannot view User B's portfolio via direct URL
- [ ] Non-admin hits /admin/metrics → 403

## Rate limiting
- [ ] 61st chat query in an hour → 429 with Retry-After header
- [ ] Wait 60 min → 1st query succeeds

## Documentation
- [ ] /terms /privacy /about all resolve

## Deployment
- [ ] Render: latest commit live
- [ ] Vercel: latest commit live
- [ ] Honeycomb shows traces (if HONEYCOMB_API_KEY set)
- [ ] Sentry: no new errors from this session
```

### `docs/SECRETS.md` — rotation inventory

```markdown
# Stylobate secret inventory + rotation cadence

| Secret | Storage | Rotation cadence | Last rotated |
|---|---|---|---|
| ANTHROPIC_API_KEY | Render env, backend/.env (gitignored) | 90 days | 2026-05-15 |
| SUPABASE_DB_URL | Render env, backend/.env | 90 days | 2026-05-18 |
| SUPABASE_SERVICE_ROLE_KEY | Render env | 90 days | n/a |
| SENTRY_DSN | Render env | n/a (rotate only if leaked) | n/a |
| HONEYCOMB_API_KEY | Render env | 90 days | n/a |
| ADMIN_EMAILS | Render env | when allowlist changes | n/a |
| Test user password | Local only, never committed | when paper trail demands | 2026-05-18 |

## Rotation procedure
1. Generate new secret in the provider.
2. Update `SUPABASE_DB_URL` / `ANTHROPIC_API_KEY` / etc. in Render via the API:
   `curl -X PUT -H "Authorization: Bearer $RENDER_KEY" ... /env-vars/<KEY>`
3. Trigger a redeploy.
4. Wait for `live` status; verify `/healthz` returns 200.
5. Update this doc's "Last rotated" column and commit.

## On suspected leak
- ANTHROPIC_API_KEY: revoke in console, regenerate, propagate.
- SUPABASE_DB_URL: reset password in Supabase dashboard → grab new pooler URL → propagate.
- SENTRY_DSN: rotate client key in Sentry project settings.
```

## 6. Landing pages

Three Next.js routes. Static-rendered (no client state). Linked from a footer added to the existing layout.

### `frontend/app/about/page.tsx`

```tsx
export const metadata = { title: "About Stylobate", description: "Multi-agent investment research for retail investors." };

export default function AboutPage() {
  return (
    <main className="container mx-auto py-12 max-w-2xl prose dark:prose-invert">
      <h1>About Stylobate</h1>
      <p>
        Stylobate is a multi-agent AI tool for retail investors. Ask it to
        deep-dive a stock, review your portfolio, run risk scenarios, or
        find candidates matching a theme — and it routes your question
        through specialist agents (Fundamental, Technical, News, Macro,
        Portfolio Strategist, Risk Manager, Screener) that combine to
        give you a research note with citations.
      </p>
      <h2>What Stylobate is NOT</h2>
      <ul>
        <li>Not personalized financial advice. We are not your advisor.</li>
        <li>Not a brokerage. Stylobate doesn't execute trades.</li>
        <li>Not a substitute for your own due diligence and tax planning.</li>
      </ul>
      <h2>Built by</h2>
      <p>Rakhi Sinha · GitHub <a href="https://github.com/RakhiIsTrying/stylobate">stylobate</a></p>
      <p>
        Have feedback? <a href="mailto:rakhisinha100896@gmail.com">Get in touch.</a>
      </p>
    </main>
  );
}
```

### `frontend/app/(legal)/terms/page.tsx`

A template that says, explicitly: this is placeholder text and should be reviewed by a lawyer before public launch. Covers: definition of service, "not financial advice" disclaimer, user obligations, IP rights, liability disclaimers, jurisdiction. Markdown-style sections.

Top of the file:

```tsx
// REVIEW BEFORE PUBLIC LAUNCH — placeholder legal text generated as a
// starting point. Have a lawyer or experienced founder review specifics.

export const metadata = { title: "Terms of Service — Stylobate" };

export default function TermsPage() {
  const lastUpdated = "May 20, 2026";
  return (
    <main className="container mx-auto py-12 max-w-2xl prose dark:prose-invert">
      <h1>Terms of Service</h1>
      <p className="text-sm text-muted-foreground">Last updated: {lastUpdated}</p>
      <p className="text-sm bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-100 p-3 rounded">
        ⚠️ This is a beta. The full terms are still being drafted. By using
        Stylobate you agree that (1) the service is provided as-is, (2) nothing
        here is personalized financial advice, (3) you accept full responsibility
        for your investment decisions, and (4) we may change or shut down the
        service at any time.
      </p>
      {/* The full Terms structure: definitions, scope, restrictions, IP, etc. */}
      <h2>1. Service description</h2>
      <p>...</p>
      ...
    </main>
  );
}
```

### `frontend/app/(legal)/privacy/page.tsx`

Same template pattern: placeholder text covering what data we collect (auth email, portfolio entries the user inputs), what we don't (no third-party ad networks, no analytics in v1), where it's stored (Supabase EU/US), retention (until user deletes account), how to delete data (email request — automated flow is post-v1).

### Footer (modify existing layout)

Add a footer block to `frontend/app/layout.tsx` (or `chat/layout.tsx` / `portfolio/layout.tsx` if there's no root layout footer):

```tsx
<footer className="border-t mt-8 py-4 text-xs text-muted-foreground text-center">
  <Link href="/about">About</Link>
  {" · "}
  <Link href="/terms">Terms</Link>
  {" · "}
  <Link href="/privacy">Privacy</Link>
  {" · "}
  <a href="https://github.com/RakhiIsTrying/stylobate">GitHub</a>
</footer>
```

The footer should appear on auth'd pages and the new legal/about pages. Sign-in / sign-up may skip it.

## 7. Hardening sweep

### Automated: `tests/test_security_grep.py`

```python
import re
from pathlib import Path

import pytest

_BACKEND_APP = Path(__file__).parent.parent / "app"

# Patterns to forbid in app/ source (NOT tests/, which have controlled fixtures)
_UNSAFE_SQL_PATTERN = re.compile(
    r'(?:execute|fetch|fetchrow)\s*\([\'"]\s*[^\']*\{',
    re.MULTILINE,
)


def test_no_fstring_sql_in_app() -> None:
    """Asserts no f-string in *.py inside app/db/ where SQL lives."""
    db_dir = _BACKEND_APP / "db"
    offenders: list[str] = []
    for py in db_dir.rglob("*.py"):
        text = py.read_text()
        # f-string literals starting with f" or f' followed by SQL keywords
        for m in re.finditer(r'f["\'][^"\'\n]*(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)', text, re.IGNORECASE):
            offenders.append(f"{py.relative_to(_BACKEND_APP.parent)}: {m.group()[:60]}")
    assert not offenders, "f-string SQL detected (use $1 parameters): " + "; ".join(offenders)


def test_no_committed_secret_patterns() -> None:
    """Scan committed source for common secret shapes that shouldn't be there."""
    # Anthropic key prefix
    _BAD = [
        re.compile(r"sk-ant-api03-[A-Za-z0-9_\-]{40,}"),       # actual Anthropic key
        re.compile(r"postgres:[^\s/@]+@db\.[a-z0-9]+\.supabase\.co"),  # DB URL with password
    ]
    offenders: list[str] = []
    for py in _BACKEND_APP.rglob("*.py"):
        text = py.read_text()
        for pat in _BAD:
            if pat.search(text):
                offenders.append(str(py.relative_to(_BACKEND_APP.parent)))
                break
    assert not offenders, "secret-shaped string in source: " + "; ".join(offenders)
```

### Manual checklist (documented in UAT.md and §5)

| Check | How |
|---|---|
| Every route requires auth or is explicitly public | `grep -L "Depends(get_current_user)" app/routes/*.py` should yield only `health.py` |
| CORS allowlist has no wildcard in production | `grep CORS render.yaml` returns explicit origins only |
| `.env` files are gitignored | `git check-ignore backend/.env frontend/.env.local` both succeed |
| No raw f-string SQL | `pytest tests/test_security_grep.py` passes |
| Test user password not committed | `git grep "Stylobate2026" -- ':(exclude)docs'` returns nothing in source |
| `ADMIN_EMAILS` is set on Render production | manual check |

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Rate limit triggered | 429 with `Retry-After` header; chat UI surfaces the message |
| Validation rejection on tightened Pydantic field | 422 with field-level error (existing FastAPI behavior) |
| Uncaught exception in route | scrubbed 500 to client; full trace to Sentry |
| Admin email allowlist empty | Admins get the default 60/hr limit (not unlimited) — fail-closed |
| `ADMIN_EMAILS` env unset on Render | Same as above |
| Legal page rendered when not yet finalized | Visible warning banner ("This is a beta. Full terms are being drafted…") |

## 9. Testing

| File | What |
|---|---|
| `tests/test_core_rate_limit.py` | Sliding window: 60 succeed, 61st → 429; window resets after 1h; admins unlimited. ~5 tests. |
| `tests/test_validation_tightening.py` | Tightened Pydantic models reject too-long content, invalid ticker chars. ~4 tests. |
| `tests/test_error_scrubbing.py` | An exception raised inside a route returns the generic 500 message, never the trace. ~2 tests. |
| `tests/test_security_grep.py` | The 2 grep tests from §7. |

Frontend: smoke test that `npm run build` succeeds (the new pages don't break TypeScript). No new Playwright tests; existing 4 stay green.

Phase 5C end-state: ~215 backend tests (208 prior including 5B meta + ~13 new in 5C). ruff + mypy strict clean. All 4 Playwright tests still pass.

## 10. File map

```
backend/
  app/
    core/
      rate_limit.py                 NEW
    routes/
      chat.py                       MODIFY — use rate_limit_check + tighten content validator
      chat_portfolio.py             MODIFY — rate_limit_check + message validator
      chat_screen.py                MODIFY — rate_limit_check + message validator
      resolve.py                    MODIFY — rate_limit_check
    models/
      portfolio.py                  MODIFY — ticker pattern
      watchlist.py                  MODIFY — ticker pattern
    main.py                         MODIFY — global exception handler
  tests/
    test_core_rate_limit.py         NEW
    test_validation_tightening.py   NEW
    test_error_scrubbing.py         NEW
    test_security_grep.py           NEW

frontend/
  app/
    layout.tsx                      MODIFY — footer (or add a footer component)
    about/
      page.tsx                      NEW
    (legal)/
      terms/
        page.tsx                    NEW
      privacy/
        page.tsx                    NEW
  components/
    footer.tsx                      NEW

docs/
  UAT.md                            NEW
  SECRETS.md                        NEW
```

## 11. End-of-Phase-5C acceptance

- Backend: ~215 tests passing; ruff + mypy strict clean.
- Frontend: `npm run build` succeeds; `npm run lint` clean.
- Existing 4 Playwright tests still pass.
- Manual verification: 61st chat query in an hour returns 429 with Retry-After.
- Manual verification: hit `/about` `/terms` `/privacy` — all three render.
- Manual verification: `docs/UAT.md` and `docs/SECRETS.md` are present and committed.
- Manual verification: `pytest tests/test_security_grep.py` passes.
- Render auto-deploy + Vercel deploy green.

**Phase 5C complete.** Combined with 5A (observability — spec'd) and 5B (evals + adversarial — spec'd), the parent spec's Phase 5 is fully designed. Order of implementation can vary — all three sub-phases ship independently.
