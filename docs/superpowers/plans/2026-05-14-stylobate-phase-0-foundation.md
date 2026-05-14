# Stylobate — Phase 0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working chat skeleton: a user can sign up via Supabase, type a message, and get an echo streamed back via SSE. All infrastructure (monorepo, CI, auth, basic schema, RLS) in place.

**Architecture:** Monorepo with two services. Python FastAPI backend handles SSE streaming and verifies Supabase JWTs. Next.js 15 frontend (App Router) handles auth UI, chat shell, and renders streaming responses. Supabase provides Postgres + Auth + RLS. No agents yet — that's Phase 1.

**Tech Stack:** Python 3.11 (uv), FastAPI, anthropic-sdk (installed but unused this phase), structlog, pytest + respx; Next.js 15, TypeScript, Tailwind, shadcn/ui, @supabase/ssr; Supabase Postgres + Auth.

**Spec reference:** `docs/superpowers/specs/2026-05-14-stylobate-design.md`. This plan implements §2 goals, §3 layout shell only, §6 stack/endpoints/CI scaffolding, §7 schema for `user_profiles`, `chats`, `messages`, `agent_memory`, `tool_calls`, `model_runs`, `cache_kv` (Phase 0 only fills user/chat/messages with data; other tables are created empty), §13 deployment-prep (CI + env templates; actual deploy is later).

**Out of scope here (covered by later phases):**
- Agents (Phase 1+)
- Tool functions, data adapters (Phase 1+)
- Portfolio CRUD + positions / tax_lots schema fill (Phase 3)
- Watchlists, screener (Phase 4)
- OpenTelemetry, Sentry, dashboards (Phase 5)

---

## File map for Phase 0

```
stylobate/                                  (repo root, git-init already done in setup)
├── .env.example                            CREATE
├── .gitignore                              EXISTS (extend lightly)
├── Makefile                                CREATE
├── README.md                               CREATE
├── .github/
│   └── workflows/
│       ├── backend-ci.yml                  CREATE
│       └── frontend-ci.yml                 CREATE
├── backend/
│   ├── .python-version                     CREATE
│   ├── pyproject.toml                      CREATE
│   ├── uv.lock                             CREATE (generated)
│   ├── app/
│   │   ├── __init__.py                     CREATE
│   │   ├── main.py                         CREATE
│   │   ├── config.py                       CREATE
│   │   ├── core/
│   │   │   ├── __init__.py                 CREATE
│   │   │   ├── auth.py                     CREATE
│   │   │   ├── logging.py                  CREATE
│   │   │   └── supabase_client.py          CREATE
│   │   ├── routes/
│   │   │   ├── __init__.py                 CREATE
│   │   │   ├── health.py                   CREATE
│   │   │   └── chat.py                     CREATE
│   │   └── db/
│   │       ├── __init__.py                 CREATE
│   │       ├── models.py                   CREATE
│   │       └── messages.py                 CREATE
│   └── tests/
│       ├── __init__.py                     CREATE
│       ├── conftest.py                     CREATE
│       ├── test_health.py                  CREATE
│       ├── test_config.py                  CREATE
│       ├── test_auth.py                    CREATE
│       ├── test_chat_stream.py             CREATE
│       └── test_messages_repo.py           CREATE
├── frontend/
│   ├── package.json                        CREATE
│   ├── tsconfig.json                       CREATE
│   ├── next.config.ts                      CREATE
│   ├── tailwind.config.ts                  CREATE
│   ├── postcss.config.mjs                  CREATE
│   ├── components.json                     CREATE  (shadcn config)
│   ├── eslint.config.mjs                   CREATE
│   ├── middleware.ts                       CREATE
│   ├── app/
│   │   ├── layout.tsx                      CREATE
│   │   ├── page.tsx                        CREATE
│   │   ├── globals.css                     CREATE
│   │   ├── (auth)/sign-in/page.tsx         CREATE
│   │   ├── (auth)/sign-up/page.tsx         CREATE
│   │   ├── (auth)/callback/route.ts        CREATE
│   │   └── chat/page.tsx                   CREATE
│   ├── components/
│   │   ├── chat-thread.tsx                 CREATE
│   │   ├── composer.tsx                    CREATE
│   │   └── user-menu.tsx                   CREATE
│   ├── lib/
│   │   ├── supabase/
│   │   │   ├── client.ts                   CREATE
│   │   │   ├── server.ts                   CREATE
│   │   │   └── middleware.ts               CREATE
│   │   ├── sse.ts                          CREATE
│   │   └── types.ts                        CREATE
│   ├── public/                             CREATE (empty)
│   └── __tests__/
│       └── sse.test.ts                     CREATE
└── supabase/
    ├── config.toml                         CREATE
    ├── seed.sql                            CREATE  (empty placeholder)
    └── migrations/
        ├── 20260514000001_initial_schema.sql  CREATE
        └── 20260514000002_rls_policies.sql    CREATE
```

Each task below maps to one focused file or a tightly coupled small set of files.

---

## Task 1: Repo-root scaffolding (Makefile, README, .env.example)

**Files:**
- Create: `Makefile`
- Create: `README.md`
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# .env.example
# Copy to .env.local (frontend) and backend/.env, then fill in.

# --- Supabase (shared) ---
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=ey...                   # public, safe to expose to browser
SUPABASE_SERVICE_ROLE_KEY=ey...           # server-only, NEVER expose

# --- Backend ---
BACKEND_PORT=8000
BACKEND_LOG_LEVEL=INFO
BACKEND_CORS_ORIGINS=http://localhost:3000

# --- Frontend ---
NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL}
NEXT_PUBLIC_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000

# --- Anthropic (Phase 1+; harmless to leave empty in Phase 0) ---
ANTHROPIC_API_KEY=sk-ant-...
```

Write this verbatim to `.env.example` at the repo root.

- [ ] **Step 2: Create `Makefile`**

```makefile
.PHONY: help install backend frontend test lint check fmt

help:
	@echo "Stylobate dev targets:"
	@echo "  make install     - install backend + frontend deps"
	@echo "  make backend     - run backend on :8000"
	@echo "  make frontend    - run frontend on :3000"
	@echo "  make test        - run backend + frontend tests"
	@echo "  make lint        - lint everything"
	@echo "  make fmt         - autoformat everything"
	@echo "  make check       - lint + typecheck + tests"

install:
	cd backend && uv sync
	cd frontend && npm install

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q
	cd frontend && npm test --silent

lint:
	cd backend && uv run ruff check . && uv run mypy app
	cd frontend && npm run lint

