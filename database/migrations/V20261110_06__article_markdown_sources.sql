USE tj_learning;

-- 应用前核对列并备份 learntrail_article；本 ALTER 不可重复执行。
-- 原有正文与作者保持不变，旧文章默认纯文本和原创类型。
ALTER TABLE learntrail_article
    ADD COLUMN content_format VARCHAR(16) NOT NULL DEFAULT 'text',
    ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'original',
    ADD COLUMN source_author VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN source_url VARCHAR(2048) NOT NULL DEFAULT '',
    ADD COLUMN license_note VARCHAR(500) NOT NULL DEFAULT '';

-- 回退应用时保留新增列和文章，不执行 DROP 或清空用户内容。
