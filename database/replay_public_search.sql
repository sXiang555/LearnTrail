-- Run after V20261118_12 and after the Canal destination and RabbitMQ consumer are ready.
-- Replays current public snapshots and the most recent removal record for each hidden or removed item.
USE tj_learning;
INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
SELECT 'article',id,public_revision,1,slug,title,summary,body FROM learntrail_article WHERE status='published';
INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
SELECT 'article',o.content_id,o.revision,0,o.slug,'','',''
FROM (
    SELECT content_id,revision,visible,slug,
           ROW_NUMBER() OVER (PARTITION BY content_id ORDER BY revision DESC,event_id DESC) AS latest
    FROM learntrail_search_outbox WHERE content_kind='article'
) o
LEFT JOIN learntrail_article a ON a.id=o.content_id AND a.status='published'
WHERE o.latest=1 AND o.visible=0 AND a.id IS NULL;

USE tj_course;
INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
SELECT 'series',id,public_revision,1,slug,title,subtitle,description
FROM learntrail_series WHERE status='published' AND blocked=0;
INSERT INTO learntrail_search_outbox(content_kind,content_id,revision,visible,slug,title,summary,body)
SELECT 'series',o.content_id,o.revision,0,o.slug,'','',''
FROM (
    SELECT content_id,revision,visible,slug,
           ROW_NUMBER() OVER (PARTITION BY content_id ORDER BY revision DESC,event_id DESC) AS latest
    FROM learntrail_search_outbox WHERE content_kind='series'
) o
LEFT JOIN learntrail_series s ON s.id=o.content_id AND s.status='published' AND s.blocked=0
WHERE o.latest=1 AND o.visible=0 AND s.id IS NULL;
