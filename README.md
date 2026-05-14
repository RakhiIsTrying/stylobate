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
