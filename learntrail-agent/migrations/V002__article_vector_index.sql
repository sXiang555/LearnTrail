-- Manual migration for the dedicated AI PostgreSQL database, after V001.
-- Requires server-side pgvector to be installed by the environment owner.
-- Inspect/backup existing schema first. Never run automatically at startup.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ai_article_indexes (
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    article_id BIGINT NOT NULL CHECK (article_id > 0),
    slug VARCHAR(80) NOT NULL,
    title TEXT NOT NULL,
    content_hash CHAR(64) CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    fingerprint CHAR(64) CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    generation UUID,
    dimensions INTEGER CHECK (dimensions BETWEEN 1 AND 16000),
    embedding_model TEXT,
    provider_model TEXT,
    source_metadata JSONB NOT NULL DEFAULT '{}',
    embedding_usage JSONB,
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    pending_token UUID,
    pending_fingerprint CHAR(64),
    pending_expires_at TIMESTAMPTZ,
    indexed_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, article_id),
    UNIQUE (user_id, slug),
    UNIQUE (user_id, article_id, generation, dimensions),
    CHECK (NOT active OR (content_hash IS NOT NULL AND fingerprint IS NOT NULL AND generation IS NOT NULL
           AND dimensions IS NOT NULL AND embedding_model IS NOT NULL AND chunk_count > 0)),
    CHECK ((pending_token IS NULL AND pending_fingerprint IS NULL AND pending_expires_at IS NULL)
        OR (pending_token IS NOT NULL AND pending_fingerprint IS NOT NULL AND pending_expires_at IS NOT NULL))
);
CREATE INDEX ai_article_indexes_active ON ai_article_indexes (user_id, article_id) WHERE active;

CREATE TABLE ai_article_chunks (
    user_id BIGINT NOT NULL,
    article_id BIGINT NOT NULL,
    generation UUID NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char > start_char),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 8000 AND length(content) = end_char - start_char),
    dimensions INTEGER NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    embedding vector NOT NULL CHECK (vector_dims(embedding) = dimensions),
    PRIMARY KEY (user_id, article_id, ordinal),
    FOREIGN KEY (user_id, article_id, generation, dimensions)
        REFERENCES ai_article_indexes(user_id, article_id, generation, dimensions) ON DELETE CASCADE
);
-- No ANN index: different models/dimensions must not share one vector search space.
COMMIT;
