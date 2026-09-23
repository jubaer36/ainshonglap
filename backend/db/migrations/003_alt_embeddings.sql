-- 003: vectors from other embedders, beside BGE-M3's (E21).
--
-- Apply by hand in the Supabase SQL editor, after 002. Safe to re-run.
--
-- `law_chunks.embedding` is vector(1024) — BGE-M3, and nothing about it changes
-- here. nemotron-3-embed produces 2048 dimensions, so its vectors cannot share
-- that column. They get their own table instead, one row per (model, chunk).
--
-- `embedding` has NO fixed dimension: pgvector's plain `vector` accepts any
-- length. Comparing vectors of different lengths is an error, which is why
-- every search filters to ONE model first — within a model all rows have the
-- same length. No vector index, for the same reason as law_chunks (002): the
-- numbers were measured on an exact scan, and at ~2,300 rows per model an exact
-- scan takes milliseconds. (Approximate indexes would also need a fixed
-- dimension.)
--
-- Loaded by `python scripts/load_supabase.py --embedder nemotron-3-embed` from
-- Laws/main/index/embeddings.nemotron-3-embed.npy. Rows carry their own text and
-- metadata so a search needs no join and returns what law_match_chunks returns.

create table if not exists law_alt_chunks (
    id           bigint generated always as identity primary key,
    model        text not null,                 -- e.g. 'nemotron-3-embed'
    vec_id       integer not null,              -- row in chunks.json
    chunk_id     text not null,
    act_slug     text not null references law_acts(slug) on delete cascade,
    section      text not null,
    granularity  text not null,
    text         text not null,
    metadata     jsonb not null,
    embedding    extensions.vector not null
);

create index if not exists law_alt_chunks_model_act on law_alt_chunks (model, act_slug);

-- Per (model, Act) commit marker, like law_loads: written last, so an Act whose
-- digest is missing here was interrupted mid-load.
create table if not exists law_alt_loads (
    model       text not null,
    act_slug    text not null references law_acts(slug) on delete cascade,
    digest      text not null,
    n_chunks    integer not null,
    loaded_at   timestamptz not null default now(),
    primary key (model, act_slug)
);

create or replace function law_match_alt_chunks(query_embedding extensions.vector,
                                                model_name text,
                                                match_count integer)
returns table (vec_id integer, chunk_id text, act_slug text, section text,
               granularity text, text text, metadata jsonb, score real)
language sql stable
set search_path = public, extensions
as $$
    select c.vec_id, c.chunk_id, c.act_slug, c.section, c.granularity,
           c.text, c.metadata, (-(c.embedding <#> query_embedding))::real
    from law_alt_chunks c
    where c.model = model_name
    order by c.embedding <#> query_embedding
    limit least(greatest(match_count, 1), 200);
$$;

-- Read by anyone, written only by the service role — as for the other law_* tables.
alter table law_alt_chunks enable row level security;
alter table law_alt_loads  enable row level security;

drop policy if exists law_read on law_alt_chunks;
create policy law_read on law_alt_chunks for select using (true);
drop policy if exists law_read on law_alt_loads;
create policy law_read on law_alt_loads  for select using (true);