fmt:
	cd backend && uv run ruff format .
	cd frontend && npm run fmt

check: lint test
```

Write verbatim to `Makefile`. Use real tabs (the syntax requires them).

- [ ] **Step 3: Create `README.md`**

```markdown
# Stylobate

Multi-agent AI tool with the analytical horsepower of an investment-banking team — for a single retail investor. Covers US + India equities, ETFs, and major crypto. Advisory only.

## Architecture

See `docs/superpowers/specs/2026-05-14-stylobate-design.md`.

- **Frontend:** Next.js 15 (App Router, TypeScript) + Tailwind + shadcn/ui
- **Backend:** Python 3.11 + FastAPI + Claude API (Anthropic SDK)
- **Database:** Supabase Postgres + Auth + RLS

## Quickstart

```bash
# 1. Create a Supabase project at https://supabase.com/dashboard
#    and copy SUPABASE_URL + keys into .env.example -> .env files.
cp .env.example .env
cp .env.example backend/.env
cp .env.example frontend/.env.local

# 2. Install dependencies
make install

# 3. Apply migrations to Supabase (one-time)
#    Either: paste supabase/migrations/*.sql into the SQL editor in the dashboard,
#    Or:    supabase link --project-ref <ref> && supabase db push

# 4. Run dev servers (in two terminals)
make backend
make frontend
```

Open http://localhost:3000.

## Layout

- `frontend/` — Next.js app
- `backend/` — FastAPI service
- `supabase/migrations/` — versioned SQL migrations
- `docs/superpowers/` — design spec + implementation plans

## Status

Phase 0 (Foundation) — see `docs/superpowers/plans/2026-05-14-stylobate-phase-0-foundation.md`.
```

Write verbatim to `README.md`.

- [ ] **Step 4: Verify and commit**

```bash
cd /Users/rakhisinha/Stylobate
ls -1 .env.example Makefile README.md
# Expected: all three listed without error.
make help
# Expected: prints the help text.
```

```bash
git add .env.example Makefile README.md
git commit -m "chore: add repo-root scaffolding (Makefile, README, .env.example)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Backend bootstrap (FastAPI + /healthz with TDD)

**Files:**
- Create: `backend/.python-version`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/routes/__init__.py`
- Create: `backend/app/routes/health.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Pin Python version**

```bash
echo "3.11" > backend/.python-version
```

- [ ] **Step 2: Initialize uv project with dependencies**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv init --no-readme --no-pin-python --package
uv add fastapi 'uvicorn[standard]' pydantic-settings httpx structlog 'anthropic>=0.40.0' 'supabase>=2.7.0' python-jose[cryptography] python-multipart
uv add --dev pytest pytest-asyncio respx ruff mypy 'types-python-jose' 'types-requests'
```

This creates `pyproject.toml` and `uv.lock`. Then edit `pyproject.toml` to add the tool sections below.

- [ ] **Step 3: Configure ruff/mypy/pytest in pyproject.toml**

Append to `backend/pyproject.toml` (after the `[project]` block uv generated):

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "RUF"]
ignore = ["B008"]  # FastAPI Depends() in defaults is fine

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- [ ] **Step 4: Create `backend/tests/__init__.py` and `backend/tests/conftest.py`**

```python
# backend/tests/__init__.py
```
(Empty file.)

```python
# backend/tests/conftest.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 5: Write the failing test for /healthz**

```python
# backend/tests/test_health.py
import pytest


@pytest.mark.asyncio
async def test_healthz_returns_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "stylobate-backend"}
```

- [ ] **Step 6: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_health.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.main'` (or similar — `app.main` doesn't exist yet).

- [ ] **Step 7: Create `backend/app/__init__.py`** (empty) **and `backend/app/routes/__init__.py`** (empty)

```bash
touch backend/app/__init__.py backend/app/routes/__init__.py
```

- [ ] **Step 8: Create `backend/app/routes/health.py`**

```python
# backend/app/routes/health.py
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "stylobate-backend"}
```

- [ ] **Step 9: Create `backend/app/main.py`**

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="Stylobate Backend", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 10: Re-run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_health.py -v
```

Expected: `1 passed`.

- [ ] **Step 11: Smoke-test the live server**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run uvicorn app.main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/healthz
kill %1 2>/dev/null
```

Expected curl output: `{"status":"ok","service":"stylobate-backend"}`.

- [ ] **Step 12: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/.python-version backend/pyproject.toml backend/uv.lock backend/app backend/tests
git commit -m "feat(backend): bootstrap FastAPI service with /healthz

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Pydantic Settings (env-driven config)

**Files:**
- Create: `backend/app/config.py`
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
import os
from unittest.mock import patch


def test_settings_load_from_env():
    env = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000,https://stylobate.app",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=False):
        from app.config import Settings
        s = Settings()
    assert s.supabase_url == "https://test.supabase.co"
    assert s.supabase_anon_key == "anon"
    assert s.cors_origins == ["http://localhost:3000", "https://stylobate.app"]


def test_settings_default_cors():
    env = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "anon",
        "SUPABASE_SERVICE_ROLE_KEY": "service",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=True):
        from app.config import Settings
        s = Settings()
    assert s.cors_origins == ["http://localhost:3000"]
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Create `backend/app/config.py`**

```python
# backend/app/config.py
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_anon_key: str = Field(alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(alias="SUPABASE_SERVICE_ROLE_KEY")
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")

    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    backend_log_level: str = Field(default="INFO", alias="BACKEND_LOG_LEVEL")
    cors_origins_raw: str = Field(
        default="http://localhost:3000",
        alias="BACKEND_CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Re-run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_config.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Wire `get_settings()` into `app/main.py`**

Edit `backend/app/main.py`:

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import health


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Stylobate Backend", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 6: Create test `.env` for local runs**

Create `backend/.env` (this file is gitignored — `.env` is already in the root `.gitignore`):

```bash
cd /Users/rakhisinha/Stylobate
cp .env.example backend/.env
# Edit backend/.env later with real values; placeholders fine for now.
```

- [ ] **Step 7: Confirm all tests still pass**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -q
```

Expected: `3 passed`.

- [ ] **Step 8: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/config.py backend/app/main.py backend/tests/test_config.py
git commit -m "feat(backend): add Pydantic Settings, wire CORS from env

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Supabase migration — core schema

**Files:**
- Create: `supabase/config.toml`
- Create: `supabase/migrations/20260514000001_initial_schema.sql`
- Create: `supabase/seed.sql` (empty placeholder)

This task creates the migration SQL files. Applying them to a Supabase project happens in Task 5.

