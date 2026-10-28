-- Avoid a PL/pgSQL name collision between the ip_hash parameter and the
-- primary-key column in the ON CONFLICT target.
create or replace function public.consume_report_ai_request(ip_hash text, max_requests integer default 12)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  updated_count integer;
begin
  insert into public.report_ai_request_limits as current_limit (ip_hash, window_started_at, request_count)
  values ($1, now(), 1)
  on conflict on constraint report_ai_request_limits_pkey do update
    set window_started_at = case
          when current_limit.window_started_at < now() - interval '1 hour' then now()
          else current_limit.window_started_at
        end,
        request_count = case
          when current_limit.window_started_at < now() - interval '1 hour' then 1
          else current_limit.request_count + 1
        end
    where current_limit.window_started_at < now() - interval '1 hour'
       or current_limit.request_count < greatest($2, 1)
  returning request_count into updated_count;
  return found;
end;
$$;
revoke all on function public.consume_report_ai_request(text, integer) from public, anon, authenticated;
grant execute on function public.consume_report_ai_request(text, integer) to service_role;
