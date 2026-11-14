package com.learntrail.learning.mapper;

import com.learntrail.learning.domain.vo.StudioPointsView;
import com.learntrail.api.dto.points.PointsTrade;
import java.time.LocalDateTime;
import org.apache.ibatis.annotations.*;
import java.util.List;

public interface StudioPointsMapper {
    String ENTRY_FILTER = " WHERE user_id=#{user}<if test='kind != null'> AND kind=#{kind}</if>" +
            "<if test='direction == &quot;income&quot;'> AND amount&gt;0</if>" +
            "<if test='direction == &quot;expense&quot;'> AND amount&lt;=0</if>";
    @Select("<script>SELECT COUNT(*) FROM learntrail_point_entry" + ENTRY_FILTER + "</script>")
    long countFilteredEntries(@Param("user") Long user, @Param("kind") String kind, @Param("direction") String direction);
    @Select("<script>SELECT kind,target_id,title,amount,created_at FROM learntrail_point_entry" + ENTRY_FILTER +
            " ORDER BY created_at DESC,kind,target_id DESC LIMIT #{size} OFFSET #{offset}</script>")
    List<StudioPointsView.Entry> filteredEntries(@Param("user") Long user, @Param("kind") String kind,
            @Param("direction") String direction, @Param("offset") int offset, @Param("size") int size);
    @Select("SELECT * FROM learntrail_point_rule WHERE kind=#{kind}")
    PointsTrade.Rule rule(String kind);

    @Select("SELECT COALESCE(SUM(amount),0) FROM learntrail_point_entry WHERE user_id=#{user} AND kind=#{kind} " +
            "AND amount>0 AND created_at>=#{since}")
    int earnedToday(@Param("user") Long user, @Param("kind") String kind, @Param("since") LocalDateTime since);

    @Select("SELECT COUNT(*) FROM learntrail_learning_activity WHERE user_id=#{user} AND kind='READ' " +
            "AND target_id=#{article} AND credited_seconds>=#{seconds} AND last_position>=0.8")
    int effectiveRead(@Param("user") Long user, @Param("article") Long article, @Param("seconds") int seconds);

    @Insert("INSERT IGNORE INTO learntrail_point_comment(user_id,fingerprint,reply_id) VALUES(#{user},#{fingerprint},#{reply})")
    int rememberComment(@Param("user") Long user, @Param("fingerprint") String fingerprint, @Param("reply") Long reply);
    @Select("SELECT course_id FROM learning_lesson WHERE user_id=#{user} AND status != 3 " +
            "AND (expire_time IS NULL OR expire_time > NOW()) ORDER BY create_time DESC,id DESC")
    List<Long> entitlements(Long user);

    @Insert("INSERT INTO learntrail_point_wallet(user_id,balance) VALUES(#{user},#{initial}) " +
            "ON DUPLICATE KEY UPDATE user_id=user_id")
    void createWallet(@Param("user") Long user, @Param("initial") int initial);

    @Select("SELECT balance FROM learntrail_point_wallet WHERE user_id=#{user} FOR UPDATE")
    Integer lockBalance(Long user);

    @Update("UPDATE learntrail_point_wallet SET balance=balance+#{amount} WHERE user_id=#{user} " +
            "AND balance+#{amount} >= 0")
    int changeBalance(@Param("user") Long user, @Param("amount") int amount);

    @Select("SELECT COUNT(*) FROM learntrail_point_entry WHERE user_id=#{user} " +
            "AND kind=#{kind} AND target_id=#{target}")
    int hasEntry(@Param("user") Long user, @Param("kind") String kind, @Param("target") Long target);

    @Insert("INSERT INTO learntrail_point_entry(user_id,kind,target_id,title,amount) " +
            "VALUES(#{user},#{kind},#{target},#{title},#{amount})")
    void addEntry(@Param("user") Long user, @Param("kind") String kind, @Param("target") Long target,
                  @Param("title") String title, @Param("amount") int amount);

    @Select("SELECT kind,target_id,title,amount,created_at FROM learntrail_point_entry " +
            "WHERE user_id=#{user} ORDER BY created_at DESC,kind,target_id DESC LIMIT 50")
    List<StudioPointsView.Entry> entries(Long user);

    @Select("SELECT COUNT(*) FROM learntrail_point_entry WHERE user_id=#{user}")
    long countEntries(Long user);

    @Select("SELECT kind,target_id,title,amount,created_at FROM learntrail_point_entry " +
            "WHERE user_id=#{user} ORDER BY created_at DESC,kind,target_id DESC LIMIT #{size} OFFSET #{offset}")
    List<StudioPointsView.Entry> entriesPage(@Param("user") Long user, @Param("offset") int offset, @Param("size") int size);

    @Select("SELECT a.id,a.slug,a.title,a.summary,a.source_type,EXISTS(SELECT 1 FROM learntrail_point_entry e " +
            "WHERE e.user_id=#{user} AND e.kind='READ' AND e.target_id=a.id) AS rewarded " +
            "FROM learntrail_article a WHERE a.status='published' ORDER BY a.id")
    List<StudioPointsView.Article> articles(Long user);

    @Select("SELECT a.*,EXISTS(SELECT 1 FROM learntrail_point_entry e WHERE e.user_id=#{user} " +
            "AND e.kind='READ' AND e.target_id=a.id) AS rewarded FROM learntrail_article a WHERE a.slug=#{slug} AND a.status='published'")
    StudioPointsView.Article article(@Param("user") Long user, @Param("slug") String slug);

    @Select("SELECT id,slug,title,source_type FROM learntrail_article WHERE id=#{id} AND status='published'")
    StudioPointsView.Article articleById(@Param("user") Long user, @Param("id") Long id);

    @Select("SELECT id FROM learning_lesson WHERE user_id=#{user} AND course_id=#{course} " +
            "AND status != 3 AND (expire_time IS NULL OR expire_time > NOW())")
    Long validLesson(@Param("user") Long user, @Param("course") Long course);

    @Insert("INSERT INTO learning_lesson(id,user_id,course_id,status) VALUES(#{id},#{user},#{course},0) " +
            "ON DUPLICATE KEY UPDATE status=IF(status=3,0,status),expire_time=NULL")
    void grantLesson(@Param("id") Long id, @Param("user") Long user, @Param("course") Long course);
}
