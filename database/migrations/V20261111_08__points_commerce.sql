-- 应用前检查实际结构并备份。历史人民币数据不转换、不重写。
USE tj_learning;
CREATE TABLE IF NOT EXISTS learntrail_point_rule (
    kind VARCHAR(20) PRIMARY KEY, amount INT NOT NULL, daily_cap INT NOT NULL,
    min_seconds INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT IGNORE INTO learntrail_point_rule VALUES
    ('READ',20,200,30),('WATCH',10,100,60),('SIGN',5,5,0),('COMMENT',2,10,0);
CREATE TABLE IF NOT EXISTS learntrail_learning_activity (
    id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, kind VARCHAR(10) NOT NULL,
    target_id BIGINT NOT NULL, credited_seconds INT NOT NULL DEFAULT 0,
    last_position DOUBLE NOT NULL DEFAULT 0, last_seen DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_activity (user_id,kind,target_id,credited_seconds)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learntrail_point_comment (
    user_id BIGINT NOT NULL, fingerprint CHAR(64) NOT NULL, reply_id BIGINT NOT NULL,
    PRIMARY KEY(user_id,fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learntrail_point_payment (
    order_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, kind VARCHAR(10) NOT NULL,
    target_id BIGINT NOT NULL, amount INT NOT NULL DEFAULT 0,
    status VARCHAR(12) NOT NULL COMMENT 'CAPTURED/CANCELLED',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_payment_user(user_id,order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

USE tj_trade;
CREATE TABLE IF NOT EXISTS learntrail_point_order (
    order_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, request_key VARCHAR(80) NOT NULL,
    kind VARCHAR(10) NOT NULL COMMENT 'COURSE/COUPON', target_id BIGINT NOT NULL,
    title VARCHAR(200) NOT NULL, slug VARCHAR(100) NULL,
    total_points INT NOT NULL, discount_points INT NOT NULL DEFAULT 0, payable_points INT NOT NULL,
    user_coupon_id BIGINT NULL, phase VARCHAR(16) NOT NULL DEFAULT 'CREATED',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_point_request(user_id,request_key), KEY idx_point_recovery(phase,updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

USE tj_promotion;
-- 只有显式登记的模板才能用于积分订单，原 coupon 中金额字段在这些模板上表示整数积分。
CREATE TABLE IF NOT EXISTS learntrail_coupon_points (
    coupon_id BIGINT PRIMARY KEY, exchange_points INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learntrail_coupon_reservation (
    order_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, user_coupon_id BIGINT NOT NULL,
    active_coupon_id BIGINT NULL, discount_points INT NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'RESERVED',
    UNIQUE KEY uk_active_coupon(active_coupon_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS learntrail_coupon_claim (
    order_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, coupon_id BIGINT NOT NULL,
    user_coupon_id BIGINT NOT NULL, claim_key VARCHAR(100) NOT NULL,
    status VARCHAR(12) NOT NULL COMMENT 'RESERVED/COMPLETED/CANCELLED',
    UNIQUE KEY uk_claim_key(claim_key), KEY idx_claim_user(user_id,coupon_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
