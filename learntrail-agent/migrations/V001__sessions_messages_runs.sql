-- Apply once, manually, to a dedicated LearnTrail AI PostgreSQL database.
-- Inspect the target schema and back it up first. Never run against Java MySQL.
-- The application does not run migrations at startup.
BEGIN;

CREATE TABLE ai_sessions (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL CHECK (user_id > 0),
    title VARCHAR(200) NOT NULL CHECK (length(btrim(title)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ai_sessions_owner_updated ON ai_sessions (user_id, updated_at DESC, id DESC);

CREATE TABLE ai_runs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES ai_sessions(id),
    status VARCHAR(12) NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled')),
    model VARCHAR(200),
    provider_response_id TEXT,
    finish_reason TEXT,
    usage JSONB CHECK (usage IS NULL OR jsonb_typeof(usage) = 'object'),
    duration_ms BIGINT CHECK (duration_ms >= 0),
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    UNIQUE (id, session_id),
    CHECK (expires_at > created_at),
    CHECK ((status = 'running' AND finished_at IS NULL) OR
           (status <> 'running' AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX ai_runs_one_active ON ai_runs (session_id) WHERE status = 'running';
CREATE INDEX ai_runs_session_created ON ai_runs (session_id, created_at DESC, id DESC);

CREATE TABLE ai_messages (
    id UUID PRIMARY KEY,
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    session_id UUID NOT NULL REFERENCES ai_sessions(id),
    run_id UUID NOT NULL,
    role VARCHAR(12) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(btrim(content)) > 0 AND length(content) <= 200000),
    status VARCHAR(12) NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (run_id, session_id) REFERENCES ai_runs(id, session_id),
    UNIQUE (run_id, role),
    CHECK (role <> 'assistant' OR status = 'completed')
);
CREATE INDEX ai_messages_session_created ON ai_messages (session_id, created_at, sequence);

COMMIT;
