-- 为已有 learning 问答服务补齐运行表。保留已有数据；应用前检查同名表结构。
USE tj_learning;
CREATE TABLE IF NOT EXISTS interaction_question (
    id BIGINT NOT NULL PRIMARY KEY,
    title VARCHAR(254) NOT NULL,
    description TEXT NOT NULL,
    course_id BIGINT NOT NULL,
    chapter_id BIGINT NULL,
    section_id BIGINT NULL,
    user_id BIGINT NOT NULL,
    latest_answer_id BIGINT NULL,
    answer_times INT NOT NULL DEFAULT 0,
    anonymity TINYINT NOT NULL DEFAULT 0,
    hidden TINYINT NOT NULL DEFAULT 0,
    status TINYINT NOT NULL DEFAULT 0,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_question_course (course_id,hidden,id),
    INDEX idx_question_section (section_id,hidden,id),
    INDEX idx_question_user (user_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS interaction_reply (
    id BIGINT NOT NULL PRIMARY KEY,
    question_id BIGINT NOT NULL,
    answer_id BIGINT NULL,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    target_user_id BIGINT NULL,
    target_reply_id BIGINT NULL,
    reply_times INT NOT NULL DEFAULT 0,
    liked_times INT NOT NULL DEFAULT 0,
    hidden TINYINT NOT NULL DEFAULT 0,
    anonymity TINYINT NOT NULL DEFAULT 0,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_reply_question (question_id,hidden,answer_id,id),
    INDEX idx_reply_answer (answer_id,hidden,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- 回滚应用时保留表和讨论记录；不自动 DROP 或删除用户内容。
