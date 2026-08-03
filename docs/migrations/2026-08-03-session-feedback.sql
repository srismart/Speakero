-- Debrief feedback: one row per thumbs up/down on a session debrief.
-- Paste into Supabase Studio > SQL Editor and run. The app writes here with
-- the service key (see feedback.py). Without this table the /api/feedback
-- endpoint still returns ok and logs the event; it just does not persist.

create table if not exists public.session_feedback (
    id          bigint generated always as identity primary key,
    user_id     uuid references auth.users (id) on delete set null,
    sid         text,
    rating      text not null check (rating in ('up', 'down')),
    comment     text,
    topic       text,
    created_at  timestamptz not null default now()
);

create index if not exists session_feedback_created_at_idx
    on public.session_feedback (created_at desc);

-- Row Level Security: the service key bypasses RLS, so writes from the backend
-- work with RLS on. Enable it and add no anon/authenticated policies, so the
-- table is not readable from the browser via the anon key.
alter table public.session_feedback enable row level security;
