-- Autopost database schema.
--
-- One source of truth for both sides: the Python publisher (GitHub Actions)
-- and the Node API behind the control panel (Vercel) each apply this file on
-- start-up. Every statement is idempotent, so running it on every boot is
-- safe and is what makes a brand-new database "just work".
--
-- Statements are separated by a line containing only `-- ;;`, because the
-- Neon HTTP driver runs one statement per call. Keep it that way: no
-- semicolon-splitting surprises inside function bodies.

create table if not exists autopost_settings (
  id          smallint primary key default 1 check (id = 1),
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
)
-- ;;
-- One row per book. `override` is what you typed in the panel (title, price,
-- description, tags). `record` is what the publisher did with it (status,
-- per-platform ids and URLs, generated title, social captions). Keeping them
-- in separate columns means a publish never clobbers your edits.
create table if not exists autopost_books (
  slug          text primary key,
  override      jsonb not null default '{}'::jsonb,
  record        jsonb not null default '{}'::jsonb,
  status        text,
  published_at  timestamptz,
  updated_at    timestamptz not null default now()
)
-- ;;
create index if not exists autopost_books_status on autopost_books (status)
-- ;;
-- Every Drive file ever noticed. A file is "new" the first time it lands here,
-- and `first_seen` is what the settle delay is measured from.
create table if not exists autopost_seen (
  file_id     text primary key,
  name        text not null,
  slug        text not null,
  size        bigint,
  first_seen  timestamptz not null default now()
)
-- ;;
-- Images. `source = 'upload'` is artwork you supplied in the panel and wins
-- over a render; `source = 'generated'` is what the publisher rendered, kept
-- so the panel can show and hand you the pin and social images.
create table if not exists autopost_artwork (
  slug         text not null,
  kind         text not null check (kind in ('cover', 'thumbnail', 'pin', 'social')),
  source       text not null check (source in ('upload', 'generated')),
  mime         text not null,
  bytes        bytea not null,
  updated_at   timestamptz not null default now(),
  primary key (slug, kind, source)
)
-- ;;
-- The calendar. A row is a promise to publish `slug` at `publish_at`.
--   scheduled   waiting for its time
--   dispatched  the Neon scheduler has started a publish run for it
--   published   done
--   failed      gave up; `last_error` says why
--   cancelled   you removed it
create table if not exists autopost_schedule (
  id             bigserial primary key,
  slug           text not null,
  publish_at     timestamptz not null,
  status         text not null default 'scheduled'
                 check (status in ('scheduled', 'dispatched', 'published', 'failed', 'cancelled')),
  attempts       int not null default 0,
  last_error     text,
  dispatched_at  timestamptz,
  finished_at    timestamptz,
  created_at     timestamptz not null default now()
)
-- ;;
create index if not exists autopost_schedule_due
  on autopost_schedule (publish_at) where status in ('scheduled', 'dispatched')
-- ;;
-- One row per publisher run: what it decided and the full log, so the panel
-- can show what happened without opening GitHub.
create table if not exists autopost_runs (
  id           bigserial primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  summary      jsonb not null default '{}'::jsonb,
  log          jsonb not null default '[]'::jsonb
)
-- ;;
-- The daily Gumroad report written by the Vercel cron (api/cron/daily.js) and
-- shown on /admin. One row per day in the shop's time zone: running the cron
-- twice in a day, whether by hand or because the platform delivered the event
-- twice, replaces that day's report instead of piling up duplicates.
-- It holds aggregates and drafts only. No buyer emails, no raw sales.
create table if not exists autopost_daily_reports (
  day         date primary key,
  created_at  timestamptz not null default now(),
  report      jsonb not null
)

-- ;;
-- Rotating OAuth credentials a platform must keep between runs (Etsy hands out
-- a new refresh token each time it is used). Never returned by the panel API.
create table if not exists autopost_credentials (
  name        text primary key,
  value       text not null,
  updated_at  timestamptz not null default now()
)