- [ ] **Step 1: Create `supabase/config.toml`**

```toml
# supabase/config.toml
project_id = "stylobate"

[api]
enabled = true
port = 54321
schemas = ["public", "graphql_public"]
extra_search_path = ["public", "extensions"]
max_rows = 1000

[db]
port = 54322
shadow_port = 54320
major_version = 15

[studio]
enabled = true
port = 54323

[auth]
enabled = true
site_url = "http://localhost:3000"
additional_redirect_urls = ["http://localhost:3000/auth/callback"]
jwt_expiry = 3600
enable_signup = true

[auth.email]
enable_signup = true
double_confirm_changes = true
enable_confirmations = false  # disable email confirm for local dev
```

- [ ] **Step 2: Create `supabase/seed.sql`** (empty placeholder)

```sql
-- supabase/seed.sql
-- Seed data placeholder. Populated in later phases.
```

- [ ] **Step 3: Create the initial schema migration**

`supabase/migrations/20260514000001_initial_schema.sql`:

```sql
-- 20260514000001_initial_schema.sql
-- Phase 0 schema: user_profiles, chats, messages, agent_memory,
-- tool_calls, model_runs, cache_kv. Empty tables for portfolios,
-- positions, watchlists, etc. are added in later phases.

set search_path = public;

-- ============================================================================
-- user_profiles : one row per auth.users row
-- ============================================================================
create table if not exists user_profiles (
  id              uuid primary key references auth.users(id) on delete cascade,
  default_market  text not null check (default_market in ('US','IN')) default 'US',
  default_currency char(3) not null default 'USD',
  risk_tolerance  text check (risk_tolerance in ('conservative','balanced','aggressive')),
  created_at      timestamptz not null default now()
);

-- Auto-create a profile when a new auth user signs up
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.user_profiles (id) values (new.id);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ============================================================================
-- chats : conversations
-- ============================================================================
create table if not exists chats (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references user_profiles(id) on delete cascade,
  title            text,
  model            text not null default 'claude-opus-4-7',
  created_at       timestamptz not null default now(),
  last_message_at  timestamptz not null default now()
);

create index if not exists chats_user_id_idx on chats(user_id, last_message_at desc);

-- ============================================================================
-- messages : ordered messages per chat
-- ============================================================================
create table if not exists messages (
  id          uuid primary key default gen_random_uuid(),
  chat_id     uuid not null references chats(id) on delete cascade,
  role        text not null check (role in ('user','assistant','system')),
  content     jsonb not null,            -- assistant: ordered array of typed blocks
  created_at  timestamptz not null default now()
);

create index if not exists messages_chat_id_idx on messages(chat_id, created_at);

-- ============================================================================
-- agent_memory : per-user cross-session memory the orchestrator reads/writes
-- ============================================================================
create table if not exists agent_memory (
  user_id     uuid not null references user_profiles(id) on delete cascade,
  key         text not null,
  value       jsonb not null,
  updated_at  timestamptz not null default now(),
  primary key (user_id, key)
);

-- ============================================================================
-- tool_calls : observability for tool invocations inside an agent turn
-- ============================================================================
create table if not exists tool_calls (
  id          uuid primary key default gen_random_uuid(),
  message_id  uuid not null references messages(id) on delete cascade,
  agent       text not null,
  tool        text not null,
  input       jsonb,
  output      jsonb,
  latency_ms  integer,
  error       text,
  created_at  timestamptz not null default now()
);

create index if not exists tool_calls_message_id_idx on tool_calls(message_id);

-- ============================================================================
-- model_runs : per LLM call cost/token accounting
-- ============================================================================
create table if not exists model_runs (
  id                  uuid primary key default gen_random_uuid(),
  message_id          uuid references messages(id) on delete cascade,
  agent               text not null,
  model               text not null,
  input_tokens        integer not null default 0,
  output_tokens       integer not null default 0,
  cache_read_tokens   integer not null default 0,
  cache_write_tokens  integer not null default 0,
  cost_usd            numeric(10, 6) not null default 0,
  created_at          timestamptz not null default now()
);

create index if not exists model_runs_message_id_idx on model_runs(message_id);

-- ============================================================================
-- cache_kv : opaque KV cache for expensive tool results (server-only)
-- ============================================================================
create table if not exists cache_kv (
  key         text primary key,
  value       jsonb not null,
  expires_at  timestamptz not null
);

create index if not exists cache_kv_expires_idx on cache_kv(expires_at);
```

- [ ] **Step 4: Verify SQL parses (best-effort lint)**

If `psql` is not available, skip syntactic verification — the file will be applied in Task 5 and any errors will surface there. Otherwise:

```bash
# Optional: requires postgres client tools
psql --version
# Then a dry-parse can be done via `pg_format` or pasting into the Supabase SQL editor
```

- [ ] **Step 5: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add supabase/
git commit -m "feat(db): add initial schema migration (Phase 0 tables)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Supabase RLS policies

**Files:**
- Create: `supabase/migrations/20260514000002_rls_policies.sql`

- [ ] **Step 1: Create the RLS migration**

