USE tj_course;

-- 应用前备份并检查列不存在；保留全部既有课程、目录、学习权益。
ALTER TABLE learntrail_series
    ADD COLUMN draft_payload JSON NULL,
    ADD COLUMN draft_preview_section_id BIGINT NULL,
    ADD COLUMN draft_preview_minutes INT NOT NULL DEFAULT 3,
    ADD COLUMN blocked TINYINT NOT NULL DEFAULT 0;

UPDATE learntrail_series SET draft_preview_section_id=preview_section_id,
    draft_preview_minutes=COALESCE(preview_minutes,3) WHERE status='draft';
