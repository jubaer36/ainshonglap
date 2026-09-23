"""The catalog's table layout — one definition, used in two places.

`scripts/build_catalog.py` writes `catalog.sqlite` with it. `corpus_db.py`
uses it to rebuild the same database in memory from Supabase, so
`catalog.py` runs the same queries against either and cannot tell which it
got. Moved here verbatim from the build script: two copies of a schema are
two schemas the moment someone edits one.
"""

SCHEMA = """
PRAGMA journal_mode = DELETE;

CREATE TABLE acts (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    act_no      TEXT,
    source_pdf  TEXT,
    n_chapters  INTEGER NOT NULL,
    n_sections  INTEGER NOT NULL
);

CREATE TABLE chapters (
    id          INTEGER PRIMARY KEY,
    act_id      INTEGER NOT NULL REFERENCES acts(id),
    number      TEXT,              -- as printed: 'XVI'
    number_int  INTEGER,           -- 16, so 'chapter 16' finds 'XVI'
    title       TEXT,
    part        TEXT,
    part_title  TEXT,
    seq         INTEGER NOT NULL,  -- position in the Act, as parsed
    n_sections  INTEGER NOT NULL
);

CREATE TABLE sections (
    id          INTEGER PRIMARY KEY,
    act_id      INTEGER NOT NULL REFERENCES acts(id),
    chapter_id  INTEGER REFERENCES chapters(id),
    number      TEXT NOT NULL,     -- as printed: '302', '302A'
    number_to   TEXT,              -- '5' in a '4 to 5' heading, else NULL
    num_int     INTEGER,           -- 302   ) ordering and range predicates:
    num_suffix  TEXT,              -- 'A'   ) 302 < 302A < 303
    title       TEXT,
    text        TEXT NOT NULL,
    page_start  INTEGER,
    page_end    INTEGER,
    seq         INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,  -- 'penal-s302', the citation key the API uses
    dup_seq     INTEGER NOT NULL,  -- 0 for the section proper, 1+ for continuations
    continuation INTEGER NOT NULL  -- 1 = a fragment the parser re-detected; see below
);

CREATE TABLE subsections (
    id          INTEGER PRIMARY KEY,
    section_id  INTEGER NOT NULL REFERENCES sections(id),
    parent_id   INTEGER REFERENCES subsections(id),   -- clauses nest in subsections
    number      TEXT,
    type        TEXT NOT NULL,     -- subsection | clause | explanation | ...
    text        TEXT NOT NULL,
    depth       INTEGER NOT NULL,
    seq         INTEGER NOT NULL
);

CREATE TABLE cross_references (
    id            INTEGER PRIMARY KEY,
    section_id    INTEGER NOT NULL REFERENCES sections(id),
    target_act_id INTEGER REFERENCES acts(id),
    target_number TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- 'single' | 'range'
    target_to     TEXT,
    raw           TEXT NOT NULL    -- the phrase it was read from, for auditing
);

CREATE INDEX idx_sections_act_num   ON sections(act_id, num_int, num_suffix);
CREATE INDEX idx_sections_act_chap  ON sections(act_id, chapter_id);
CREATE INDEX idx_sections_key       ON sections(key);
CREATE INDEX idx_sections_title     ON sections(title);
CREATE INDEX idx_chapters_act       ON chapters(act_id, number_int);
CREATE INDEX idx_subsections_sec    ON subsections(section_id, seq);
CREATE INDEX idx_xref_section       ON cross_references(section_id);
CREATE INDEX idx_xref_target        ON cross_references(target_act_id, target_number);

-- Exact-phrase and keyword search, which dense retrieval is bad at: rare terms,
-- proper nouns and numbers. Not a replacement for the vector index — a
-- complement, and the catalog's own find box.
CREATE VIRTUAL TABLE sections_fts USING fts5(
    title, text, content='sections', content_rowid='id', tokenize='porter unicode61'
);
"""
