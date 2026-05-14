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
