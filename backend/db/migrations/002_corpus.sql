-- The statute corpus, in Postgres.
--
-- Run in the Supabase SQL editor, after db/schema.sql and 001. Safe to re-run.
--
-- Until now the corpus lived in three local files: catalog.sqlite (exact
-- lookups), chunks.faiss + chunks.json (meaning search), and chunks.json again
-- for the side panels. These tables replace all three, so answers and previews
-- read one copy of the text, and adding an Act is a load rather than a rebuild
-- and redeploy.
--
-- WHAT THIS IS NOT: a new source of law. The PDFs are the source; the parsed
-- JSON is what is read from them and verified. These tables are a read model
-- built from that, by scripts/load_supabase.py, and checked against it by
-- scripts/verify/verify_supabase.py. If this database ever disagrees with the
-- parsed JSON, the database is wrong.
--
-- Everything is prefixed `law_` to keep it apart from conversations, messages
-- and uploads, which hold user data under a completely different access rule.

create extension if not exists vector with schema extensions;


-- ── structure ───────────────────────────────────────────────────────────────
-- Keys are natural rather than serial, so re-loading one Act is idempotent: the
-- same section always gets the same key, and a reload replaces rather than
-- duplicates. Deleting an Act row cascades to everything under it, which is
-- what makes a per-Act reload a single delete followed by inserts.

create table if not exists law_acts (
    slug        text primary key,              -- 'penal'
    name        text not null,                 -- 'The Penal Code, 1860'
    act_no      text,
    source_pdf  text,
    n_chapters  integer not null,
    n_sections  integer not null
);

create table if not exists law_chapters (
    id          text primary key,              -- 'penal:ch16', act + position
    act_slug    text not null references law_acts(slug) on delete cascade,
    number      text,                          -- as printed: 'XVI'
    number_int  integer,                       -- 16, so 'chapter 16' finds 'XVI'
    title       text,
    part        text,
    part_title  text,
    seq         integer not null,
    n_sections  integer not null
);

create table if not exists law_sections (
    key          text primary key,             -- 'penal-s302', the citation key
    act_slug     text not null references law_acts(slug) on delete cascade,
    chapter_id   text references law_chapters(id) on delete cascade,
    number       text not null,                -- as printed: '302', '302A'
    number_to    text,                         -- '5' in a '4 to 5' heading
    num_int      integer,                      -- 302   ) ordering and ranges:
    num_suffix   text,                         -- 'A'   ) 302 < 302A < 303
    title        text,
    text         text not null,
    page_start   integer,
    page_end     integer,
    seq          integer not null,
    -- A page-break fragment the parser re-read as a new section heading. Kept,
    -- flagged, and denied the plain citation key (it gets '#2'), so 'penal-s2'
    -- can never resolve to a stray paragraph from page 104.
    dup_seq      integer not null,
    continuation boolean not null,
    -- Full-text search, replacing SQLite FTS5. Generated, so it cannot drift
    -- from the text it indexes.
    fts          tsvector generated always as
                   (to_tsvector('english', coalesce(title, '') || ' ' || text)) stored
);

create table if not exists law_subsections (
    id          text primary key,              -- 'penal-s302:ss3'
    section_key text not null references law_sections(key) on delete cascade,
    parent_id   text,                          -- clauses nest in subsections
    number      text,
    type        text not null,                 -- subsection | clause | explanation ...
    text        text not null,
    depth       integer not null,
    seq         integer not null
);

create table if not exists law_cross_references (
    id              text primary key,          -- 'crpc-s155:r0'
    section_key     text not null references law_sections(key) on delete cascade,
    target_act_slug text,                      -- null when the build would not guess
    target_number   text not null,
    kind            text not null,             -- 'single' | 'range'
    target_to       text,
    raw             text not null              -- the phrase it was read from
);


-- ── meaning search ──────────────────────────────────────────────────────────
-- One row per vector: 1,321 section chunks and 999 subsection chunks, 1024-dim
-- BGE-M3, unit length. The embeddings are computed locally and loaded as-is;
-- pgvector stores and compares them, it does not produce them.
--
-- `vec_id` is the row's position in the local FAISS index it was loaded from.
-- It is what the verifier uses to compare the two stores result for result,
-- and it is deliberately NOT the primary key: positions are only stable within
-- one build, and adding an Act shifts them.
--
-- NO VECTOR INDEX, on purpose. FAISS here is IndexFlatIP — an exact scan — and
-- the retrieval numbers (R@1 41.8%, R@5 59.0%, R@20 71.4%) were measured on
-- that. An HNSW or IVF index is approximate and can drop a good match, which
-- would change those numbers. At 2,320 rows an exact scan takes a few
-- milliseconds, so there is nothing to buy with an approximation. Revisit only
-- at hundreds of thousands of rows, and then re-measure.

