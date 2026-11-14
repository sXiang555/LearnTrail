-- Apply manually after V002, after inspecting and backing up the AI database.
-- Existing chunks are backfilled in-place on the next authorized BM25 search;
-- no embedding requests or article re-creation are needed.
BEGIN;
ALTER TABLE ai_article_chunks
    ADD COLUMN bm25_terms JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(bm25_terms) = 'object'),
    ADD COLUMN bm25_length INTEGER NOT NULL DEFAULT 0 CHECK (bm25_length >= 0),
    ADD COLUMN tokenizer_version TEXT NOT NULL DEFAULT '';
CREATE INDEX ai_article_chunks_keywords ON ai_article_chunks USING GIN (bm25_terms jsonb_ops);
COMMIT;
