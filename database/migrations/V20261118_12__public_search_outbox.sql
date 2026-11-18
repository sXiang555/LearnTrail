-- Run after V20261118_11. Configure Canal before running database/replay_public_search.sql.
-- Only this projection table is subscribed. Content, wallet and user data tables are not published to the broker.
USE tj_learning;
CREATE TABLE learntrail_search_outbox (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    content_kind VARCHAR(12) NOT NULL,
    content_id BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    visible TINYINT(1) NOT NULL,
    slug VARCHAR(80) NOT NULL,
    title VARCHAR(100) NOT NULL,
    summary TEXT NOT NULL,
    body MEDIUMTEXT NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_search_content(content_kind,content_id,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER //
CREATE TRIGGER learntrail_article_search_added AFTER INSERT ON learntrail_article FOR EACH ROW
BEGIN
    IF NEW.status='published' THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('article',NEW.id,NEW.public_revision,NEW.status='published',NEW.slug,
                    IF(NEW.status='published',NEW.title,''),IF(NEW.status='published',NEW.summary,''),
                    IF(NEW.status='published',NEW.body,''));
    END IF;
END//
CREATE TRIGGER learntrail_article_search_changed AFTER UPDATE ON learntrail_article FOR EACH ROW
BEGIN
    IF NEW.public_revision<>OLD.public_revision AND (NEW.status='published' OR OLD.status='published') THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('article',NEW.id,NEW.public_revision,NEW.status='published',NEW.slug,
                    IF(NEW.status='published',NEW.title,''),IF(NEW.status='published',NEW.summary,''),
                    IF(NEW.status='published',NEW.body,''));
    END IF;
END//
CREATE TRIGGER learntrail_article_search_removed AFTER DELETE ON learntrail_article FOR EACH ROW
BEGIN
    IF OLD.status='published' THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('article',OLD.id,OLD.public_revision+1,0,OLD.slug,'','','');
    END IF;
END//
DELIMITER ;

USE tj_course;
CREATE TABLE learntrail_search_outbox (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    content_kind VARCHAR(12) NOT NULL,
    content_id BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    visible TINYINT(1) NOT NULL,
    slug VARCHAR(80) NOT NULL,
    title VARCHAR(100) NOT NULL,
    summary TEXT NOT NULL,
    body MEDIUMTEXT NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_search_content(content_kind,content_id,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER //
CREATE TRIGGER learntrail_series_search_added AFTER INSERT ON learntrail_series FOR EACH ROW
BEGIN
    IF NEW.status='published' AND NEW.blocked=0 THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('series',NEW.id,NEW.public_revision,NEW.status='published' AND NEW.blocked=0,NEW.slug,
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.title,''),
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.subtitle,''),
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.description,''));
    END IF;
END//
CREATE TRIGGER learntrail_series_search_changed AFTER UPDATE ON learntrail_series FOR EACH ROW
BEGIN
    IF NEW.public_revision<>OLD.public_revision
        AND ((NEW.status='published' AND NEW.blocked=0) OR (OLD.status='published' AND OLD.blocked=0)) THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('series',NEW.id,NEW.public_revision,NEW.status='published' AND NEW.blocked=0,NEW.slug,
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.title,''),
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.subtitle,''),
                    IF(NEW.status='published' AND NEW.blocked=0,NEW.description,''));
    END IF;
END//
CREATE TRIGGER learntrail_series_search_removed AFTER DELETE ON learntrail_series FOR EACH ROW
BEGIN
    IF OLD.status='published' AND OLD.blocked=0 THEN
        INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
            VALUES ('series',OLD.id,OLD.public_revision+1,0,OLD.slug,'','','');
    END IF;
END//
DELIMITER ;
