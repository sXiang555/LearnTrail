USE tj_course;

-- 应用前检查并备份；不修改现有文章、视频、权益与观看进度。
CREATE TABLE IF NOT EXISTS learntrail_series_article (
    series_id BIGINT NOT NULL,
    article_id BIGINT NOT NULL,
    version VARCHAR(10) NOT NULL COMMENT 'draft/published',
    position INT NOT NULL DEFAULT 0,
    PRIMARY KEY (series_id, version, article_id),
    KEY idx_article (article_id, version, series_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
