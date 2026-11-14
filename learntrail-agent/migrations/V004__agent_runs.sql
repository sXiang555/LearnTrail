-- Manual AI-only migration. Inspect and back up the database before applying.
BEGIN;
ALTER TABLE ai_runs
    ADD COLUMN kind VARCHAR(12) NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','agent')),
    ADD COLUMN agent_data JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(agent_data)='object');
CREATE TABLE ai_agent_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL,
    session_id UUID NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    kind VARCHAR(32) NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0),
    duration_ms BIGINT NOT NULL CHECK (duration_ms >= 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (run_id,session_id) REFERENCES ai_runs(id,session_id),
    UNIQUE (run_id,sequence)
);
CREATE INDEX ai_agent_events_session ON ai_agent_events (session_id,run_id,sequence);
COMMIT;
