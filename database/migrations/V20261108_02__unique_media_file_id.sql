-- The upload response and the VOD event can arrive in either order.
USE tj_media;
ALTER TABLE media ADD UNIQUE KEY uk_media_file_id (file_id);
