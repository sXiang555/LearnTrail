-- LearnTrail accounts can register with a username and bind a phone separately.
USE tj_user;
ALTER TABLE `user` MODIFY COLUMN cell_phone VARCHAR(11) NULL DEFAULT NULL COMMENT '手机号';