```sql
-- 20260514000002_rls_policies.sql
-- Enable RLS on all user-owned tables and add policies scoping reads/writes
-- to auth.uid().

set search_path = public;

-- ============================================================================
-- user_profiles
-- ============================================================================
alter table user_profiles enable row level security;

drop policy if exists "user_profiles_select_own" on user_profiles;
create policy "user_profiles_select_own"
  on user_profiles for select
  using (id = auth.uid());

drop policy if exists "user_profiles_update_own" on user_profiles;
create policy "user_profiles_update_own"
  on user_profiles for update
  using (id = auth.uid())
  with check (id = auth.uid());

-- inserts handled by the trigger as a security-definer function; no policy needed

-- ============================================================================
-- chats
-- ============================================================================
alter table chats enable row level security;

drop policy if exists "chats_owner_all" on chats;
create policy "chats_owner_all"
  on chats for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ============================================================================
-- messages : owned via chat
-- ============================================================================
alter table messages enable row level security;

drop policy if exists "messages_owner_all" on messages;
create policy "messages_owner_all"
  on messages for all
  using (
    exists (
      select 1 from chats c
      where c.id = messages.chat_id and c.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from chats c
      where c.id = messages.chat_id and c.user_id = auth.uid()
    )
  );

-- ============================================================================
-- agent_memory
-- ============================================================================
alter table agent_memory enable row level security;

drop policy if exists "agent_memory_owner_all" on agent_memory;
create policy "agent_memory_owner_all"
  on agent_memory for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ============================================================================
-- tool_calls : owned via message -> chat
-- ============================================================================
alter table tool_calls enable row level security;

drop policy if exists "tool_calls_owner_select" on tool_calls;
create policy "tool_calls_owner_select"
  on tool_calls for select
  using (
    exists (
      select 1 from messages m join chats c on c.id = m.chat_id
      where m.id = tool_calls.message_id and c.user_id = auth.uid()
    )
  );
-- inserts only via service role

-- ============================================================================
-- model_runs : owned via message -> chat (read-only for owner)
-- ============================================================================
alter table model_runs enable row level security;

drop policy if exists "model_runs_owner_select" on model_runs;
create policy "model_runs_owner_select"
  on model_runs for select
  using (
    exists (
      select 1 from messages m join chats c on c.id = m.chat_id
      where m.id = model_runs.message_id and c.user_id = auth.uid()
    )
  );

-- ============================================================================
-- cache_kv : service role only — no end-user policies
-- ============================================================================
alter table cache_kv enable row level security;
-- No policies => no end-user can read or write. Service role bypasses RLS.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add supabase/migrations/20260514000002_rls_policies.sql
git commit -m "feat(db): add RLS policies for Phase 0 tables

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Provision the Supabase project (manual user step)**

This is an out-of-band action. Provide these instructions to the executor / user:

1. Go to https://supabase.com/dashboard. Sign in.
2. Click **New project**. Name: `stylobate`. Region: closest. Database password: generate and save.
3. Wait ~2 minutes for provisioning.
4. Once ready, go to **Settings → API**. Copy: `Project URL`, `anon public key`, `service_role secret`.
5. Paste into `backend/.env` and `frontend/.env.local`:
   - `SUPABASE_URL=https://<ref>.supabase.co`
   - `SUPABASE_ANON_KEY=eyJ...`  (anon)
   - `SUPABASE_SERVICE_ROLE_KEY=eyJ...`  (service_role)
   - In frontend, the public ones go under `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
6. Apply the migrations. Two options:
   - **SQL Editor (simplest)**: open Supabase dashboard → SQL Editor → paste contents of `20260514000001_initial_schema.sql`, run; then `20260514000002_rls_policies.sql`, run.
   - **CLI**: `npm i -g supabase`, then `supabase login`, `supabase link --project-ref <ref>`, `supabase db push`.

- [ ] **Step 4: Verify the schema applied**

In the Supabase SQL Editor:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;
```

Expected rows (in any order):
- `agent_memory`
- `cache_kv`
- `chats`
- `messages`
- `model_runs`
- `tool_calls`
- `user_profiles`

```sql
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
order by tablename;
```

Expected: `rowsecurity = true` for every row.

---

## Task 6: Backend Supabase client + JWT auth dependency

**Files:**
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/supabase_client.py`
- Create: `backend/app/core/auth.py`
- Create: `backend/tests/test_auth.py`

- [ ] **Step 1: Create `backend/app/core/__init__.py`** (empty)

```bash
touch backend/app/core/__init__.py
```

- [ ] **Step 2: Create the Supabase client factory**

`backend/app/core/supabase_client.py`:

```python
# backend/app/core/supabase_client.py
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache(maxsize=1)
def get_service_client() -> Client:
    """Service-role client. Bypasses RLS. Use only on server, never expose."""
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_user_client(jwt: str) -> Client:
    """Per-request client scoped to the authenticated user via RLS."""
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(jwt)
    return client
```

- [ ] **Step 3: Write the failing test for JWT auth**

`backend/tests/test_auth.py`:

```python
# backend/tests/test_auth.py
import time
import uuid

import pytest
from jose import jwt


SECRET = "test-secret-for-unit-tests-only"  # nosec


def _make_token(sub: str | None = None, exp_offset: int = 3600) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": sub or str(uuid.uuid4()),
        "aud": "authenticated",
        "exp": now + exp_offset,
        "iat": now,
        "role": "authenticated",
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_decode_valid_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import decode_jwt
    sub = str(uuid.uuid4())
    token = _make_token(sub=sub)
    payload = decode_jwt(token)
    assert payload["sub"] == sub
    assert payload["aud"] == "authenticated"


@pytest.mark.asyncio
async def test_decode_expired_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import AuthError, decode_jwt
    token = _make_token(exp_offset=-10)
    with pytest.raises(AuthError):
        decode_jwt(token)


