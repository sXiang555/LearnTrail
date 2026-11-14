-- LearnTrail creator storefront. Apply to tj_course after backing up the database.
-- Existing course, order and learning tables are preserved.
USE tj_course;
CREATE TABLE IF NOT EXISTS learntrail_creator (
    id BIGINT NOT NULL PRIMARY KEY,
    owner_user_id BIGINT NULL,
    slug VARCHAR(80) NOT NULL,
    display_name VARCHAR(80) NOT NULL,
    headline VARCHAR(160) NOT NULL,
    bio TEXT NOT NULL,
    initials VARCHAR(8) NOT NULL,
    avatar_color VARCHAR(24) NOT NULL DEFAULT 'forest',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_creator_slug (slug),
    UNIQUE KEY uk_creator_owner (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_series (
    id BIGINT NOT NULL PRIMARY KEY,
    course_id BIGINT NOT NULL,
    creator_id BIGINT NOT NULL,
    slug VARCHAR(100) NOT NULL,
    title VARCHAR(100) NOT NULL,
    subtitle VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    topic VARCHAR(40) NOT NULL,
    cover_tone VARCHAR(24) NOT NULL DEFAULT 'forest',
    preview_section_id BIGINT NULL,
    preview_minutes INT NOT NULL DEFAULT 3,
    featured TINYINT NOT NULL DEFAULT 0,
    sort_order INT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'published',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_series_slug (slug),
    UNIQUE KEY uk_series_course (course_id),
    KEY idx_series_creator (creator_id),
    KEY idx_series_topic (topic, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_watchlist (
    user_id BIGINT NOT NULL,
    series_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, series_id),
    KEY idx_watchlist_series (series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS learntrail_creator_follow (
    user_id BIGINT NOT NULL,
    creator_id BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, creator_id),
    KEY idx_follow_creator (creator_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
