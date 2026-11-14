package com.learntrail.learning.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.learntrail.learning.domain.po.StudioArticle;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;
import com.learntrail.api.dto.remark.ContentStatsDTO;
import java.util.List;

public interface StudioArticleMapper extends BaseMapper<StudioArticle> {
    @Update("UPDATE learntrail_article SET liked_times=#{likedTimes},view_times=#{viewTimes},stat_version=#{version},updated_at=updated_at " +
            "WHERE id=#{bizId} AND stat_version<#{version}")
    int updateContentStats(ContentStatsDTO snapshot);

    @Select("SELECT author_user_id,COUNT(*) AS article_count FROM learntrail_article " +
            "WHERE status='published' AND author_user_id IS NOT NULL GROUP BY author_user_id")
    List<StudioArticle.AuthorCount> publishedCounts();

    @Select("SELECT * FROM learntrail_article WHERE id=#{id} FOR UPDATE")
    StudioArticle lock(Long id);

    @Select("<script>SELECT target_id FROM learntrail_point_entry WHERE user_id=#{user} AND kind='READ' " +
            "AND target_id IN <foreach collection='ids' item='id' open='(' separator=',' close=')'>#{id}</foreach></script>")
    List<Long> rewarded(@Param("user") Long user, @Param("ids") List<Long> ids);
}