@pytest.mark.asyncio
async def test_decode_bad_signature(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.core.auth import AuthError, decode_jwt
    bad = jwt.encode({"sub": "x", "aud": "authenticated"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_jwt(bad)
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_auth.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.core.auth'`.

- [ ] **Step 5: Extend `Settings` with `supabase_jwt_secret`**

Edit `backend/app/config.py`:

```python
# backend/app/config.py
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_anon_key: str = Field(alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = Field(default="", alias="SUPABASE_JWT_SECRET")
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")

    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    backend_log_level: str = Field(default="INFO", alias="BACKEND_LOG_LEVEL")
    cors_origins_raw: str = Field(
        default="http://localhost:3000",
        alias="BACKEND_CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

Also add to `.env.example` under `# --- Supabase (shared) ---`:

```bash
SUPABASE_JWT_SECRET=...                   # Settings → API → JWT Secret, NOT a key
```

- [ ] **Step 6: Create `backend/app/core/auth.py`**

```python
# backend/app/core/auth.py
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings


class AuthError(Exception):
    pass


def decode_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise AuthError("SUPABASE_JWT_SECRET not configured")
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as e:
        raise AuthError(str(e)) from e


def get_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return decode_jwt(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


CurrentUser = Depends(get_current_user)
```

- [ ] **Step 7: Update `conftest.py` fixture to set the JWT secret**

Edit `backend/tests/conftest.py`:

```python
# backend/tests/conftest.py
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-test")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret-for-unit-tests-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # Bust the lru_cache so each test re-reads env
    from app.config import get_settings
    get_settings.cache_clear()
    yield


@pytest.fixture
async def client():
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 8: Re-run all backend tests**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -v
```

Expected: all tests pass (5 total: 1 health + 2 config + 3 auth — note `test_config.py` may now need its monkeypatch updated; if a test fails because env is double-set, drop the conflicting lines inside `test_config.py` since the autouse fixture provides defaults).

If `test_config.py` tests break: edit `backend/tests/test_config.py` to delete the duplicate env vars that are now in the autouse fixture, and rely on the fixture's defaults plus assertions about them.

- [ ] **Step 9: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/core backend/app/config.py backend/tests/test_auth.py backend/tests/conftest.py .env.example
git commit -m "feat(backend): add Supabase client factories + JWT auth dependency

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Messages repository (DB write)

**Files:**
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/models.py`
- Create: `backend/app/db/messages.py`
- Create: `backend/tests/test_messages_repo.py`

- [ ] **Step 1: Create `backend/app/db/__init__.py`** (empty)

```bash
touch backend/app/db/__init__.py
```

- [ ] **Step 2: Create domain models**

`backend/app/db/models.py`:

```python
# backend/app/db/models.py
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    chat_id: UUID
    role: Literal["user", "assistant", "system"]
    content: dict[str, Any] | list[Any]
    created_at: datetime


class Chat(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: UUID
    user_id: UUID
    title: str | None = None
    model: str = "claude-opus-4-7"
    created_at: datetime
    last_message_at: datetime
```

- [ ] **Step 3: Write the failing test for the messages repo**

`backend/tests/test_messages_repo.py`:

```python
# backend/tests/test_messages_repo.py
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_insert_user_message_returns_row():
    fake_client = MagicMock()
    inserted_row = {
        "id": str(uuid4()),
        "chat_id": str(uuid4()),
        "role": "user",
        "content": {"type": "text", "text": "hi"},
        "created_at": "2026-05-14T18:00:00+00:00",
    }
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

    from app.db.messages import insert_message
    msg = await insert_message(
        fake_client,
        chat_id=inserted_row["chat_id"],
        role="user",
        content={"type": "text", "text": "hi"},
    )
    assert str(msg.id) == inserted_row["id"]
    assert msg.role == "user"
    assert msg.content == {"type": "text", "text": "hi"}


@pytest.mark.asyncio
async def test_get_or_create_chat_creates_when_missing():
    fake_client = MagicMock()
    # First call: select returns empty
    select_q = fake_client.table.return_value.select.return_value
    select_q.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    # Second call: insert returns one row
    chat_id = str(uuid4())
    user_id = str(uuid4())
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": chat_id,
        "user_id": user_id,
        "title": None,
        "model": "claude-opus-4-7",
        "created_at": "2026-05-14T18:00:00+00:00",
        "last_message_at": "2026-05-14T18:00:00+00:00",
    }]

    from app.db.messages import get_or_create_chat
    chat = await get_or_create_chat(fake_client, user_id=user_id, chat_id=None)
    assert str(chat.user_id) == user_id
    assert chat.title is None
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_messages_repo.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.db.messages'`.

- [ ] **Step 5: Create `backend/app/db/messages.py`**

```python
# backend/app/db/messages.py
from typing import Any, Literal
from uuid import UUID

from supabase import Client

from app.db.models import Chat, Message


async def insert_message(
    client: Client,
    *,
    chat_id: str | UUID,
    role: Literal["user", "assistant", "system"],
    content: dict[str, Any] | list[Any],
) -> Message:
    resp = (
        client.table("messages")
        .insert({"chat_id": str(chat_id), "role": role, "content": content})
        .execute()
    )
    row = resp.data[0]
    return Message(**row)


async def get_or_create_chat(
    client: Client,
    *,
    user_id: str | UUID,
    chat_id: str | UUID | None,
) -> Chat:
    if chat_id is not None:
        resp = (
            client.table("chats")
            .select("*")
            .eq("id", str(chat_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        if resp.data:
            return Chat(**resp.data[0])
    resp = client.table("chats").insert({"user_id": str(user_id)}).execute()
    return Chat(**resp.data[0])
```

- [ ] **Step 6: Re-run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_messages_repo.py -v
```

Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/db backend/tests/test_messages_repo.py
git commit -m "feat(backend): add messages + chats repository

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: SSE echo endpoint `/chat/stream`

**Files:**
- Create: `backend/app/core/logging.py`
- Create: `backend/app/routes/chat.py`
- Create: `backend/tests/test_chat_stream.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create structured logging helper**

`backend/app/core/logging.py`:

```python
# backend/app/core/logging.py
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)
```

- [ ] **Step 2: Write the failing test for SSE echo**

`backend/tests/test_chat_stream.py`:

```python
# backend/tests/test_chat_stream.py
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt

from app.db.models import Chat, Message


def _token(sub: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": now + 3600, "iat": now},
        "test-secret-for-unit-tests-only",
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_chat_stream_rejects_unauth(client):
    response = await client.post("/chat/stream", json={"content": "hi"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_stream_echoes_message(client):
    user_id = str(uuid.uuid4())
    chat_id = uuid.uuid4()

    fake_chat = Chat(
        id=chat_id,
        user_id=uuid.UUID(user_id),
        title=None,
        model="claude-opus-4-7",
        created_at="2026-05-14T18:00:00+00:00",
        last_message_at="2026-05-14T18:00:00+00:00",
    )
    fake_user_msg = Message(
        id=uuid.uuid4(),
        chat_id=chat_id,
        role="user",
        content={"type": "text", "text": "hello"},
        created_at="2026-05-14T18:00:00+00:00",
    )
    fake_asst_msg = Message(
        id=uuid.uuid4(),
        chat_id=chat_id,
        role="assistant",
        content=[{"type": "text", "text": "echo: hello"}],
        created_at="2026-05-14T18:00:01+00:00",
    )

    with patch("app.routes.chat.get_or_create_chat", new=AsyncMock(return_value=fake_chat)), \
         patch("app.routes.chat.insert_message", new=AsyncMock(side_effect=[fake_user_msg, fake_asst_msg])), \
         patch("app.routes.chat.get_service_client", return_value=object()):
        response = await client.post(
            "/chat/stream",
            json={"content": "hello"},
            headers={"Authorization": f"Bearer {_token(user_id)}"},
        )

    assert response.status_code == 200
    body = response.text
    # SSE: events are 'event: <type>\ndata: <json>\n\n'
    assert "event: delta" in body
    assert "echo: hello" in body
    assert "event: done" in body
```

- [ ] **Step 3: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_chat_stream.py -v
```

Expected: 404 on POST (route doesn't exist) for both tests.

- [ ] **Step 4: Create the chat router**

`backend/app/routes/chat.py`:

```python
# backend/app/routes/chat.py
import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.core.supabase_client import get_service_client
from app.db.messages import get_or_create_chat, insert_message

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger(__name__)


class ChatStreamRequest(BaseModel):
    content: str
    chat_id: str | None = None


def _sse(event: str, data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/stream")
async def chat_stream(
    req: ChatStreamRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    user_id = user["sub"]

    # Phase 0 uses the service-role client for inserts since we're not yet
    # enforcing per-request user-JWT scoping on Postgrest. Phase 1+ will swap
    # to get_user_client(jwt) so RLS double-checks every write.
    sb = get_service_client()

    async def event_stream():
        chat = await get_or_create_chat(sb, user_id=user_id, chat_id=req.chat_id)

        user_msg = await insert_message(
            sb, chat_id=chat.id, role="user",
            content={"type": "text", "text": req.content},
        )
        yield _sse("progress", {"step": "received", "message_id": str(user_msg.id)})

        echo = f"echo: {req.content}"
        yield _sse("delta", {"type": "text", "text": echo})

        asst_msg = await insert_message(
            sb, chat_id=chat.id, role="assistant",
            content=[{"type": "text", "text": echo}],
        )
        yield _sse("done", {"message_id": str(asst_msg.id), "chat_id": str(chat.id)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 5: Register router in `app/main.py`**

Edit `backend/app/main.py`:

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.logging import configure_logging
from app.routes import chat, health


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.backend_log_level)

    app = FastAPI(title="Stylobate Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    return app


app = create_app()
```

- [ ] **Step 6: Re-run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest tests/test_chat_stream.py -v
```

Expected: `2 passed`.

If the second test still fails with `TypeError` on the type-builder hack: replace the `type(...)` constructs with explicit `Chat(**…)` / `Message(**…)` calls in the test (import `Chat`, `Message` from `app.db.models`).

- [ ] **Step 7: Run the full backend test suite**

```bash
cd /Users/rakhisinha/Stylobate/backend
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add backend/app/core/logging.py backend/app/main.py backend/app/routes/chat.py backend/tests/test_chat_stream.py
git commit -m "feat(backend): add SSE echo /chat/stream with JWT auth + persistence

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Frontend bootstrap (Next.js + Tailwind + shadcn)

**Files:**
- Create: `frontend/package.json` and the rest of the Next.js scaffold (via `create-next-app`)
- Create: `frontend/components.json`
- Modify: `frontend/tailwind.config.ts`

- [ ] **Step 1: Scaffold Next.js**

```bash
cd /Users/rakhisinha/Stylobate
npx --yes create-next-app@latest frontend \
  --typescript --eslint --tailwind --app --no-src-dir \
  --import-alias "@/*" --use-npm --skip-install
cd frontend
npm install
```

If `create-next-app` prompts about Turbopack (`Would you like to use Turbopack?`), answer **No** (default is fine — this plan doesn't depend on Turbopack).

- [ ] **Step 2: Add core deps**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm install @supabase/supabase-js @supabase/ssr
npm install -D @types/node
```

- [ ] **Step 3: Initialize shadcn/ui**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npx --yes shadcn@latest init --base-color slate --css-variables --yes
npx --yes shadcn@latest add button input label form card --yes
```

This creates `components.json` and populates `components/ui/`.

- [ ] **Step 4: Verify frontend builds**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm run build
```

Expected: build completes without errors. (Initial scaffold may show a Next.js placeholder home page — fine.)

- [ ] **Step 5: Add a test script and a single sanity test**

Add to `frontend/package.json` `scripts`:

```json
"test": "vitest run",
"fmt": "prettier --write .",
"format:check": "prettier --check ."
```

Install vitest:

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm install -D vitest @vitest/coverage-v8 jsdom prettier
```

Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["**/__tests__/**/*.test.{ts,tsx}", "**/*.test.{ts,tsx}"],
  },
});
```

Create `frontend/__tests__/smoke.test.ts`:

```ts
import { describe, it, expect } from "vitest";

describe("smoke", () => {
  it("arithmetic still works", () => {
    expect(1 + 1).toBe(2);
  });
});
```

Run:

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm test
```

Expected: 1 test passes.

- [ ] **Step 6: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add frontend/ -- ':!frontend/node_modules' ':!frontend/.next'
git commit -m "feat(frontend): bootstrap Next.js 15 + Tailwind + shadcn/ui

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Frontend Supabase auth (sign in / sign up / middleware)

**Files:**
- Create: `frontend/lib/supabase/client.ts`
- Create: `frontend/lib/supabase/server.ts`
- Create: `frontend/lib/supabase/middleware.ts`
- Create: `frontend/middleware.ts`
- Create: `frontend/app/(auth)/sign-in/page.tsx`
- Create: `frontend/app/(auth)/sign-up/page.tsx`
- Create: `frontend/app/(auth)/callback/route.ts`
- Modify: `frontend/app/page.tsx` (redirect to /chat if signed in, else /sign-in)
- Modify: `frontend/app/layout.tsx` (minimal header)

- [ ] **Step 1: Create the browser-side Supabase client**

`frontend/lib/supabase/client.ts`:

```ts
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
```

- [ ] **Step 2: Create the server-side client (RSC + route handlers)**

`frontend/lib/supabase/server.ts`:

```ts
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createClient() {
  const cookieStore = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // ignored in static render
          }
        },
      },
    },
  );
}
```

- [ ] **Step 3: Create the middleware-side client**

`frontend/lib/supabase/middleware.ts`:

```ts
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );
  const { data: { user } } = await supabase.auth.getUser();
  return { response, user };
}
```

- [ ] **Step 4: Create Next.js middleware to redirect unauthenticated users**

`frontend/middleware.ts`:

```ts
import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/middleware";

