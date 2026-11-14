USE tj_course;

-- 应用前备份 learntrail_creator、learntrail_series。仅调整已核对的公开策展资料。
START TRANSACTION;
UPDATE learntrail_creator SET bio=CASE id
    WHEN 1 THEN '关注工程实践与知识表达，沿着具体问题梳理概念、方法与实现。让每一次学习，都能在实践中找到落点。'
    WHEN 2 THEN '从开发者的视角理解界面、交互与产品，记录代码如何变成清晰、好用的作品。'
    WHEN 3 THEN '围绕前端技术与持续实践，用小步练习组织知识，让每一次尝试都成为可以回访的经验。'
    END
WHERE id IN (1,2,3) AND bio LIKE '%演示%';

UPDATE learntrail_series SET description=REPLACE(description,'演示视频沿用项目已有教学素材。','') WHERE id=101;
UPDATE learntrail_series SET description=REPLACE(description,'示例视频来自现有课程素材。','') WHERE id=102;
UPDATE learntrail_series SET description=REPLACE(description,'示例素材沿用项目已有教学视频。','') WHERE id=103;
UPDATE learntrail_series SET description=REPLACE(description,'这是使用已有教学素材整理的免费演示专栏。','沿着目录学习类型约束、通配符与泛型方法，并结合代码理解它们的使用方式。') WHERE id=104;

-- 仅下架此前由本项目验收流程创建的公开专栏，已有读者权益和记录保留。
UPDATE learntrail_series SET status='unlisted'
WHERE id=2119293711764750338 AND title='视频专栏验收 mw7d4v0c' AND status='published';
COMMIT;

-- 回退仅恢复备份中本次涉及的行；不要覆盖后续用户内容或删除学习记录。
