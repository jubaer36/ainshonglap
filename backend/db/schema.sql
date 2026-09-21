-- AinShonglap · Supabase schema
--
-- Run once in the Supabase SQL editor (or via `supabase db push`).
--
-- Identity comes from Supabase Auth (`auth.users`); nothing here duplicates it.
--
-- NOTE ON ISOLATION. The API talks to Supabase with the SERVICE ROLE key, which
-- bypasses Row Level Security by design. The policies below are therefore NOT
-- what protects one user's questions from another — `app/db/conversations.py`
-- filters every statement by `user_id`, and that code is the actual boundary.
-- The policies are kept anyway so that anything which ever connects with an
-- anon key (a dashboard, a future direct-from-browser path) is safe by default
-- rather than open.

create table if not exists conversations (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users(id) on delete cascade,
    title       text not null default 'New conversation',
    -- The rolling summary the next question is read against, plus the section
    -- keys written by code rather than by the summariser.
    memory      jsonb,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table if not exists messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    -- Denormalised from the conversation so every per-user filter can be
    -- applied on this table directly, without a join that could be forgotten.
    user_id         uuid not null references auth.users(id) on delete cascade,
    role            text not null check (role in ('user', 'assistant')),
    -- The user's text, or the whole AssistantMessage. Stored as sent so a
    -- reopened conversation renders exactly as it did, including citations.
    content         jsonb not null,
    -- The conversation memory as it stood AFTER this turn. Stored per message,
    -- not per conversation: an answer was produced against the context of its
    -- own moment, and replaying it against a later summary explains the wrong
    -- thing.
    memory          jsonb,
    generator       text,
    plan            jsonb,
    seq             integer not null,
    created_at      timestamptz not null default now(),
    unique (conversation_id, seq)
);

-- Page images sent to the vision reader. The transcription itself is a draft of
-- a question and lives in `messages`; this is the provenance for it.
create table if not exists uploads (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users(id) on delete cascade,
    conversation_id uuid references conversations(id) on delete cascade,
    message_id      uuid references messages(id) on delete set null,
    -- Path inside the `page-images` storage bucket.
    path            text not null,
    bytes           integer not null,
    mime            text not null,
    chars           integer,
    -- What the vision model read off the page, kept beside the page so the two
    -- can be compared — the reading is a generative guess, and the image is the
    -- only thing that can check it.
    transcript      text,
    created_at      timestamptz not null default now()
);

create index if not exists idx_conversations_user   on conversations (user_id, updated_at desc);
create index if not exists idx_messages_conversation on messages (conversation_id, seq);
create index if not exists idx_messages_user        on messages (user_id, created_at desc);
create index if not exists idx_uploads_user         on uploads (user_id, created_at desc);

-- Keep `updated_at` honest, so "most recent conversation" means it.
create or replace function touch_conversation() returns trigger as $$
begin
    update conversations set updated_at = now() where id = new.conversation_id;
    return new;
end;
$$ language plpgsql;

drop trigger if exists messages_touch_conversation on messages;
create trigger messages_touch_conversation
    after insert on messages
    for each row execute function touch_conversation();

alter table conversations enable row level security;
alter table messages      enable row level security;
alter table uploads       enable row level security;

drop policy if exists own_conversations on conversations;
create policy own_conversations on conversations
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists own_messages on messages;
create policy own_messages on messages
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists own_uploads on uploads;
create policy own_uploads on uploads
    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