export async function middleware(request: NextRequest) {
  const { response, user } = await updateSession(request);
  const pathname = request.nextUrl.pathname;
  const isAuthPath = pathname.startsWith("/sign-in")
    || pathname.startsWith("/sign-up")
    || pathname.startsWith("/auth/");

  if (!user && !isAuthPath && pathname !== "/") {
    return NextResponse.redirect(new URL("/sign-in", request.url));
  }
  if (user && (pathname === "/" || isAuthPath)) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

- [ ] **Step 5: Create the auth callback route**

`frontend/app/(auth)/callback/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  if (code) {
    const supabase = await createClient();
    await supabase.auth.exchangeCodeForSession(code);
  }
  return NextResponse.redirect(`${origin}/chat`);
}
```

- [ ] **Step 6: Create the sign-in page**

`frontend/app/(auth)/sign-in/page.tsx`:

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";

export default function SignInPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      setError(error.message);
      return;
    }
    router.push("/chat");
    router.refresh();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-8">
      <h1 className="text-2xl font-semibold tracking-tight">Stylobate</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit">Sign in</Button>
        <p className="text-center text-sm text-muted-foreground">
          No account? <Link href="/sign-up" className="underline">Sign up</Link>
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 7: Create the sign-up page**

`frontend/app/(auth)/sign-up/page.tsx`:

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";

