USE tj_learning;

-- 应用前备份，并检查本迁移尚未执行。既有四篇文章继续公开。
ALTER TABLE learntrail_article
    MODIFY COLUMN body MEDIUMTEXT NOT NULL,
    ADD COLUMN author_user_id BIGINT NULL,
    ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'published',
    ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    ADD COLUMN published_at DATETIME NULL,
    ADD INDEX idx_article_author (author_user_id, status, id),
    ADD INDEX idx_article_published (status, published_at, id);

UPDATE learntrail_article SET published_at=created_at WHERE status='published' AND published_at IS NULL;
