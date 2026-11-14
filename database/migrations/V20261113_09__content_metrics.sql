-- Inspect the actual schema and take a private backup before applying once.
-- No fabricated historical views; like snapshots are synchronized from Redis.
ALTER TABLE tj_learning.learntrail_article
    ADD COLUMN liked_times BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN view_times BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN stat_version BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD INDEX idx_article_likes (status,liked_times,id),
    ADD INDEX idx_article_views (status,view_times,id);

ALTER TABLE tj_course.learntrail_series
    ADD COLUMN liked_times BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN view_times BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN stat_version BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD INDEX idx_series_likes (status,blocked,liked_times,id),
    ADD INDEX idx_series_views (status,blocked,view_times,id);
