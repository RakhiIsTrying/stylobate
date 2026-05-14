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
