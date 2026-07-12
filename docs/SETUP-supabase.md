# Supabase setup (Spec A)

Without these steps the app runs anonymous-only (sign-in hidden). ~15 minutes.

1. Create a project at supabase.com. Copy from Settings -> API:
   - Project URL -> `SUPABASE_URL`
   - anon public key -> `SUPABASE_ANON_KEY`
   - service_role key -> `SUPABASE_SERVICE_KEY` (server only, never in frontend)
   - Settings -> API -> JWT Settings -> JWT Secret -> `SUPABASE_JWT_SECRET`
2. Auth -> Providers: enable Email (magic link on). Enable Google (paste OAuth
   client id/secret from Google Cloud Console; authorized redirect =
   the Supabase callback URL shown on that page).
3. Auth -> URL Configuration: set Site URL to your app URL
   (http://localhost:8080 for dev) so magic links redirect back.
4. SQL editor, run:

   ```sql
   create table app_config (key text primary key, value text);
   insert into app_config (key, value) values
     ('max_session_minutes.anonymous', '5'),
     ('max_session_minutes.free', '15'),
     ('max_session_minutes.pro', '30'),
     ('pro_override_max_minutes', '60'),
     ('anon_sessions_per_day', '2'),
     ('sessions_per_day.free', 'null'),
     ('sessions_per_day.pro', 'null'),
     ('sessions_per_month.free', '8'),
     ('sessions_per_month.pro', 'null'),
     ('tts_calls_per_session', '30');

   create table session_starts (
     id uuid primary key default gen_random_uuid(),
     user_id uuid not null,
     tier text not null,
     started_at timestamptz not null default now()
   );
   create index on session_starts (user_id, started_at);

   alter table app_config enable row level security;
   alter table session_starts enable row level security;
   -- no policies: service_role bypasses RLS; anon/authenticated get nothing.
   ```

5. Ops (Supabase Studio is the v1 admin console):
   - Edit `app_config` rows in the Table Editor to change limits live
     (the server caches for 60s). Set a value to the text `null` for
     unlimited, `0` to shut a tier off.
   - Manage users under Authentication -> Users.
   - To make a user Pro (until Stripe lands): Authentication -> Users ->
     select user -> edit App Metadata -> `{"tier": "pro"}`. The user must
     sign out and back in to pick up the new tier in their token.

Environment variable fallbacks (used when Supabase is unreachable or
unconfigured): `LIMIT_<KEY>` with dots replaced by underscores, uppercased.
Example: `LIMIT_MAX_SESSION_MINUTES_FREE=20`.
