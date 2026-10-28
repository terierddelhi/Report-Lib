-- TERI Reports Library: run once in Supabase SQL Editor.
create extension if not exists pgcrypto;

create table if not exists public.library_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  added_at timestamptz not null default now()
);

create or replace function public.is_library_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.library_admins where user_id = (select auth.uid())
  );
$$;
revoke all on function public.is_library_admin() from public;
grant execute on function public.is_library_admin() to anon, authenticated;

create table if not exists public.reports (
  id text primary key,
  filename text not null,
  title text not null,
  authors text not null default '',
  published text not null default '',
  summary text not null default '',
  pages integer not null default 0,
  warnings jsonb not null default '[]'::jsonb,
  source_url text not null default '',
  object_path text not null unique,
  indexed_at timestamptz not null default now()
);

create table if not exists public.report_sections (
  report_id text not null references public.reports(id) on delete cascade,
  name text not null,
  first_page integer not null,
  primary key (report_id, name, first_page)
);

create table if not exists public.report_chunks (
  id bigint generated always as identity primary key,
  report_id text not null references public.reports(id) on delete cascade,
  page integer not null,
  section text not null default 'Report text',
  content text not null,
  search_vector tsvector generated always as (to_tsvector('simple', content)) stored
);
create index if not exists report_chunks_search_idx on public.report_chunks using gin (search_vector);
create index if not exists report_chunks_report_page_idx on public.report_chunks (report_id, page);

alter table public.library_admins enable row level security;
alter table public.reports enable row level security;
alter table public.report_sections enable row level security;
alter table public.report_chunks enable row level security;

drop policy if exists "Public can read reports" on public.reports;
create policy "Public can read reports" on public.reports for select using (true);
drop policy if exists "Admins manage reports" on public.reports;
create policy "Admins manage reports" on public.reports for all to authenticated
  using (public.is_library_admin()) with check (public.is_library_admin());

drop policy if exists "Public can read report sections" on public.report_sections;
create policy "Public can read report sections" on public.report_sections for select using (true);
drop policy if exists "Admins manage report sections" on public.report_sections;
create policy "Admins manage report sections" on public.report_sections for all to authenticated
  using (public.is_library_admin()) with check (public.is_library_admin());

drop policy if exists "Public can read report chunks" on public.report_chunks;
create policy "Public can read report chunks" on public.report_chunks for select using (true);
drop policy if exists "Admins manage report chunks" on public.report_chunks;
create policy "Admins manage report chunks" on public.report_chunks for all to authenticated
  using (public.is_library_admin()) with check (public.is_library_admin());

drop policy if exists "Admins can read own admin row" on public.library_admins;
create policy "Admins can read own admin row" on public.library_admins for select to authenticated
  using (user_id = (select auth.uid()));

grant select on public.reports, public.report_sections, public.report_chunks to anon, authenticated;
grant insert, update, delete on public.reports, public.report_sections, public.report_chunks to authenticated;
grant usage, select on sequence public.report_chunks_id_seq to authenticated;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('reports', 'reports', true, 50000000, array['application/pdf'])
on conflict (id) do update set public = true, file_size_limit = 50000000,
  allowed_mime_types = array['application/pdf'];

drop policy if exists "Admins upload report PDFs" on storage.objects;
create policy "Admins upload report PDFs" on storage.objects for insert to authenticated
  with check (bucket_id = 'reports' and public.is_library_admin());
drop policy if exists "Admins update report PDFs" on storage.objects;
create policy "Admins update report PDFs" on storage.objects for update to authenticated
  using (bucket_id = 'reports' and public.is_library_admin())
  with check (bucket_id = 'reports' and public.is_library_admin());
drop policy if exists "Admins delete report PDFs" on storage.objects;
create policy "Admins delete report PDFs" on storage.objects for delete to authenticated
  using (bucket_id = 'reports' and public.is_library_admin());

create or replace function public.search_report_chunks(query_text text, result_limit integer default 24)
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
  order by rank desc, c.report_id, c.page
  limit least(greatest(result_limit, 1), 60);
$$;
grant execute on function public.search_report_chunks(text, integer) to anon, authenticated;

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