export default function SignUpPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signUp({
      email, password,
      options: { emailRedirectTo: `${location.origin}/auth/callback` },
    });
    if (error) {
      setError(error.message);
      return;
    }
    router.push("/chat");
    router.refresh();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-8">
      <h1 className="text-2xl font-semibold tracking-tight">Create account</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit">Sign up</Button>
        <p className="text-center text-sm text-muted-foreground">
          Already have one? <Link href="/sign-in" className="underline">Sign in</Link>
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 8: Replace `frontend/app/page.tsx` so it routes by auth state**

`frontend/app/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export default async function HomePage() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  redirect(user ? "/chat" : "/sign-in");
}
```

- [ ] **Step 9: Wrap layout with a minimal header**

`frontend/app/layout.tsx`:

```tsx
import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Stylobate",
  description: "Multi-agent investing analysis for retail investors.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 10: Smoke-test the auth flow**

This step requires the Supabase project from Task 5 step 3 to be live and `frontend/.env.local` to be populated.

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm run dev
```

Open http://localhost:3000. Expected: redirect to `/sign-in`. Click "Sign up", enter a test email + password, submit. Expected: redirected to `/chat` (page may 404 — that's fine, we'll add `/chat` in Task 11).

Then check the Supabase dashboard: Authentication → Users — your account is listed. Database → user_profiles — a row exists for your user id.

- [ ] **Step 11: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add frontend/lib frontend/middleware.ts frontend/app/page.tsx frontend/app/layout.tsx frontend/app/\(auth\)
git commit -m "feat(frontend): add Supabase auth (sign-in, sign-up, middleware)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Frontend chat shell with SSE

**Files:**
- Create: `frontend/lib/types.ts`
- Create: `frontend/lib/sse.ts`
- Create: `frontend/components/composer.tsx`
- Create: `frontend/components/chat-thread.tsx`
- Create: `frontend/app/chat/page.tsx`
- Create: `frontend/__tests__/sse.test.ts`

- [ ] **Step 1: Create shared types**

`frontend/lib/types.ts`:

```ts
export type DeltaText = { type: "text"; text: string };
export type DeltaBlock = DeltaText;

export type SSEEvent =
  | { event: "progress"; data: { step: string; message_id?: string } }
  | { event: "delta"; data: DeltaBlock }
  | { event: "done"; data: { message_id: string; chat_id: string } }
  | { event: "error"; data: { message: string } };

export type ChatMessageBlock = DeltaBlock;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: ChatMessageBlock[] | { type: "text"; text: string };
}
```

- [ ] **Step 2: Write the failing test for the SSE parser**

`frontend/__tests__/sse.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { parseSSEStream } from "@/lib/sse";

describe("parseSSEStream", () => {
  it("parses a simple delta + done stream", async () => {
    const chunks = [
      'event: progress\ndata: {"step":"received"}\n\n',
      'event: delta\ndata: {"type":"text","text":"echo: hi"}\n\n',
      'event: done\ndata: {"message_id":"m1","chat_id":"c1"}\n\n',
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        for (const c of chunks) controller.enqueue(enc.encode(c));
        controller.close();
      },
    });
    const events: any[] = [];
    for await (const ev of parseSSEStream(stream)) events.push(ev);
    expect(events).toHaveLength(3);
    expect(events[0]).toEqual({ event: "progress", data: { step: "received" } });
    expect(events[1]).toEqual({ event: "delta", data: { type: "text", text: "echo: hi" } });
    expect(events[2]).toEqual({ event: "done", data: { message_id: "m1", chat_id: "c1" } });
  });

  it("handles chunked frames", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        controller.enqueue(enc.encode("event: delt"));
        controller.enqueue(enc.encode('a\ndata: {"type":"text","text":"hi"}\n'));
        controller.enqueue(enc.encode("\n"));
        controller.close();
      },
    });
    const events: any[] = [];
    for await (const ev of parseSSEStream(stream)) events.push(ev);
    expect(events).toEqual([{ event: "delta", data: { type: "text", text: "hi" } }]);
  });
});
```

- [ ] **Step 3: Run the test, confirm it fails**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm test -- sse.test.ts
```

Expected: failure (module not found).

- [ ] **Step 4: Implement the SSE parser**

`frontend/lib/sse.ts`:

```ts
import type { SSEEvent } from "./types";

export async function* parseSSEStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (parsed) yield parsed;
    }
  }
}

function parseFrame(frame: string): SSEEvent | null {
  const lines = frame.split("\n");
  let event = "";
  let data = "";
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!event) return null;
  try {
    return { event, data: JSON.parse(data) } as SSEEvent;
  } catch {
    return null;
  }
}

export async function postChatStream({
  jwt,
  content,
  chatId,
  backendUrl,
}: {
  jwt: string;
  content: string;
  chatId: string | null;
  backendUrl: string;
}): Promise<ReadableStream<Uint8Array>> {
  const resp = await fetch(`${backendUrl}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${jwt}`,
    },
    body: JSON.stringify({ content, chat_id: chatId }),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE request failed: ${resp.status}`);
  }
  return resp.body;
}
```

- [ ] **Step 5: Re-run the test, confirm it passes**

```bash
cd /Users/rakhisinha/Stylobate/frontend
npm test -- sse.test.ts
```

Expected: 2 passed.

- [ ] **Step 6: Build the chat UI components**

`frontend/components/composer.tsx`:

```tsx
"use client";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";

export function Composer({ onSend, disabled }: { onSend: (text: string) => void; disabled?: boolean }) {
  const [value, setValue] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!value.trim() || disabled) return;
    onSend(value.trim());
    setValue("");
  }

  return (
    <form onSubmit={submit} className="flex gap-2 border-t bg-card p-4">
      <input
        type="text"
        className="flex-1 rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
        placeholder="Ask anything — for now I just echo back."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={disabled}
      />
      <Button type="submit" disabled={disabled}>Send</Button>
    </form>
  );
}
```

`frontend/components/chat-thread.tsx`:

```tsx
"use client";
import type { ChatMessage } from "@/lib/types";

export function ChatThread({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex flex-col gap-4 p-6">
      {messages.map((m) => (
        <div key={m.id} className={m.role === "user" ? "self-end" : "self-start"}>
          <div className={
            "max-w-2xl rounded-lg px-4 py-2 text-sm " +
            (m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted")
          }>
            {Array.isArray(m.content)
              ? m.content.map((b, i) => (b.type === "text" ? <p key={i}>{b.text}</p> : null))
              : m.content.type === "text" ? <p>{m.content.text}</p> : null}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Create the chat page**

`frontend/app/chat/page.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";

import { ChatThread } from "@/components/chat-thread";
import { Composer } from "@/components/composer";
import { parseSSEStream, postChatStream } from "@/lib/sse";
import { createClient } from "@/lib/supabase/client";
import type { ChatMessage } from "@/lib/types";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [chatId, setChatId] = useState<string | null>(null);
  const [jwt, setJwt] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      setJwt(session?.access_token ?? null);
    });
  }, []);

  async function handleSend(text: string) {
    if (!jwt) return;
    setSending(true);
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: { type: "text", text },
    };
    setMessages((m) => [...m, userMsg]);

    try {
      const stream = await postChatStream({
        jwt, content: text, chatId, backendUrl: BACKEND_URL,
      });
      const asstId = crypto.randomUUID();
      const asstMsg: ChatMessage = { id: asstId, role: "assistant", content: [] };
      setMessages((m) => [...m, asstMsg]);
      let buf: ChatMessage["content"] = [];

      for await (const ev of parseSSEStream(stream)) {
        if (ev.event === "delta" && ev.data.type === "text") {
          buf = Array.isArray(buf) ? [...buf, ev.data] : [ev.data];
          setMessages((m) =>
            m.map((x) => (x.id === asstId ? { ...x, content: buf } : x)),
          );
        } else if (ev.event === "done") {
          setChatId(ev.data.chat_id);
        }
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h1 className="text-sm font-semibold">Stylobate</h1>
      </header>
      <div className="flex-1 overflow-y-auto">
        <ChatThread messages={messages} />
      </div>
      <Composer onSend={handleSend} disabled={sending || !jwt} />
    </main>
  );
}
```

- [ ] **Step 8: End-to-end smoke test**

In two terminals (assuming Task 5 step 3 done and Supabase configured):

```bash
# Terminal 1
cd /Users/rakhisinha/Stylobate
make backend