create table if not exists law_chunks (
    id           bigint generated always as identity primary key,
    vec_id       integer not null,
    chunk_id     text not null,                -- not unique: 36 subsection ids repeat
    act_slug     text not null references law_acts(slug) on delete cascade,
    section      text not null,                -- as printed, for grouping hits
    granularity  text not null,                -- 'section' | 'child'
    text         text not null,
    metadata     jsonb not null,               -- exactly as in chunks.json
    embedding    extensions.vector(1024) not null
);


-- ── load record ─────────────────────────────────────────────────────────────
-- Written LAST by the loader, after every row for the Act is in. A load is
-- several API calls and cannot be one transaction, so this row is the commit
-- marker: an Act without a matching digest here was interrupted mid-load and
-- is not to be trusted.

create table if not exists law_loads (
    act_slug    text primary key references law_acts(slug) on delete cascade,
    digest      text not null,                 -- sha256 of everything loaded
    n_sections  integer not null,
    n_chunks    integer not null,
    loaded_at   timestamptz not null default now()
);


-- ── indexes (ordinary ones) ─────────────────────────────────────────────────

create index if not exists law_chapters_act     on law_chapters (act_slug, number_int);
create index if not exists law_sections_act_num on law_sections (act_slug, num_int, num_suffix);
create index if not exists law_sections_chapter on law_sections (chapter_id);
create index if not exists law_sections_fts     on law_sections using gin (fts);
create index if not exists law_subsections_sec  on law_subsections (section_key, seq);
create index if not exists law_xref_section     on law_cross_references (section_key);
create index if not exists law_xref_target      on law_cross_references (target_act_slug, target_number);
create index if not exists law_chunks_act       on law_chunks (act_slug);
create index if not exists law_chunks_vec       on law_chunks (vec_id);


-- ── search function ─────────────────────────────────────────────────────────
-- Exact nearest neighbours by inner product. `<#>` is pgvector's NEGATIVE inner
-- product, so ascending order is best-first and the score is its negation —
-- the same number FAISS IndexFlatIP returns, which is what lets the verifier
-- compare the two directly.
--
-- Called over RPC with the question's embedding. The caller supplies only a
-- vector and a count; the query itself is fixed here, so nothing a model
-- produces ever becomes SQL.

create or replace function law_match_chunks(query_embedding extensions.vector(1024),
                                            match_count integer)
returns table (vec_id integer, chunk_id text, act_slug text, section text,
               granularity text, text text, metadata jsonb, score real)
language sql stable
set search_path = public, extensions
as $$
    select c.vec_id, c.chunk_id, c.act_slug, c.section, c.granularity,
           c.text, c.metadata, (-(c.embedding <#> query_embedding))::real
    from law_chunks c
    order by c.embedding <#> query_embedding
    limit least(greatest(match_count, 1), 200);
$$;


-- ── access ──────────────────────────────────────────────────────────────────
-- Statute text is public and read-only. Anyone may read; nobody may write
-- except the service role, which bypasses RLS and is used only by the loader.
-- Unlike the conversation tables, there is nothing here to isolate per user.

alter table law_acts             enable row level security;
alter table law_chapters         enable row level security;
alter table law_sections         enable row level security;
alter table law_subsections      enable row level security;
alter table law_cross_references enable row level security;
alter table law_chunks           enable row level security;
alter table law_loads            enable row level security;

drop policy if exists law_read on law_acts;
create policy law_read on law_acts             for select using (true);
drop policy if exists law_read on law_chapters;
create policy law_read on law_chapters         for select using (true);
drop policy if exists law_read on law_sections;
create policy law_read on law_sections         for select using (true);
drop policy if exists law_read on law_subsections;
create policy law_read on law_subsections      for select using (true);
drop policy if exists law_read on law_cross_references;
create policy law_read on law_cross_references for select using (true);
drop policy if exists law_read on law_chunks;
create policy law_read on law_chunks           for select using (true);
drop policy if exists law_read on law_loads;
create policy law_read on law_loads            for select using (true);
