-- Everything needed to reload a session exactly as it was.
--
-- Run in the Supabase SQL editor, after db/schema.sql. Safe to re-run.
--
-- Two things were missing.
--
-- 1. CONTEXT AT EACH STEP. The rolling summary was stored once per
--    conversation, so reopening restored only the LATEST context. That is
--    enough to carry on, and not enough to understand what happened: an answer
--    at turn 3 was produced against the summary as it stood at turn 3, and
--    replaying it against turn 7's summary explains the wrong thing. The
--    snapshot now lives on the message.
--
-- 2. IMAGES. A page photographed and read into a question is part of the
--    session's evidence. Without it, a reopened conversation shows a
--    transcription with no way to see what was actually on the page — and the
--    transcription is a vision model's reading, which is exactly the thing
--    worth checking against the original.

alter table messages
    -- The conversation memory as it stood AFTER this turn: the prose summary
    -- plus the section keys written by code. Replaying to any turn restores the
    -- context that turn was answered against.
    add column if not exists memory jsonb,
    -- Which model produced it, and what the planner decided. Both already ride
    -- inside `content`; these are lifted out so a session can be filtered and
    -- audited without unpacking every row.
    add column if not exists generator text,
    add column if not exists plan jsonb;

alter table uploads
    -- Which conversation and turn the image belonged to. Without these an
    -- upload is an orphan file and cannot be shown when the session reopens.
    add column if not exists conversation_id uuid references conversations(id) on delete cascade,
    add column if not exists message_id uuid references messages(id) on delete set null,
    -- What the vision model read off it. Kept beside the image so the reading
    -- and the page can be compared later, which is the whole point of storing
    -- the page.
    add column if not exists transcript text;

create index if not exists idx_uploads_conversation on uploads (conversation_id, created_at);

-- Storage policies for the private `page-images` bucket. The API uploads with
-- the service-role key, so these matter only for any client that ever reaches
-- storage directly — same reasoning as the table policies.
drop policy if exists own_page_images on storage.objects;
create policy own_page_images on storage.objects
    for all
    using (bucket_id = 'page-images' and owner = auth.uid())
    with check (bucket_id = 'page-images' and owner = auth.uid());
