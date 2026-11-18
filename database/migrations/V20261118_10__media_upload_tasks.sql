-- 先核对实际结构并备份。仅新增表；不迁移、删除或重写原 media/课程记录。
USE tj_media;
CREATE TABLE IF NOT EXISTS learntrail_media_upload (
    id CHAR(32) PRIMARY KEY, user_id BIGINT NOT NULL, request_key VARCHAR(80) NOT NULL,
    filename VARCHAR(100) NOT NULL, file_size BIGINT NOT NULL, sha256 CHAR(64) NOT NULL,
    part_size INT NOT NULL, part_count INT NOT NULL, bucket VARCHAR(63) NOT NULL,
    object_key VARCHAR(250) NOT NULL, storage_upload_id VARCHAR(300) NULL,
    phase VARCHAR(20) NOT NULL DEFAULT 'INITIATING', media_id BIGINT NULL,
    lease_token CHAR(32) NULL, lease_until DATETIME(3) NULL,
    expires_at DATETIME(3) NOT NULL, error_code VARCHAR(80) NULL,
    created_at DATETIME(3) NOT NULL, updated_at DATETIME(3) NOT NULL,
    UNIQUE KEY uk_media_upload_request(user_id,request_key),
    KEY idx_media_upload_expiry(phase,expires_at),
    KEY idx_media_upload_owner(user_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_media_upload_part (
    upload_id CHAR(32) NOT NULL, part_number INT NOT NULL,
    etag VARCHAR(150) NOT NULL, part_size BIGINT NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY(upload_id,part_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 同一条持久化任务也是待投递记录。消息只是唤醒信号，数据库决定执行资格。
CREATE TABLE IF NOT EXISTS learntrail_media_task (
    id BIGINT PRIMARY KEY, upload_id CHAR(32) NOT NULL, media_id BIGINT NOT NULL,
    pipeline_version INT NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING', attempts INT NOT NULL DEFAULT 0,
    run_token CHAR(32) NULL, lease_until DATETIME(3) NULL,
    next_attempt_at DATETIME(3) NOT NULL, dispatch_after DATETIME(3) NOT NULL,
    error_code VARCHAR(80) NULL, created_at DATETIME(3) NOT NULL, updated_at DATETIME(3) NOT NULL,
    UNIQUE KEY uk_media_pipeline(upload_id,pipeline_version),
    KEY idx_media_task_dispatch(status,next_attempt_at,dispatch_after),
    KEY idx_media_task_lease(status,lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 只有服务端读取对象并核验 SHA-256、媒体处理成功后才登记；去重不跨用户授予权益。
CREATE TABLE IF NOT EXISTS learntrail_media_digest (
    user_id BIGINT NOT NULL, sha256 CHAR(64) NOT NULL, file_size BIGINT NOT NULL,
    pipeline_version INT NOT NULL, media_id BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY(user_id,sha256,file_size,pipeline_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