# Terminal 2
cd /Users/rakhisinha/Stylobate
make frontend
```

Open http://localhost:3000. Sign in. Type "hello world". Expected: a user bubble appears with "hello world"; an assistant bubble appears with "echo: hello world"; in the Supabase dashboard `messages` table, two new rows.

- [ ] **Step 9: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add frontend/lib/types.ts frontend/lib/sse.ts frontend/components frontend/app/chat frontend/__tests__/sse.test.ts
git commit -m "feat(frontend): add chat shell with SSE echo end-to-end

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: CI workflows (backend + frontend)

**Files:**
- Create: `.github/workflows/backend-ci.yml`
- Create: `.github/workflows/frontend-ci.yml`

- [ ] **Step 1: Create backend CI**

`.github/workflows/backend-ci.yml`:

```yaml
name: backend-ci
on:
  push:
    paths:
      - "backend/**"
      - ".github/workflows/backend-ci.yml"
  pull_request:
    paths:
      - "backend/**"
      - ".github/workflows/backend-ci.yml"

jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run mypy app
      - run: uv run pytest -q
        env:
          SUPABASE_URL: https://test.supabase.co
          SUPABASE_ANON_KEY: anon-key-test
          SUPABASE_SERVICE_ROLE_KEY: service-role-test
          SUPABASE_JWT_SECRET: test-secret-for-unit-tests-only
          ANTHROPIC_API_KEY: sk-ant-test
```

- [ ] **Step 2: Create frontend CI**

`.github/workflows/frontend-ci.yml`:

```yaml
name: frontend-ci
on:
  push:
    paths:
      - "frontend/**"
      - ".github/workflows/frontend-ci.yml"
  pull_request:
    paths:
      - "frontend/**"
      - ".github/workflows/frontend-ci.yml"

jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npm test
      - run: npm run build
        env:
          NEXT_PUBLIC_SUPABASE_URL: https://test.supabase.co
          NEXT_PUBLIC_SUPABASE_ANON_KEY: anon-test
          NEXT_PUBLIC_BACKEND_URL: http://localhost:8000
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rakhisinha/Stylobate
git add .github/
git commit -m "ci: add backend + frontend GitHub Actions workflows

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Push to GitHub

This task requires user confirmation because it makes the repo public on the user's account. Pause for explicit go-ahead.

**Files:** none (operates on `.git/config` and remote)

- [ ] **Step 1: Confirm intent with user**

Ask: "Ready to push Stylobate to `github.com/RakhiIsTrying/stylobate` as a public repo? It includes the spec, plan, and code so far. Reply yes/no."

- [ ] **Step 2: If yes — sanity-check that no remote is already configured**

```bash
cd /Users/rakhisinha/Stylobate
git remote -v
```

Expected: empty (no remotes). If `origin` already exists, stop and tell the user — don't auto-overwrite.

- [ ] **Step 3: Create the remote repo and push**

```bash
cd /Users/rakhisinha/Stylobate
gh repo create RakhiIsTrying/stylobate --public --source=. --remote=origin --description "Multi-agent investment-banker AI for retail investors (US + India). WIP." --push
```

Expected output (last lines):
```
✓ Created repository RakhiIsTrying/stylobate on GitHub
✓ Added remote origin
✓ Pushed commits to https://github.com/RakhiIsTrying/stylobate.git
```

Then verify:

```bash
cd /Users/rakhisinha/Stylobate
git remote -v
gh repo view RakhiIsTrying/stylobate --web
```

- [ ] **Step 3: If no — skip and leave a note**

Leave a note in `README.md` that `origin` is not yet configured; the user can push manually later.

---

## End-of-phase verification

After all 13 tasks land, run this checklist:

- [ ] `make check` from the repo root passes (backend lint + mypy + pytest; frontend lint + vitest)
- [ ] `make backend` + `make frontend` start without errors
- [ ] Visiting `http://localhost:3000` redirects to `/sign-in`
- [ ] Signing up creates a row in `auth.users` AND `public.user_profiles` (via the trigger)
- [ ] Posting "hello world" in the chat returns "echo: hello world" streamed via SSE, and persists two rows to `public.messages`
- [ ] GitHub Actions run green on the latest commit
- [ ] The `agent_memory`, `tool_calls`, `model_runs`, `cache_kv` tables exist and are empty (waiting for Phase 1)
- [ ] All migrations are reflected in Supabase (`select table_name from information_schema.tables where table_schema='public'` returns 7 tables)

If everything green: Phase 0 is done. Next plan: Phase 1 — single specialist end-to-end (Lead Banker + Fundamental Analyst + Ticker Resolver + yfinance + EDGAR adapters + prompt caching + output validator + structured stock_card delta).
