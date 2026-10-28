-- Add report-scoped retrieval and private, per-IP hourly quotas for AI answers.
create or replace function public.search_report_chunks_scoped(
  query_text text,
  target_report_id text default null,
  result_limit integer default 10
)
returns table(report_id text, page integer, section text, content text, rank real)
language sql
stable
security invoker
set search_path = ''
as $$
  select c.report_id, c.page, c.section, c.content,
         ts_rank(c.search_vector, websearch_to_tsquery('simple', query_text)) as rank
  from public.report_chunks c
  where nullif(trim(query_text), '') is not null
    and c.search_vector @@ websearch_to_tsquery('simple', query_text)
    and (target_report_id is null or c.report_id = target_report_id)
  order by rank desc, c.report_id, c.page
  limit least(greatest(result_limit, 1), 12);
$$;
grant execute on function public.search_report_chunks_scoped(text, text, integer) to anon, authenticated;

create table if not exists public.report_ai_request_limits (
  ip_hash text primary key,
  window_started_at timestamptz not null,
  request_count integer not null default 0
);
alter table public.report_ai_request_limits enable row level security;
revoke all on public.report_ai_request_limits from public, anon, authenticated;

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
  on conflict (ip_hash) do update
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
