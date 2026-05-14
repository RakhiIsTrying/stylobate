-- 20260514000003_revoke_handle_new_user.sql
-- The handle_new_user() SECURITY DEFINER function should only be invoked
-- by the on_auth_user_created trigger, never via REST/RPC. Revoke EXECUTE
-- from the user-facing roles so callers can't escalate via /rpc.
revoke execute on function public.handle_new_user() from anon, authenticated, public;
