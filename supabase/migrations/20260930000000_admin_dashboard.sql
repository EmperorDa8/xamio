-- Founder dashboard: one RPC that returns every number, callable only by admins.
--
-- Access is enforced HERE, in the database, not in the frontend: the function
-- checks the caller's auth.uid() against xamio.admins and raises otherwise.
-- xamio is the private schema (not exposed through PostgREST), so nobody can
-- read or insert into the admins list through the API — rows are added only
-- with SQL by the project owner.
--
-- Emails are masked (a•••@domain); the dashboard is for behaviour, not identity.

create table if not exists xamio.admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  added_at timestamptz not null default now()
);
revoke all on xamio.admins from anon, authenticated;

-- Seed the founder account.
insert into xamio.admins (user_id)
select id from auth.users where lower(email) = 'uabdul88@gmail.com'
on conflict do nothing;


create or replace function public.am_i_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from xamio.admins a where a.user_id = auth.uid());
$$;


create or replace function public.admin_dashboard(days integer default 60)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  result jsonb;
  span integer := least(greatest(coalesce(days, 60), 7), 365);
begin
  if not exists (select 1 from xamio.admins a where a.user_id = auth.uid()) then
    -- Same message as a missing function, so the endpoint reveals nothing.
    raise exception 'not found' using errcode = 'P0002';
  end if;

  with
  admins as (select user_id from xamio.admins),
  u as (
    select id, email, created_at, last_sign_in_at,
           coalesce(raw_app_meta_data->>'provider', 'email') as provider,
           id in (select user_id from admins) as is_admin
    from auth.users
  ),
  real_users as (select * from u where not is_admin),
  ev as (
    select e.user_id, e.event_type, e.created_at
    from public.activity_events e
    where e.user_id not in (select user_id from admins)
  ),
  per_user as (
    select u.id,
      count(*) filter (where e.event_type = 'app_open') as opens,
      count(distinct e.created_at::date) as active_days,
      count(*) filter (where e.event_type = 'upload') as uploads,
      count(*) filter (where e.event_type = 'sync') as syncs,
      max(e.created_at) as last_seen
    from u left join public.activity_events e on e.user_id = u.id
    group by u.id
  ),
  rem_users as (
    select distinct q.user_id from xamio.reminder_queue q where q.user_id is not null
  ),
  days_axis as (
    select d::date as day
    from generate_series(current_date - (span - 1), current_date, interval '1 day') d
  )
  select jsonb_build_object(
    'generated_at', now(),
    'days', span,

    'totals', jsonb_build_object(
      'accounts', (select count(*) from u),
      'real_users', (select count(*) from real_users),
      'admins', (select count(*) from admins),
      'signups_7d', (select count(*) from real_users where created_at > now() - interval '7 days'),
      'signups_30d', (select count(*) from real_users where created_at > now() - interval '30 days'),
      'dau', (select count(distinct user_id) from ev where created_at > now() - interval '1 day'),
      'wau', (select count(distinct user_id) from ev where created_at > now() - interval '7 days'),
      'mau', (select count(distinct user_id) from ev where created_at > now() - interval '30 days'),
      'schedules', (select count(*) from public.schedules s where s.user_id not in (select user_id from admins)),
      'items', (select count(*) from public.schedule_exams x where x.user_id not in (select user_id from admins))
    ),

    'funnel', jsonb_build_array(
      jsonb_build_object('step', 'Signed up', 'n', (select count(*) from real_users)),
      jsonb_build_object('step', 'Opened the app', 'n', (select count(distinct user_id) from ev where event_type = 'app_open')),
      jsonb_build_object('step', 'Uploaded a timetable', 'n', (select count(distinct user_id) from ev where event_type = 'upload')),
      jsonb_build_object('step', 'Added to a calendar', 'n', (select count(distinct user_id) from ev where event_type = 'sync')),
      jsonb_build_object('step', 'Set email reminders', 'n',
        (select count(*) from real_users r where r.id::text in (select user_id::text from rem_users)))
    ),

    'daily', (
      select jsonb_agg(jsonb_build_object(
        'day', d.day,
        'signups', (select count(*) from real_users r where r.created_at::date = d.day),
        'active', (select count(distinct user_id) from ev where created_at::date = d.day),
        'uploads', (select count(*) from ev where event_type = 'upload' and created_at::date = d.day),
        'syncs', (select count(*) from ev where event_type = 'sync' and created_at::date = d.day)
      ) order by d.day)
      from days_axis d
    ),

    'retention', (
      select coalesce(jsonb_agg(c order by c->>'cohort'), '[]'::jsonb) from (
        select jsonb_build_object(
          'cohort', g.cohort::date,
          'size', g.size,
          -- users from this cohort active in week w after their signup week
          'weeks', (
            select jsonb_agg(
              (select count(distinct e.user_id) from ev e
                 join real_users r2 on r2.id = e.user_id
                where date_trunc('week', r2.created_at) = g.cohort
                  and floor(extract(epoch from (e.created_at - g.cohort)) / 604800) = w)
              order by w)
            from generate_series(0, 8) w
          )
        ) as c
        from (
          select date_trunc('week', created_at) as cohort, count(*) as size
          from real_users group by 1
        ) g
      ) cohorts
    ),

    'users', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'label', left(u.email, 1) || '•••@' || split_part(u.email, '@', 2),
        'is_admin', u.is_admin,
        'provider', u.provider,
        'signed_up', u.created_at,
        'last_seen', greatest(p.last_seen, u.last_sign_in_at),
        'active_days', p.active_days,
        'opens', p.opens,
        'uploads', p.uploads,
        'syncs', p.syncs,
        'schedules', (select count(*) from public.schedules s where s.user_id = u.id),
        'items', (select count(*) from public.schedule_exams x where x.user_id = u.id),
        'reminders_pending', (select count(*) from xamio.reminder_queue q where q.user_id::text = u.id::text and q.status = 'pending')
      ) order by u.created_at desc), '[]'::jsonb)
      from u join per_user p on p.id = u.id
    ),

    'reminders', jsonb_build_object(
      'by_status', (select coalesce(jsonb_object_agg(status, n), '{}'::jsonb)
                    from (select status, count(*) n from xamio.reminder_queue group by status) s),
      'recipients', (select count(distinct destination) from xamio.reminder_queue),
      'next_due', (select min(send_at) from xamio.reminder_queue where status = 'pending'),
      'overdue', (select count(*) from xamio.reminder_queue where status = 'pending' and send_at < now() - interval '10 minutes'),
      'recent_errors', (select coalesce(jsonb_agg(jsonb_build_object('at', coalesce(sent_at, claimed_at, send_at), 'status', status, 'error', left(last_error, 160))), '[]'::jsonb)
                        from (select * from xamio.reminder_queue where last_error is not null order by send_at desc limit 8) q),
      'dispatch_last', (select jsonb_build_object('at', created, 'status', status_code, 'body', left(content, 300))
                        from net._http_response order by created desc limit 1)
    ),

    'content', jsonb_build_object(
      'items_by_kind', (select coalesce(jsonb_object_agg(kind, n), '{}'::jsonb)
                        from (select kind, count(*) n from public.schedule_exams group by kind) k),
      'parsers', (select coalesce(jsonb_object_agg(coalesce(model_used, 'unknown'), n), '{}'::jsonb)
                  from (select model_used, count(*) n from public.schedules group by model_used) m)
    ),

    'recent', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'at', e.created_at, 'event', e.event_type,
        'who', coalesce(left(au.email, 1) || '•••@' || split_part(au.email, '@', 2), 'unknown'),
        'is_admin', e.user_id in (select user_id from admins)
      ) order by e.created_at desc), '[]'::jsonb)
      from (select * from public.activity_events order by created_at desc limit 30) e
      left join auth.users au on au.id = e.user_id
    )
  ) into result;

  return result;
end;
$$;

revoke all on function public.admin_dashboard(integer) from public, anon;
revoke all on function public.am_i_admin() from public, anon;
grant execute on function public.admin_dashboard(integer) to authenticated;
grant execute on function public.am_i_admin() to authenticated;
