-- Session persistence: one row per completed debrief for signed-in users.
-- Paste into Supabase Studio > SQL Editor and run. The backend writes here with
-- the service key (see persistence.py), gated on a signed-in user_id. Without
-- this table the debrief still works; the write is a fail-open no-op.
--
-- The history UI and progress chart (a later slice) read from this table, so
-- an owner-scoped SELECT policy is included for the authenticated user.

create table if not exists public.sessions (
    id                bigint generated always as identity primary key,
    user_id           uuid not null references auth.users (id) on delete cascade,
    topic             text,
    mode              text not null default 'individual',
    filler_count      integer not null default 0,
    pause_count       integer not null default 0,
    wpm               integer not null default 0,
    total_words       integer not null default 0,
    duration_seconds  integer not null default 0,
    delivery_score    integer,
    content_score     integer,
    verdict           text,
    created_at        timestamptz not null default now()
);

create index if not exists sessions_user_created_idx
    on public.sessions (user_id, created_at desc);

-- RLS: the service key (backend writes) bypasses RLS. Add a read policy so a
-- signed-in user can fetch only their own sessions from the browser (anon key
-- + user JWT) for the history UI.
alter table public.sessions enable row level security;

drop policy if exists sessions_owner_select on public.sessions;
create policy sessions_owner_select on public.sessions
    for select
    using (auth.uid() = user_id);
