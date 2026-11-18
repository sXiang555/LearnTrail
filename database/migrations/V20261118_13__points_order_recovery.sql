-- Apply after V20261111_08. Existing PAYING and CANCELLING orders become eligible automatically.
USE tj_trade;
ALTER TABLE learntrail_point_order
    ADD COLUMN recovery_attempts INT NOT NULL DEFAULT 0,
    ADD COLUMN recovery_due_at DATETIME(3) NULL,
    ADD COLUMN recovery_token CHAR(32) NULL,
    ADD COLUMN recovery_lease_until DATETIME(3) NULL,
    ADD COLUMN recovery_error VARCHAR(64) NULL,
    ADD KEY idx_point_recovery_due(phase,recovery_due_at,order_id);
