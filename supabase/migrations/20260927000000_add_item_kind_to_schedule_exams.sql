-- Store what kind of item each saved row is.
--
-- Without these columns a restored schedule lost every row's kind: an essay
-- came back as an "exam", so it got exam reminders, an "EXAM:" calendar title,
-- and a different stable_key, which made the next Google sync duplicate it.
-- It also let an exam and an essay for the same course on the same day collide
-- on unique(schedule_id, stable_key) and the second be silently dropped.
--
-- Additive and nullable-with-default: safe to apply while the old frontend is
-- live, and the new frontend falls back to the old columns until it is applied.

alter table public.schedule_exams
  add column if not exists kind text not null default 'exam',
  add column if not exists title text,
  add column if not exists weight_pct numeric,
  add column if not exists est_effort_hours numeric,
  add column if not exists submission_url text;

alter table public.schedule_exams
  drop constraint if exists schedule_exams_kind_check;
alter table public.schedule_exams
  add constraint schedule_exams_kind_check
  check (kind in ('exam', 'test', 'coursework', 'problem_set', 'milestone', 'meeting'));
