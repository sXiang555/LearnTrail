-- Manual migration. Inspect and back up AI PostgreSQL before applying.
-- Saver 3.0.4 migrations 0..9, ordinary indexes inside one atomic transaction.
-- No runtime setup(); keep this file unchanged after application.
BEGIN;
ALTER TABLE ai_runs DROP CONSTRAINT ai_runs_status_check;
ALTER TABLE ai_runs ADD CONSTRAINT ai_runs_status_check CHECK (status IN ('running','succeeded','failed','cancelled','paused'));
-- Upstream checkpoint migration 0
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);
INSERT INTO checkpoint_migrations(v) VALUES (0) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 1
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
INSERT INTO checkpoint_migrations(v) VALUES (1) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 2
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
INSERT INTO checkpoint_migrations(v) VALUES (2) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 3
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
INSERT INTO checkpoint_migrations(v) VALUES (3) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 4
ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;
INSERT INTO checkpoint_migrations(v) VALUES (4) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 5
SELECT 1;
INSERT INTO checkpoint_migrations(v) VALUES (5) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 6
CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);
INSERT INTO checkpoint_migrations(v) VALUES (6) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 7
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
INSERT INTO checkpoint_migrations(v) VALUES (7) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 8
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);
INSERT INTO checkpoint_migrations(v) VALUES (8) ON CONFLICT DO NOTHING;
-- Upstream checkpoint migration 9
ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';
INSERT INTO checkpoint_migrations(v) VALUES (9) ON CONFLICT DO NOTHING;
COMMIT;
