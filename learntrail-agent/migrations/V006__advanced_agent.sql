-- Apply manually after inspection and a private backup. AI database only.
BEGIN;
ALTER TABLE ai_sessions ADD CONSTRAINT ai_sessions_id_owner UNIQUE(id,user_id);
ALTER TABLE ai_messages ADD CONSTRAINT ai_messages_id_session UNIQUE(id,session_id);

CREATE TABLE ai_approvals (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL CHECK(user_id>0),
    session_id UUID NOT NULL,
    run_id UUID NOT NULL,
    call_id VARCHAR(200) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    tool_revision CHAR(64) NOT NULL,
    arguments_hash CHAR(64) NOT NULL,
    arguments_type VARCHAR(40) NOT NULL,
    arguments_blob BYTEA NOT NULL,
    resource_snapshot JSONB NOT NULL CHECK(jsonb_typeof(resource_snapshot)='object'),
    status VARCHAR(16) NOT NULL CHECK(status IN
        ('pending','approved','rejected','expired','cancelled','executing','executed','failed','unknown')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    result_type VARCHAR(40),
    result_blob BYTEA,
    error_code VARCHAR(64),
    UNIQUE(run_id,call_id),
    FOREIGN KEY(session_id,user_id) REFERENCES ai_sessions(id,user_id),
    FOREIGN KEY(run_id,session_id) REFERENCES ai_runs(id,session_id),
    CHECK(expires_at>created_at),
    CHECK((result_type IS NULL)=(result_blob IS NULL))
);
CREATE INDEX ai_approvals_owner_pending ON ai_approvals(user_id,status,expires_at);
CREATE INDEX ai_approvals_run ON ai_approvals(session_id,run_id,created_at);

CREATE TABLE ai_demo_actions (
    approval_id UUID PRIMARY KEY REFERENCES ai_approvals(id),
    user_id BIGINT NOT NULL CHECK(user_id>0),
    draft_id UUID NOT NULL UNIQUE,
    payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE ai_memories (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL CHECK(user_id>0),
    memory_type VARCHAR(20) NOT NULL CHECK(memory_type IN ('preference','learning_goal','study_habit')),
    key VARCHAR(40) NOT NULL,
    content VARCHAR(500) NOT NULL CHECK(length(btrim(content))>=2),
    content_hash CHAR(64) NOT NULL,
    search_terms TEXT[] NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
    status VARCHAR(12) NOT NULL DEFAULT 'active' CHECK(status IN ('active','deleted','expired')),
    source_kind VARCHAR(16) NOT NULL CHECK(source_kind IN ('manual','user_quote')),
    source_session_id UUID,
    source_message_id UUID,
    source_quote VARCHAR(1000),
    last_approval_id UUID REFERENCES ai_approvals(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ,
    FOREIGN KEY(source_session_id,user_id) REFERENCES ai_sessions(id,user_id),
    FOREIGN KEY(source_message_id,source_session_id) REFERENCES ai_messages(id,session_id),
    CHECK(source_message_id IS NULL OR source_session_id IS NOT NULL),
    CHECK(source_kind<>'user_quote' OR (source_message_id IS NOT NULL AND source_quote IS NOT NULL))
);
CREATE UNIQUE INDEX ai_memories_active_slot ON ai_memories(user_id,memory_type,key) WHERE status='active';
CREATE INDEX ai_memories_owner_updated ON ai_memories(user_id,updated_at DESC);
CREATE INDEX ai_memories_terms ON ai_memories USING GIN(search_terms);
COMMIT;
