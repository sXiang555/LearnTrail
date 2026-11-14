package com.learntrail.course.domain.vo;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.learntrail.api.dto.learning.StudioArticleLinkDTO;
import lombok.Data;
import lombok.EqualsAndHashCode;
import javax.validation.Valid;
import javax.validation.constraints.Max;
import javax.validation.constraints.Min;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.Pattern;
import javax.validation.constraints.Positive;
import javax.validation.constraints.PositiveOrZero;
import javax.validation.constraints.Size;
import java.util.ArrayList;
import java.util.List;
import java.time.LocalDateTime;

/** Public creator-platform contracts, separate from the legacy course administration views. */
public final class StudioView {
    private StudioView() { }

    @Data
    public static class Series {
        private Long id;
        private Long courseId;
        private Long creatorId;
        private String slug;
        private String title;
        private String subtitle;
        private String description;
        private String topic;
        private String coverTone;
        private String status;
        private boolean available;
        private boolean blocked;
        private boolean previouslyPublished;
        private boolean creatorOwned;
        @JsonIgnore
        private String draftPayload;
        private String creatorName;
        private String creatorSlug;
        private String creatorInitials;
        private String creatorColor;
        private Integer price;
        private Integer unlockPoints;
        private Integer free;
        private Long categoryId;
        private Integer featured;
        private Integer episodeCount;
        private Integer duration;
        private Long likedTimes = 0L;
        private Long viewTimes = 0L;
        private Integer previewMinutes;
        private Long previewSectionId;
        private boolean saved;
        private boolean owned;
        private Long lessonId;
        private Long latestSectionId;
        private int progressPercent;
        private List<Episode> episodes = new ArrayList<>();
        private List<Long> articleIds = new ArrayList<>();
        private List<StudioArticleLinkDTO> articles = new ArrayList<>();
    }

    @Data
    public static class Creator {
        private Long id;
        @JsonIgnore
        private Long ownerUserId;
        private String slug;
        private String displayName;
        private String headline;
        private String bio;
        private String initials;
        private String avatarColor;
        private Integer seriesCount;
        private Integer followerCount;
        private Long articleCount = 0L;
        private Integer rank;
        private boolean following;

        public Long getArticleAuthorId() { return ownerUserId; }
        public long getContentCount() { return (seriesCount == null ? 0 : seriesCount) + articleCount; }
    }

    @Data
    public static class Follower {
        private Long userId;
        private String name;
        private String icon;
        private LocalDateTime followedAt;
    }

    @Data
    public static class Episode {
        private Long id;
        private String title;
        private Integer duration;
        private Integer position;
        private boolean preview;
        private boolean finished;
        private Integer moment;
    }

    @Data
    public static class SeriesEdit {
        @NotBlank @Size(max = 100)
        private String title;
        @NotBlank @Size(max = 200)
        private String subtitle;
        @NotBlank @Size(max = 10000)
        private String description;
        @NotBlank @Size(max = 40)
        private String topic;
        @Pattern(regexp = "forest|clay|violet|mint") @NotBlank
        private String coverTone;
    }

    @Data
    @EqualsAndHashCode(callSuper = true)
    public static class SeriesCreate extends SeriesEdit {
        @Size(max = 20)
        private List<@NotNull @Positive Long> articleIds;

        /** 售价，单位为分。0 表示免费。 */
        @PositiveOrZero(message = "专栏售价不能为负数")
        private Integer price;

        @PositiveOrZero(message = "解锁积分不能为负数")
        @Max(value = 100000, message = "解锁积分最多为 100000")
        private Integer unlockPoints;

        @NotNull(message = "请选择内容分类")
        @Positive
        private Long categoryId;
    }

    @Data
    public static class DraftEpisode {
        private Long id;
        private Long mediaId;
        private String title;
        private String filename;
        private Integer duration;
    }

    @Data
    public static class EpisodeEdit {
        @NotNull @Positive
        private Long mediaId;
        @NotBlank @Size(max = 100)
        private String title;
    }

    @Data
    public static class EpisodesEdit {
        @NotNull @Size(max = 30)
        private List<@NotNull @Valid EpisodeEdit> episodes;
        @Positive
        private Long previewMediaId;
        @NotNull @Min(1) @Max(3)
        private Integer previewMinutes = 3;
    }

    @Data
    public static class CreatorEdit {
        @NotBlank @Size(max = 80)
        private String displayName;
        @NotBlank @Size(max = 160)
        private String headline;
        @NotBlank @Size(max = 3000)
        private String bio;
    }
}
