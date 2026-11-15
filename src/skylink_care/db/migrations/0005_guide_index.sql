-- 0005_guide_index: the searchable copy of the device guides.
-- One row per guide section. A build replaces every row in one transaction, so readers see the
-- old index or the new one, never a mixture. The documents themselves stay the source of truth:
-- this table can be dropped and rebuilt at any time.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE guide_chunks (
    chunk_id      text PRIMARY KEY,               -- doc_id#section_id
    doc_id        text NOT NULL,
    doc_type      text NOT NULL,
    device_model  text NOT NULL,
    doc_version   text NOT NULL,
    section_id    text NOT NULL,
    title         text NOT NULL,                  -- "<guide title>: <section title>"
    body          text NOT NULL,
    -- Title and body as searched: spelling variants folded together ("Wi-Fi" and "wifi").
    search_title  text NOT NULL,
    search_body   text NOT NULL,
    steps         jsonb NOT NULL DEFAULT '[]',    -- the numbered steps of the section, verbatim
    access_roles  text[] NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(256) NOT NULL,
    -- A customer who names the topic ("wifi", "slow") should land on the section about it,
    -- however often other sections mention the word in passing: the title is matched on its own.
    title_search  tsvector GENERATED ALWAYS AS (to_tsvector('english', search_title)) STORED,
    search        tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', search_title), 'A') ||
                      setweight(to_tsvector('english', search_body), 'B')) STORED
);
CREATE INDEX guide_chunks_by_model ON guide_chunks (device_model, section_id);
CREATE INDEX guide_chunks_search ON guide_chunks USING gin (search);

-- Which build is live (one row).
CREATE TABLE guide_index (
    id            boolean PRIMARY KEY DEFAULT true CHECK (id),
    index_version text NOT NULL,                  -- hash of everything that was indexed
    embedder      text NOT NULL,
    documents     integer NOT NULL,
    chunks        integer NOT NULL,
    built_at      timestamptz NOT NULL DEFAULT clock_timestamp()
);
