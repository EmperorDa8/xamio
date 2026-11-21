-- Audience breakdown for /admin: country, OS, browser and device type.
--
-- The frontend stamps every activity event with metadata.client =
-- {country, os, browser, device} (see frontend/src/lib/analytics.js; country
-- comes from Vercel's IP geolocation via /api/geo, the IP is never stored).
-- Each real user is counted once, by the most recent event that carries a
-- client object in the window. Events from before this shipped have no client
-- object, so `covered` says how many users the breakdown is based on.
--
-- Same access rule as admin_dashboard(): checked in the database against
-- xamio.admins, P0002 for anyone else.

create or replace function public.admin_audience(days integer default 60)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  span integer := least(greatest(coalesce(days, 60), 7), 365);
  result jsonb;
begin
  if not exists (select 1 from xamio.admins a where a.user_id = auth.uid()) then
    raise exception 'not found' using errcode = 'P0002';
  end if;

  with
  latest as (
    select distinct on (e.user_id)
      e.user_id,
      nullif(e.metadata->'client'->>'country', '') as country,
      coalesce(nullif(e.metadata->'client'->>'os', ''), 'Other') as os,
      coalesce(nullif(e.metadata->'client'->>'browser', ''), 'Other') as browser,
      coalesce(nullif(e.metadata->'client'->>'device', ''), 'Other') as device,
      e.created_at
    from public.activity_events e
    where e.metadata ? 'client'
      and e.created_at > now() - make_interval(days => span)
    order by e.user_id, e.created_at desc, e.id desc
  ),
  real_latest as (
    select l.* from latest l
    where l.user_id not in (select user_id from xamio.admins)
  )
  select jsonb_build_object(
    'days', span,
    'covered', (select count(*) from real_latest),
    'real_users', (select count(*) from auth.users u where u.id not in (select user_id from xamio.admins)),
    'countries', (select coalesce(jsonb_object_agg(k, n), '{}'::jsonb)
                  from (select coalesce(country, 'Unknown') k, count(*) n from real_latest group by 1) s),
    'os', (select coalesce(jsonb_object_agg(os, n), '{}'::jsonb)
           from (select os, count(*) n from real_latest group by 1) s),
    'browsers', (select coalesce(jsonb_object_agg(browser, n), '{}'::jsonb)
                 from (select browser, count(*) n from real_latest group by 1) s),
    'devices', (select coalesce(jsonb_object_agg(device, n), '{}'::jsonb)
                from (select device, count(*) n from real_latest group by 1) s),
    -- per user (admins included, so your own row in the users table fills in)
    'by_user', (select coalesce(jsonb_object_agg(user_id, jsonb_build_object(
                  'country', country, 'os', os, 'browser', browser, 'device', device)), '{}'::jsonb)
                from latest)
  ) into result;

  return result;
end;
$$;

revoke all on function public.admin_audience(integer) from public, anon;
grant execute on function public.admin_audience(integer) to authenticated;
