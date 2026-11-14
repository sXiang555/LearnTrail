package com.learntrail.course.mapper;

import com.learntrail.course.domain.vo.StudioView;
import com.learntrail.course.domain.query.StudioQuery;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Update;
import com.learntrail.api.dto.remark.ContentStatsDTO;
import java.util.List;
import java.util.Map;

@Mapper
public interface StudioMapper {
    @Update("UPDATE learntrail_series SET liked_times=#{likedTimes},view_times=#{viewTimes},stat_version=#{version},updated_at=updated_at " +
            "WHERE id=#{bizId} AND stat_version<#{version}")
    int updateContentStats(ContentStatsDTO snapshot);

    @Select("SELECT article_id FROM learntrail_series_article WHERE series_id=#{id} AND version=#{version} ORDER BY position,article_id")
    List<Long> articleIds(@Param("id") Long id, @Param("version") String version);

    @Delete("DELETE FROM learntrail_series_article WHERE series_id=#{id} AND version=#{version}")
    void clearArticles(@Param("id") Long id, @Param("version") String version);

    @Insert("INSERT INTO learntrail_series_article(series_id,article_id,version,position) VALUES(#{id},#{article},'draft',#{position})")
    void addArticle(@Param("id") Long id, @Param("article") Long article, @Param("position") int position);

    @Insert("INSERT INTO learntrail_series_article(series_id,article_id,version,position) " +
            "SELECT series_id,article_id,#{to},position FROM learntrail_series_article WHERE series_id=#{id} AND version=#{from}")
    void copyArticles(@Param("id") Long id, @Param("from") String from, @Param("to") String to);

    @Select("SELECT s.id,s.course_id,s.slug,s.title,s.subtitle,s.cover_tone,s.topic FROM learntrail_series_article a " +
            "JOIN learntrail_series s ON s.id=a.series_id JOIN course c ON c.id=s.course_id " +
            "WHERE a.article_id=#{article} AND a.version='published' AND s.status='published' " +
            "AND s.blocked=0 AND c.status=2 AND c.deleted=0 ORDER BY s.id DESC")
    List<StudioView.Series> relatedSeries(Long article);
    long countPage(@Param("query") StudioQuery query, @Param("userId") Long userId, @Param("courseIds") List<Long> courseIds);
    List<StudioView.Series> seriesPage(@Param("query") StudioQuery query, @Param("userId") Long userId, @Param("courseIds") List<Long> courseIds);
    long countCreators(@Param("q") String q, @Param("userId") Long userId, @Param("articleCounts") Map<Long, Long> articleCounts);
    List<StudioView.Creator> creatorsPage(@Param("q") String q, @Param("userId") Long userId,
            @Param("offset") int offset, @Param("size") int size, @Param("articleCounts") Map<Long, Long> articleCounts);
    List<String> topics();
    StudioView.Series discussionPolicy(@Param("courseId") Long courseId, @Param("userId") Long userId);
    List<StudioView.Series> series(@Param("q") String q, @Param("topic") String topic,
            @Param("creator") String creator, @Param("saved") boolean saved,
            @Param("userId") Long userId, @Param("includeDraft") boolean includeDraft);
    StudioView.Series find(@Param("id") Long id, @Param("slug") String slug, @Param("userId") Long userId);
    Integer findUnlockPoints(@Param("courseId") Long courseId);
    List<StudioView.Creator> creators(@Param("userId") Long userId);
    StudioView.Creator creator(@Param("slug") String slug, @Param("userId") Long userId);
    StudioView.Creator owner(@Param("userId") Long userId);
    List<StudioView.Follower> followers(@Param("creatorId") Long creatorId, @Param("offset") int offset, @Param("size") int size);
    int insertCreator(@Param("id") Long id, @Param("userId") Long userId,
            @Param("initials") String initials, @Param("profile") StudioView.CreatorEdit profile);
    int insertDraft(@Param("id") Long id, @Param("courseId") Long courseId,
            @Param("creatorId") Long creatorId, @Param("edit") StudioView.SeriesCreate edit,
            @Param("unlockPoints") int unlockPoints);
    List<StudioView.Series> drafts(@Param("userId") Long userId);
    List<StudioView.Series> workspaceSeries(@Param("userId") Long userId);
    List<StudioView.Series> ownedSeries(@Param("userId") Long userId, @Param("courseIds") List<Long> courseIds);
    StudioView.Series lockSeries(@Param("id") Long id, @Param("userId") Long userId);
    List<StudioView.DraftEpisode> draftEpisodes(@Param("courseId") Long courseId);
    int updatePreview(@Param("id") Long id, @Param("sectionId") Long sectionId, @Param("minutes") int minutes);
    int publishDraft(@Param("id") Long id, @Param("edit") StudioView.SeriesEdit edit, @Param("unlockPoints") int unlockPoints);
    int updateDraftPayload(@Param("id") Long id, @Param("userId") Long userId, @Param("payload") String payload);
    int beginRevision(@Param("id") Long id, @Param("userId") Long userId, @Param("payload") String payload);
    int changeState(@Param("id") Long id, @Param("userId") Long userId, @Param("state") String state);
    int setBlocked(@Param("id") Long id, @Param("blocked") boolean blocked);
    Long draftChapter(@Param("courseId") Long courseId);
    List<Long> boundMediaIds(@Param("courseId") Long courseId, @Param("userId") Long userId);
    List<StudioView.Episode> episodes(@Param("courseId") Long courseId);
    StudioView.Series accessPolicy(@Param("sectionId") Long sectionId, @Param("userId") Long userId);
    int save(@Param("userId") Long userId, @Param("seriesId") Long seriesId);
    int unsave(@Param("userId") Long userId, @Param("seriesId") Long seriesId);
    int follow(@Param("userId") Long userId, @Param("creatorId") Long creatorId);
    int unfollow(@Param("userId") Long userId, @Param("creatorId") Long creatorId);
    int updateSeries(@Param("id") Long id, @Param("userId") Long userId, @Param("edit") StudioView.SeriesEdit edit,
            @Param("unlockPoints") Integer unlockPoints);
    int updateCreator(@Param("userId") Long userId, @Param("edit") StudioView.CreatorEdit edit);
}
