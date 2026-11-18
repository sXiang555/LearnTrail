-- Public projections use a durable revision to make late cache rebuilds unreachable.
USE tj_learning;
ALTER TABLE learntrail_article ADD COLUMN public_revision BIGINT NOT NULL DEFAULT 1;
CREATE TRIGGER learntrail_article_public_revision BEFORE UPDATE ON learntrail_article
FOR EACH ROW SET NEW.public_revision = IF(
    NOT (NEW.status <=> OLD.status AND NEW.slug <=> OLD.slug AND BINARY NEW.title <=> BINARY OLD.title
         AND BINARY NEW.summary <=> BINARY OLD.summary AND BINARY NEW.body <=> BINARY OLD.body
         AND NEW.content_format <=> OLD.content_format AND NEW.source_type <=> OLD.source_type
         AND BINARY NEW.source_author <=> BINARY OLD.source_author AND BINARY NEW.source_url <=> BINARY OLD.source_url
         AND BINARY NEW.license_note <=> BINARY OLD.license_note AND NEW.author_user_id <=> OLD.author_user_id
         AND NEW.published_at <=> OLD.published_at),
    OLD.public_revision + 1, OLD.public_revision);

USE tj_course;
ALTER TABLE learntrail_series ADD COLUMN public_revision BIGINT NOT NULL DEFAULT 1;
CREATE TRIGGER learntrail_series_public_revision BEFORE UPDATE ON learntrail_series
FOR EACH ROW SET NEW.public_revision = IF(
    NOT (NEW.status <=> OLD.status AND NEW.blocked <=> OLD.blocked AND NEW.slug <=> OLD.slug
         AND BINARY NEW.title <=> BINARY OLD.title AND BINARY NEW.subtitle <=> BINARY OLD.subtitle
         AND BINARY NEW.description <=> BINARY OLD.description AND BINARY NEW.topic <=> BINARY OLD.topic
         AND NEW.cover_tone <=> OLD.cover_tone AND NEW.unlock_points <=> OLD.unlock_points
         AND NEW.preview_section_id <=> OLD.preview_section_id AND NEW.preview_minutes <=> OLD.preview_minutes),
    OLD.public_revision + 1, OLD.public_revision);
